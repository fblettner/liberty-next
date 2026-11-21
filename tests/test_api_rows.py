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
    # the screen's order and labels, then the dictionary's; a filter only where the endpoint has the parameter
    assert [(c.name, c.label, c.filter) for c in meta.columns] == [("ALPH", "Name", True), ("AN8", "Address", False), ("AT1", "Search type", True)]


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
