"""An endpoint a screen can read: its rows served as a query's (liberty/connectors/api_rows.py),
with an answer shaped like a JD Edwards form's — rows under a path, keys that are not column
names, the columns' titles and a "more records" flag beside them."""

from __future__ import annotations

import json

import httpx
import pytest

from liberty.auth.scope import DataScope
from liberty.connectors.api import APIConnector
from liberty.connectors.api_rows import ApiRowsError, api_row_events, endpoint_params, rows_endpoint
from liberty.connectors.config import ApiConnectorConfig, ColumnHint, EndpointDef, ParamDef
from liberty.connectors.dictionary import DictionaryEntry, DictionaryFile
from liberty.connectors.sql import StreamDone, StreamMeta, StreamRows

ANSWER = {"fs_F": {"data": {"gridData": {
    "columns": {"z_AN8_19": "Address Number", "z_ALPH_20": "Alpha Name", "z_AT1_50": "Sch Typ"},
    "rowset": [{"z_AN8_19": 1001, "z_ALPH_20": "AB Common", "z_AT1_50": "E"},
               {"z_AN8_19": 1234, "z_ALPH_20": "Long, Ben", "z_AT1_50": "E"},
               {"z_AN8_19": 4242, "z_ALPH_20": "Capital System", "z_AT1_50": "C"}],
    "summary": {"records": 3, "moreRecords": True}}}, "errors": []}}

CFG = ApiConnectorConfig(type="api", base_url="https://erp.test/rest", endpoints=[
    EndpointDef(name="addresses", method="POST", path="/form", body='{"type": "{{AT1}}", "name": "{{ALPH}}", "max": "{{MAX}}"}',
                params=[ParamDef(name="AT1"), ParamDef(name="ALPH"), ParamDef(name="MAX", default="100")],
                rows_field="fs_F.data.gridData.rowset", row_map={"AN8": "z_AN8_19", "ALPH": "z_ALPH_20", "AT1": "z_AT1_50"},
                labels_field="fs_F.data.gridData.columns", more_field="fs_F.data.gridData.summary.moreRecords", limit_param="max",
                error_fields=["fs_F.errors"]),
    EndpointDef(name="plain", path="/list", rows_field="items"),
    EndpointDef(name="no_rows", path="/thing"),
])


def _conn(sent: list, answers: dict | None = None) -> APIConnector:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.url.path, json.loads(request.content or b"null")))
        return httpx.Response(200, json=(answers or {}).get(request.url.path, ANSWER))

    return APIConnector("erp", CFG, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def _read(conn, name, params=None, **kw):
    events = [e async for e in api_row_events(conn, name, params, dictionary=kw.pop("dictionary", DictionaryFile()), **kw)]
    meta, done = events[0], events[-1]
    assert isinstance(meta, StreamMeta) and isinstance(done, StreamDone)
    return meta, [r for e in events if isinstance(e, StreamRows) for r in e.rows], done


@pytest.mark.asyncio
async def test_the_rows_of_an_endpoint_come_as_a_querys() -> None:
    sent: list = []
    conn = _conn(sent)
    meta, rows, done = await _read(conn, "addresses", {"at1": "E", "ALPH_op": "contains", "UNKNOWN": "x", "_screen": "s"}, chunk_size=2)
    # the columns are named by the map (JDE's aliases), titled by the server when nothing else names them
    assert [(c.name, c.label) for c in meta.columns] == [("AN8", "Address Number"), ("ALPH", "Alpha Name"), ("AT1", "Sch Typ")]
    assert rows == [{"AN8": 1001, "ALPH": "AB Common", "AT1": "E"}, {"AN8": 1234, "ALPH": "Long, Ben", "AT1": "E"},
                    {"AN8": 4242, "ALPH": "Capital System", "AT1": "C"}]
    assert done.total == 3 and done.truncated is True          # the server says there is more
    # only the endpoint's own parameters are sent, whatever the case; its defaults apply
    assert sent == [("/rest/form", {"type": "E", "name": "", "max": "100"})]
    assert endpoint_params(CFG.endpoints[0], {"alph": "A*", "x": 1, "MAX": ""}) == {"ALPH": "A*"}
    # a value a lookup offers as the database stores it (blank-padded) is sent without its padding
    assert endpoint_params(CFG.endpoints[0], {"AT1": "         E", "ALPH": "   "}) == {"AT1": "E"}
    # the cap is applied again here
    _meta, capped, done = await _read(conn, "addresses", max_rows=2)
    assert len(capped) == 2 and done.truncated is True
    # … and asked from the server, through the parameter the endpoint names (limit_param)
    assert sent[-1][1]["max"] == "2"
    await _read(conn, "addresses", {"MAX": "7"}, screen_max_rows=500)
    assert sent[-1][1]["max"] == "500"                         # the screen's limit, over a value sent
    # a limit of 0: the columns only (what the screen editor asks to list them)
    meta, none, _done = await _read(conn, "addresses", max_rows=0)
    assert none == [] and [c.name for c in meta.columns] == ["AN8", "ALPH", "AT1"]
    assert sent[-1][1]["max"] == "1"


@pytest.mark.asyncio
async def test_the_screens_columns_the_dictionary_and_the_filters_the_endpoint_has() -> None:
    conn = _conn([])
    dictionary = DictionaryFile(entries={"AN8": DictionaryEntry(label="Address"), "AT1": DictionaryEntry(label="Search type")})
    hints = [ColumnHint(name="ALPH", label="Name", filter=True), ColumnHint(name="AN8", filter=True), ColumnHint(name="AT1", filter=True)]
    meta, _rows, _done = await _read(conn, "addresses", column_hints=hints, dictionary=dictionary)
    # the screen's order and labels, then the dictionary's; every column the screen flags can be filtered —
    # by the server when it is a parameter of the endpoint (ALPH, AT1), on the rows returned otherwise (AN8)
    assert [(c.name, c.label, c.filter) for c in meta.columns] == [("ALPH", "Name", True), ("AN8", "Address", True), ("AT1", "Search type", True)]


@pytest.mark.asyncio
async def test_a_filter_the_endpoint_does_not_take_is_applied_to_its_rows() -> None:
    """What the SQL connector does with a WHERE: the value, its operator, and the bounds."""
    sent: list = []
    conn = _conn(sent)
    hints = [ColumnHint(name="ALPH", filter=True), ColumnHint(name="AN8", filter=True), ColumnHint(name="AT1", filter=True)]

    async def names(**params):
        return [r["ALPH"] for r in (await _read(conn, "addresses", params, column_hints=hints))[1]]

    assert await names() == ["AB Common", "Long, Ben", "Capital System"]
    assert await names(AN8="12") == ["Long, Ben"]                               # contains, the default
    assert await names(AN8="1001", AN8_op="equals") == ["AB Common"]            # a number as a number ("1001" = 1001)
    assert await names(AN8="1001", AN8_op="notEquals") == ["Long, Ben", "Capital System"]
    assert await names(AN8="42", AN8_op="startsWith") == ["Capital System"] and await names(AN8="34", AN8_op="endsWith") == ["Long, Ben"]
    assert await names(AN8_from="1200", AN8_to="5000") == ["Long, Ben", "Capital System"]
    # a column that IS a parameter is the server's to filter: sent, not applied again here
    sent.clear()
    assert await names(AT1="C") == ["AB Common", "Long, Ben", "Capital System"] and sent[-1][1]["type"] == "C"
    # an unflagged column is not a filter
    assert len((await _read(conn, "addresses", {"AN8": "12"}, column_hints=[ColumnHint(name="AN8")]))[1]) == 3


@pytest.mark.asyncio
async def test_the_callers_data_scope_applies_to_the_rows_returned() -> None:
    conn = _conn([])
    only_c = DataScope.from_claims([[{"column": "AT1", "values": ["C"], "mode": "deny"}]])
    _meta, rows, done = await _read(conn, "addresses", scope=only_c)
    assert rows == [{"AN8": 4242, "ALPH": "Capital System", "AT1": "C"}] and done.total == 1
    # a rule on a column the rows do not carry: its own mode decides, as for a query
    elsewhere = DataScope.from_claims([[{"column": "CO", "values": ["00001"], "mode": "deny"}]])
    assert (await _read(conn, "addresses", scope=elsewhere))[1] == []
    ignored = DataScope.from_claims([[{"column": "CO", "values": ["00001"], "mode": "ignore"}]])
    assert len((await _read(conn, "addresses", scope=ignored))[1]) == 3


@pytest.mark.asyncio
async def test_what_is_not_readable_and_what_fails() -> None:
    sent: list = []
    conn = _conn(sent, {"/rest/list": {"items": [{"id": 1, "name": "a"}, {"id": 2, "extra": True}]},
                        "/rest/form": {"fs_F": {"errors": [{"TITLE": "Error: Search Type Invalid"}], "data": {}}}})
    assert rows_endpoint(conn, "addresses") is not None and rows_endpoint(conn, "no_rows") is None
    assert rows_endpoint(conn, "nope") is None and rows_endpoint(object(), "addresses") is None
    # without a map the records' own keys are the columns (the union of the first ones)
    meta, rows, done = await _read(conn, "plain")
    assert [c.name for c in meta.columns] == ["id", "name", "extra"] and len(rows) == 2 and done.truncated is False
    # the server's refusal (its error fields) is the read's error
    with pytest.raises(ApiRowsError, match="Search Type Invalid"):
        await _read(conn, "addresses", {"AT1": "ZZ"})
    with pytest.raises(ApiRowsError, match="not an endpoint a screen can read"):
        await _read(conn, "no_rows")


@pytest.mark.asyncio
async def test_a_server_that_refuses_when_it_has_nothing_and_rows_that_are_not_where_the_endpoint_says() -> None:
    """Seen on JD Edwards' Server Manager: "no active users" comes as an HTTP 500 with a message,
    and the instances are under ``result`` where the documentation says ``instancesInfo``."""
    cfg = ApiConnectorConfig(type="api", base_url="https://sm.test/rest", endpoints=[
        EndpointDef(name="users", path="/useractivity", rows_field="userActivityDetail", error_fields=["errorMessage"],
                    empty_on=["no active users"]),
        EndpointDef(name="strict", path="/useractivity", rows_field="userActivityDetail", error_fields=["errorMessage"]),
        EndpointDef(name="missing", path="/missing", rows_field="items", empty_on=["404"]),
        EndpointDef(name="instances", path="/instancesinfo", rows_field="instancesInfo"),
        EndpointDef(name="deep", path="/instancesinfo", rows_field="result.0.nodes"),
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/useractivity"):
            return httpx.Response(500, json={"errorCode": 27, "errorMessage": "There are no active users that match the supplied criteria."})
        if request.url.path.endswith("/missing"):
            return httpx.Response(404, text="Not Found")
        return httpx.Response(200, json={"result": [{"instanceName": "EntServer"}]})

    conn = APIConnector("sm", cfg, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    # the refusal that means "nothing to list": an empty list — with the screen's own columns, so its grid
    # (and the toolbar that reads again) is drawn; without a screen, no column
    meta, rows, done = await _read(conn, "users", column_hints=[ColumnHint(name="userName", label="User")])
    assert rows == [] and done.total == 0 and [(c.name, c.label) for c in meta.columns] == [("userName", "User")]
    assert (await _read(conn, "users"))[0].columns == []
    assert (await _read(conn, "missing"))[1] == []                                   # … by its HTTP status
    # not declared: an error — with the server's own words (its error field), not a bare "HTTP 500"
    with pytest.raises(ApiRowsError, match="HTTP 500 — There are no active users"):
        await _read(conn, "strict")
    assert (await conn.call("strict")).error == "HTTP 500 — There are no active users that match the supplied criteria."
    # a list that is not where the endpoint says it is: said, with what the answer holds — not an empty screen
    with pytest.raises(ApiRowsError, match="the answer has no 'instancesInfo' — it holds: result"):
        await _read(conn, "instances")
    # … but a path that starts right and has nothing further is just empty
    assert (await _read(conn, "deep"))[1] == []
    await conn.aclose()
