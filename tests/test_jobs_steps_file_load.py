"""``file_load`` — a spreadsheet / CSV into a table on a real SQLite registry: header mapping,
coercion to the target types, append vs replace_partition (constants and file-driven keys, one
transaction), templates, the error paths."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from openpyxl import Workbook
from sqlalchemy import text

from liberty.connectors.config import ConnectorsFile, PoolConfig, QueryDef, SqlConnectorConfig
from liberty.connectors.registry import ConnectorRegistry
from liberty.jobs import FileLoadExecutor, ManualTrigger, RunContext, Step, StepFailed
from liberty.jobs.schema import SqlEndpoint
from liberty.jobs.steps.file_load import coerce_cell, normalize_header, render_template

DDL = """CREATE TABLE fact_sales (
    period TEXT NOT NULL, company TEXT NOT NULL, article TEXT, customer TEXT,
    qty INTEGER, revenue NUMERIC(14, 2), sold_on DATE, batch_id TEXT
)"""


@pytest_asyncio.fixture
async def registry(tmp_path):
    cfg = ConnectorsFile(
        pools={"target": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'target.db'}")},
        connectors={"sales": SqlConnectorConfig(type="sql", pool="target", queries=[
            QueryDef(name="period_closed_get", sql="SELECT 'period ' || :PERIOD || ' is closed' AS reason FROM period_status WHERE period = :PERIOD AND status = 'closed'"),
            QueryDef(name="broken_get", sql="SELECT * FROM no_such_table"),
        ])},
    )
    reg = ConnectorRegistry(cfg)
    async with reg.pools.engine("target").begin() as conn:
        await conn.execute(text(DDL))
        await conn.execute(text("CREATE TABLE period_status (period TEXT, status TEXT)"))
        await conn.execute(text("INSERT INTO period_status VALUES ('2026-02', 'closed'), ('2026-03', 'open')"))
    try:
        yield reg
    finally:
        await reg.aclose()


async def rows(reg: ConnectorRegistry, where: str = "", order: str = "period, company, article") -> list[tuple]:
    async with reg.pools.engine("target").connect() as conn:
        res = await conn.execute(text(f"SELECT period, company, article, qty, revenue, sold_on, batch_id FROM fact_sales {where} ORDER BY {order}"))
        return [tuple(r) for r in res.fetchall()]


def xlsx(path: Path, header: list, data: list[list], *, sheet: str = "Sales", extra_sheet: bool = False) -> Path:
    wb = Workbook()
    if extra_sheet:
        wb.active.title = "Notes"
        wb.active.append(["ignore me"])
        ws = wb.create_sheet(sheet)
    else:
        ws = wb.active
        ws.title = sheet
    ws.append(header)
    for r in data:
        ws.append(r)
    wb.save(path)
    return path


def csvfile(path: Path, header: list, data: list[list], *, delimiter: str = ";") -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(header)
        w.writerows(data)
    return path


def step(**kw) -> Step:
    base = dict(type="file_load", name="load", target=SqlEndpoint(connector="sales", table="fact_sales"))
    return Step(**{**base, **kw})


def ctx(**params) -> RunContext:
    return RunContext(run_id="run-1", job_id="sales-load", trigger=ManualTrigger(triggered_by="tester"))


def with_params(s: Step, **params) -> Step:
    return s.model_copy(update={"op_kwargs": {**s.op_kwargs, **params}})


# ── pure helpers ───────────────────────────────────────────────────────────────────────────

def test_helpers() -> None:
    from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String
    from liberty.jobs.steps.file_load import detect_delimiter
    assert detect_delimiter("article;qty;revenue\nA;1;2,5\n") == ";" and detect_delimiter("a,b\n1,2\n") == "," and detect_delimiter("a\tb\n") == "\t"
    assert detect_delimiter("single\n1\n2\n") == ","
    assert normalize_header(" Sold On ") == "soldon" and normalize_header("sold_on") == "soldon"
    assert render_template("/in/${params.period}-${run.id}-${job.id}.xlsx", params={"period": "2026-03"}, run_id="r1", job_id="j") == "/in/2026-03-r1-j.xlsx"
    # a plain ${run_id} is NOT a placeholder (the config loader would have env-substituted it)
    assert render_template("${run_id}", params={}, run_id="r1", job_id="j") == "${run_id}"
    with pytest.raises(StepFailed, match="parameter 'period' is not set"):
        render_template("${params.period}", params={}, run_id="r", job_id="j")
    assert coerce_cell("12", Integer()) == 12 and coerce_cell(12.0, Integer()) == 12 and coerce_cell("1 234", Integer()) == 1234
    assert coerce_cell("", Integer()) is None and coerce_cell(None, String()) is None
    assert str(coerce_cell("1 234,50", Numeric())) == "1234.50" and str(coerce_cell("1,234.50", Numeric())) == "1234.50"
    assert coerce_cell("2026-03-01", Date()) == date(2026, 3, 1) and coerce_cell("01/03/2026", Date()) == date(2026, 3, 1)
    assert coerce_cell("2026-03-01 10:30", DateTime()).hour == 10
    assert coerce_cell("oui", Boolean()) is True and coerce_cell("N", Boolean()) is False
    assert coerce_cell(50.0, String()) == "50" and coerce_cell(" 00050 ", String()) == "00050"
    for bad, typ in [("12.5", Integer()), ("abc", Numeric()), ("31/31/2026", Date()), ("maybe", Boolean())]:
        with pytest.raises(ValueError):
            coerce_cell(bad, typ)


# ── loads ──────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_append_from_xlsx_with_mapping_and_constants(registry, tmp_path) -> None:
    f = xlsx(tmp_path / "sales.xlsx", ["Item", "Customer", "Quantity", "Revenue", "Sold on"],
             [["A1", "C1", 2, 10.5, date(2026, 3, 2)], ["A2", "C1", 3.0, "20,25", "03/03/2026"], [None, None, None, None, None]],
             extra_sheet=True)
    s = step(path=str(f), sheet="Sales", columns={"article": "Item", "qty": "#3", "revenue": "Revenue"},
             constants={"period": "${params.period}", "company": "00001", "batch_id": "${run.id}"})
    res = await FileLoadExecutor(registry).execute(with_params(s, period="2026-03"), ctx())
    assert res.rows_affected == 2 and res.extras["read"] == 2 and res.extras["rejected"] == 0
    assert "2 inserted" in (res.log_excerpt or "")
    got = await rows(registry)
    assert got == [
        ("2026-03", "00001", "A1", 2, 10.5, "2026-03-02", "run-1"),     # customer / sold_on matched by name
        ("2026-03", "00001", "A2", 3, 20.25, "2026-03-03", "run-1"),
    ]
    # sheet by index, header on row 2
    f2 = xlsx(tmp_path / "s2.xlsx", ["title row"], [], sheet="X")
    from openpyxl import load_workbook
    wb = load_workbook(f2); ws = wb["X"]; ws.append(["period", "company", "article"]); ws.append(["2026-04", "00002", "B"]); wb.save(f2)
    res = await FileLoadExecutor(registry).execute(step(path=str(f2), sheet=1, header_row=2), ctx())
    assert res.rows_affected == 1 and (await rows(registry, "WHERE period = '2026-04'"))[0][2] == "B"


@pytest.mark.asyncio
async def test_replace_partition_by_constants_replays_one_month(registry, tmp_path) -> None:
    ex = FileLoadExecutor(registry)
    f = csvfile(tmp_path / "m.csv", ["article", "qty", "revenue"], [["A", "1", "1,5"], ["B", "2", "2,5"]])
    s = step(path=str(f), constants={"period": "${params.period}", "company": "${params.company}"},
             load_mode="replace_partition", partition_by=["period", "company"])
    await ex.execute(with_params(s, period="2026-03", company="00001"), ctx())
    await ex.execute(with_params(s, period="2026-03", company="00002"), ctx())
    await ex.execute(with_params(s, period="2026-04", company="00001"), ctx())
    assert len(await rows(registry)) == 6
    # the corrected March file for company 00001 replaces only that partition
    csvfile(f, ["article", "qty", "revenue"], [["A", "10", "100"]])
    res = await ex.execute(with_params(s, period="2026-03", company="00001"), ctx())
    assert res.extras["deleted"] == 2 and res.rows_affected == 1 and res.extras["partitions"] == [["2026-03", "00001"]]
    assert "1 partition(s) replaced (2 row(s) deleted)" in (res.log_excerpt or "")
    got = await rows(registry)
    assert len(got) == 5 and [r for r in got if r[:2] == ("2026-03", "00001")] == [("2026-03", "00001", "A", 10, 100.0, None, None)]


@pytest.mark.asyncio
async def test_replace_partition_keys_from_the_file(registry, tmp_path) -> None:
    ex = FileLoadExecutor(registry)
    f = csvfile(tmp_path / "all.csv", ["period", "company", "article", "qty"],
                [["2026-03", "00001", "A", "1"], ["2026-03", "00002", "B", "1"], ["2026-04", "00001", "C", "1"]], delimiter=",")
    s = step(path=str(f), load_mode="replace_partition", partition_by=["period", "company"])
    await ex.execute(s, ctx())
    # a file with two partitions replaces those two and leaves 2026-04 alone
    csvfile(f, ["period", "company", "article", "qty"], [["2026-03", "00001", "A", "5"], ["2026-03", "00002", "B", "6"]], delimiter=",")
    res = await ex.execute(s, ctx())
    assert res.extras["deleted"] == 2 and res.extras["partitions"] == [["2026-03", "00001"], ["2026-03", "00002"]]
    assert [(r[0], r[1], r[3]) for r in await rows(registry)] == [("2026-03", "00001", 5), ("2026-03", "00002", 6), ("2026-04", "00001", 1)]


@pytest.mark.asyncio
async def test_bad_cell_fails_or_skips_and_the_transaction_holds(registry, tmp_path) -> None:
    ex = FileLoadExecutor(registry)
    good = csvfile(tmp_path / "g.csv", ["period", "company", "article", "qty"], [["2026-03", "00001", "A", "1"], ["2026-03", "00001", "B", "2"]])
    s = step(path=str(good), load_mode="replace_partition", partition_by=["period", "company"])
    await ex.execute(s, ctx())
    bad = csvfile(tmp_path / "b.csv", ["period", "company", "article", "qty"], [["2026-03", "00001", "A", "9"], ["2026-03", "00001", "B", "two"]])
    with pytest.raises(StepFailed, match="row 3: qty: not a whole number: 'two'"):
        await ex.execute(step(path=str(bad), load_mode="replace_partition", partition_by=["period", "company"]), ctx())
    assert [r[3] for r in await rows(registry)] == [1, 2]          # nothing deleted, nothing inserted
    res = await ex.execute(step(path=str(bad), load_mode="replace_partition", partition_by=["period", "company"], on_error="skip"), ctx())
    assert res.rows_affected == 1 and res.extras["rejected"] == 1 and res.extras["rejects"] == ["b.csv row 3: qty: not a whole number: 'two'"]
    assert [r[3] for r in await rows(registry)] == [9]


@pytest.mark.asyncio
async def test_failures_name_the_cause(registry, tmp_path) -> None:
    ex = FileLoadExecutor(registry)
    f = csvfile(tmp_path / "f.csv", ["period", "company", "article"], [["2026-03", "00001", "A"]])
    with pytest.raises(StepFailed, match="file not found"):
        await ex.execute(step(path=str(tmp_path / "nope.csv")), ctx())
    with pytest.raises(StepFailed, match="header 'Item' not found"):
        await ex.execute(step(path=str(f), columns={"article": "Item"}), ctx())
    with pytest.raises(StepFailed, match="column 'nope' is not in the target table"):
        await ex.execute(step(path=str(f), columns={"nope": "article"}), ctx())
    with pytest.raises(StepFailed, match="position #9 is outside the header"):
        await ex.execute(step(path=str(f), columns={"article": "#9"}), ctx())
    with pytest.raises(StepFailed, match="partition column 'region' is not in the target table"):
        await ex.execute(step(path=str(f), load_mode="replace_partition", partition_by=["region"]), ctx())
    with pytest.raises(StepFailed, match="come neither from the file nor from `constants`"):
        await ex.execute(step(path=str(f), load_mode="replace_partition", partition_by=["batch_id"]), ctx())
    (tmp_path / "x.json").write_text("{}")
    with pytest.raises(StepFailed, match="unsupported file type .json"):
        await ex.execute(step(path=str(tmp_path / "x.json")), ctx())
    with pytest.raises(StepFailed, match="no sheet 'Other'"):
        await ex.execute(step(path=str(xlsx(tmp_path / "s.xlsx", ["period"], [])), sheet="Other"), ctx())
    with pytest.raises(StepFailed, match="unknown target connector"):
        await ex.execute(step(path=str(f), target=SqlEndpoint(connector="ghost", table="t")), ctx())
    with pytest.raises(StepFailed, match="has no columns"):
        await ex.execute(step(path=str(f), target=SqlEndpoint(connector="sales", table="missing")), ctx())
    with pytest.raises(StepFailed, match="no file — set `path` or `watch_dir`"):
        await ex.execute(step(), ctx())
    with pytest.raises(StepFailed, match="watched folder not found"):
        await ex.execute(step(watch_dir=str(tmp_path / "nowhere")), ctx())
    with pytest.raises(StepFailed, match="no file column matches"):
        await ex.execute(step(path=str(csvfile(tmp_path / "h.csv", ["x", "y"], [["1", "2"]]))), ctx())
    # schema-level checks
    with pytest.raises(ValueError, match="missing required field"):
        Step(type="file_load", name="s", path="/x.csv")   # no target
    with pytest.raises(ValueError, match="needs `partition_by`"):
        step(path="/x.csv", load_mode="replace_partition")


@pytest.mark.asyncio
async def test_upload_override_and_rejects_report(registry, tmp_path) -> None:
    """The Run-with-parameters modal uploads a file and fires with op_kwargs[step] = {file: path}:
    that file wins over the step's path; every reject lands in a CSV under the work area."""
    from liberty.config import ConnectorSettings, JobsSettings, Settings
    settings = Settings(connectors=ConnectorSettings(config_path=tmp_path / "cfg" / "connectors.toml"), jobs=JobsSettings(files_dir=tmp_path / "work"))
    ex = FileLoadExecutor(registry, settings=settings)
    configured = csvfile(tmp_path / "configured.csv", ["period", "company", "article", "qty"], [["2026-01", "00001", "A", "1"]])
    uploaded = csvfile(tmp_path / "uploaded.csv", ["period", "company", "article", "qty"], [["2026-02", "00001", "B", "x"], ["2026-02", "00001", "C", "3"]])
    s = step(path=str(configured), on_error="skip")
    res = await ex.execute(with_params(s, file=str(uploaded)), ctx())
    assert res.extras["files"] == [str(uploaded)] and res.rows_affected == 1 and res.extras["rejected"] == 1
    report = tmp_path / "work" / "sales-load" / res.extras["rejects_file"]
    assert res.extras["rejects_file"] == "run-1-load-rejects.csv" and report.is_file()
    assert report.read_text().splitlines() == ["file,row,reason", "uploaded.csv,2,qty: not a whole number: 'x'"]
    assert [r[0] for r in await rows(registry)] == ["2026-02"]
    # a blank per-fire file falls back to the step's path; no settings → no report, no crash
    res = await FileLoadExecutor(registry).execute(with_params(s, file="  "), ctx())
    assert res.extras["files"] == [str(configured)] and "rejects_file" not in res.extras


@pytest.mark.asyncio
async def test_watched_folder_loads_every_file_and_moves_it(registry, tmp_path) -> None:
    ex = FileLoadExecutor(registry)
    inbox = tmp_path / "inbox"; inbox.mkdir()
    csvfile(inbox / "b-2026-04.csv", ["period", "company", "article", "qty"], [["2026-04", "00001", "A", "1"]])
    csvfile(inbox / "a-2026-03.csv", ["period", "company", "article", "qty"], [["2026-03", "00001", "A", "1"], ["2026-03", "00002", "B", "2"]])
    (inbox / "notes.txt").write_text("not a csv for us")
    (inbox / ".hidden.csv").write_text("period\n2026")
    s = step(watch_dir=str(inbox), watch_pattern="*.csv", load_mode="replace_partition", partition_by=["period", "company"])
    res = await ex.execute(s, ctx())
    assert [Path(f).name for f in res.extras["files"]] == ["a-2026-03.csv", "b-2026-04.csv"]   # name order
    assert res.rows_affected == 3 and res.extras["partitions"] == [["2026-03", "00001"], ["2026-03", "00002"], ["2026-04", "00001"]]
    assert "a-2026-03.csv, b-2026-04.csv: 3 row(s) read" in (res.log_excerpt or "")
    done = sorted(p.name for p in (inbox / "done").iterdir())
    assert len(done) == 2 and done[0].endswith("-a-2026-03.csv") and not (inbox / "a-2026-03.csv").exists()
    assert (inbox / "notes.txt").exists() and (inbox / ".hidden.csv").exists()
    # the folder is empty now: nothing to do, not a failure
    res = await ex.execute(s, ctx())
    assert res.rows_affected == 0 and res.extras["files"] == [] and "empty" in (res.log_excerpt or "")
    # a bad file goes to failed/ and the step fails; a good one before it was already loaded and moved
    csvfile(inbox / "c.csv", ["period", "company", "article", "qty"], [["2026-05", "00001", "A", "oops"]])
    with pytest.raises(StepFailed, match="c.csv row 2"):
        await ex.execute(s, ctx())
    assert [p.name for p in (inbox / "failed").iterdir()][0].endswith("-c.csv")


@pytest.mark.asyncio
async def test_refuse_query_guards_the_load(registry, tmp_path) -> None:
    ex = FileLoadExecutor(registry)
    f = csvfile(tmp_path / "m.csv", ["article", "qty"], [["A", "1"]])
    s = step(path=str(f), constants={"period": "${params.period}", "company": "00001"}, refuse_query="period_closed_get")
    # the open period loads; the closed one is refused with the row's first column as the reason
    assert (await ex.execute(with_params(s, period="2026-03"), ctx())).rows_affected == 1
    with pytest.raises(StepFailed, match="refused by period_closed_get: period 2026-02 is closed"):
        await ex.execute(with_params(s, period="2026-02"), ctx())
    assert [r[0] for r in await rows(registry)] == ["2026-03"]
    with pytest.raises(StepFailed, match="refuse query 'broken_get' failed"):
        await ex.execute(with_params(step(path=str(f), refuse_query="broken_get"), period="x"), ctx())
    with pytest.raises(StepFailed, match="unknown refuse connector 'ghost'"):
        await ex.execute(step(path=str(f), refuse_query="period_closed_get", refuse_connector="ghost"), ctx())


@pytest.mark.asyncio
async def test_control_total_from_a_parameter_or_a_cell(registry, tmp_path) -> None:
    from liberty.jobs.steps.file_load import read_cell
    ex = FileLoadExecutor(registry)
    f = xlsx(tmp_path / "t.xlsx", ["Item", "Quantity", "Revenue"], [["A", 2, 10.5], ["B", 3, "20,25"], [None, None, None], ["Total", None, 30.75]])
    # the workbook's own total cell: C5 (header row 1, three data rows, a blank, then the total)
    assert read_cell(f, "@C5", sheet=None, delimiter=None, encoding="utf-8") == 30.75
    assert read_cell(f, "@Sales!A1", sheet=None, delimiter=None, encoding="utf-8") == "Item"
    assert read_cell(f, "@Z99", sheet=None, delimiter=None, encoding="utf-8") is None
    with pytest.raises(StepFailed, match="not a cell reference"):
        read_cell(f, "@total", sheet=None, delimiter=None, encoding="utf-8")
    base = dict(path=str(f), columns={"article": "Item", "qty": "Quantity", "revenue": "Revenue"}, constants={"period": "2026-03", "company": "00001"},
                load_mode="replace_partition", partition_by=["period", "company"], control_column="revenue")
    # the "Total" row is a bad article row? no — article "Total" with a blank qty loads as a row; exclude it by failing on it:
    # keep the fixture honest: the Total row carries revenue 30.75 and would double the sum, so skip it with on_error? It has
    # no bad cell. Instead the expected total counts it: 10.5 + 20.25 + 30.75 = 61.5.
    res = await ex.execute(with_params(step(**base, control_total="${params.total}"), total="61.5"), ctx())
    assert res.rows_affected == 3 and res.extras["control_total"] == "61.50"
    # a cell of the file as the expected value — the Total row says 30.75, the rows sum to 61.5: mismatch, rolled back
    with pytest.raises(StepFailed, match="control total mismatch on revenue: loaded 61.50, expected 30.75 — the load was rolled back"):
        await ex.execute(step(**base, control_total="@C5"), ctx())
    assert len(await rows(registry)) == 3          # the previous load is still there, untouched
    # tolerance
    assert (await ex.execute(step(**base, control_total="61.49", control_tolerance=0.02), ctx())).rows_affected == 3
    with pytest.raises(StepFailed, match="control total mismatch"):
        await ex.execute(step(**base, control_total="61.40", control_tolerance=0.02), ctx())
    # a CSV cell, and the usual failures
    c = csvfile(tmp_path / "c.csv", ["article", "qty", "revenue"], [["A", "1", "2,5"], ["", "", ""], ["total", "", "2,5"]])
    assert read_cell(c, "@C4", sheet=None, delimiter=None, encoding="utf-8") == "2,5"   # header, A, a blank line, then the total row
    with pytest.raises(StepFailed, match="control total '\\$\\{params.total\\}' is not a number"):
        await ex.execute(with_params(step(path=str(c), control_column="revenue", control_total="${params.total}"), total="abc"), ctx())
    with pytest.raises(StepFailed, match="control column 'nope' is not loaded"):
        await ex.execute(step(path=str(c), control_column="nope", control_total="1"), ctx())
    with pytest.raises(ValueError, match="go together"):
        step(path=str(c), control_column="revenue")


@pytest.mark.asyncio
async def test_refuse_query_runs_per_partition_of_the_file(registry, tmp_path) -> None:
    """With partition_by, the guard follows the FILE: one check per partition it carries, the
    partition columns as binds — no typed parameter to forget."""
    ex = FileLoadExecutor(registry)
    f = csvfile(tmp_path / "two.csv", ["period", "company", "article", "qty"], [["2026-03", "00001", "A", "1"], ["2026-02", "00001", "B", "2"], ["2026-03", "00002", "C", "3"]])
    s = step(path=str(f), load_mode="replace_partition", partition_by=["period", "company"], refuse_query="period_closed_get")
    with pytest.raises(StepFailed, match="refused by period_closed_get: period 2026-02 is closed"):
        await ex.execute(s, ctx())
    assert await rows(registry) == []                       # refused before the transaction: nothing loaded
    ok = csvfile(tmp_path / "open.csv", ["period", "company", "article", "qty"], [["2026-03", "00001", "A", "1"], ["2026-03", "00002", "C", "3"]])
    assert (await ex.execute(step(path=str(ok), load_mode="append", partition_by=["period"], refuse_query="period_closed_get"), ctx())).rows_affected == 2


def test_driver_message_keeps_the_sentence_only() -> None:
    from liberty.web.errors import driver_message
    class E(Exception):
        pass
    e = E("(sqlalchemy.dialects.postgresql.asyncpg.Error) invalid input for query argument $1: 2025 (expected str, got int)\n[SQL: SELECT 1] [parameters: (2025,)]")
    assert driver_message(e) == "invalid input for query argument $1: 2025 (expected str, got int)"
    assert driver_message(ValueError("plain")) == "plain"
