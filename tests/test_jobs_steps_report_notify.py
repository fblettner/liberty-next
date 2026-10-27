"""The digest steps (C5): ``report`` renders a saved explorer analysis / dashboard to the run's
work area, under a user's or a role's visibility; ``notify`` mails the run's files. On the
SQLite explorer model of test_explorer_engine."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from openpyxl import load_workbook

from liberty.config import ConnectorSettings, JobsSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.explorer import parse_explorer
from liberty.explorer.model import ModelCache
from liberty.explorer.store import KIND_ANALYSIS, KIND_DASHBOARD, AnalysisStore
from liberty.jobs import ManualTrigger, RunContext, Step, StepFailed
from liberty.jobs.schema import StepType
from liberty.jobs.steps.notify_step import NotifyStepExecutor
from liberty.jobs.steps.report_step import ReportStepExecutor
from liberty.jobs.wiring import build_executors
from liberty.notify.dispatcher import DispatchReport
from liberty.userviews.store import UserViewStore
from tests.test_explorer_engine import MODEL, env  # noqa: F401 — the SQLite model fixture

DEF = {"entity": "sales", "view": "table", "rows": [{"field": "company.NAME"}], "measures": [{"field": "sales.REVENUE", "agg": "sum"}],
       "filterRows": [], "period": {"field": "sales.SOLD_ON", "preset": "all", "range": {"dateFrom": "", "dateTo": ""}}, "sort": [], "limit": 100, "calculated": []}


class _Dispatcher:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def dispatch(self, message, *, channels, recipients, action=None, attachments=None):
        self.calls.append({"subject": message.subject, "body": message.body, "link": message.link, "channels": channels, "recipients": recipients, "attachments": [(n, len(b)) for n, b in (attachments or [])], "payload": message.payload})
        return DispatchReport(portal_users=["ann"], emails=["ops@corp.test"])


class _Backend:
    """Two roles: `group` sees everything, `c1` only company C1; ann holds c1, bob is in group."""

    async def list_roles(self):
        return [("group", ["*"], None, []), ("c1", ["*"], None, [{"column": "COMPANY", "values": ["C1"]}]),
                ("attr", ["*"], None, [{"column": "COMPANY", "source": "#USER.company#"}])]

    async def get_by_username(self, username):
        users = {
            "ann": SimpleNamespace(username="ann", roles=["c1"], attributes={}, is_superuser=False),
            "bob": SimpleNamespace(username="bob", roles=["group"], attributes={}, is_superuser=False),
            "cid": SimpleNamespace(username="cid", roles=["attr"], attributes={"company": ["C2"]}, is_superuser=False),
        }
        return users.get(username)


@pytest_asyncio.fixture
async def state(env, tmp_path):  # noqa: F811
    registry, screens, view = env
    pools = PoolRegistry({"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'views.db'}")})
    views = UserViewStore(pools, "default")
    await views.create_schema()
    store = AnalysisStore(views)
    await store.save("shop/sales", KIND_ANALYSIS, "ann", obj_id=None, name="Revenue by company", definition=DEF, shared=True)
    await store.save("shop/sales", KIND_DASHBOARD, "ann", obj_id=None, name="Board", definition={"tiles": [{"analysis": "revenue-by-company", "w": 2, "h": "m"}]}, shared=True)
    await store.save("shop/sales", KIND_ANALYSIS, "ann", obj_id=None, name="Private", definition=DEF, shared=False)
    settings = Settings(connectors=ConnectorSettings(config_path=tmp_path / "connectors.toml"), jobs=JobsSettings(files_dir=tmp_path / "files"))
    explorer = parse_explorer({"explorer": {"shop": {"sales": MODEL}}})
    st = SimpleNamespace(settings=settings, connectors=registry, screens=screens, explorer=explorer, explorer_cache=ModelCache(), user_views=views,
                         notify=_Dispatcher(), auth_backend=_Backend(), reports=None)
    yield st
    await pools.dispose()


def _ctx(run_id: str = "run1") -> RunContext:
    return RunContext(run_id=run_id, job_id="digest", trigger=ManualTrigger(triggered_by="t"))


@pytest.mark.asyncio
async def test_report_step_renders_an_analysis_and_a_dashboard(state) -> None:
    ex = ReportStepExecutor(lambda: state)
    res = await ex.execute(Step(type=StepType.REPORT, name="revenue", report="analysis:revenue-by-company", app="shop", model="sales"), _ctx())
    assert res.extras["file"] == "run1-Revenue_by_company.xlsx" and res.extras["format"] == "xlsx" and "1 section(s)" in (res.log_excerpt or "")
    path = Path(state.settings.jobs.files_dir) / "digest" / res.extras["file"]
    wb = load_workbook(io.BytesIO(path.read_bytes()))
    ws = wb[wb.sheetnames[0]]
    assert [c.value for c in ws[2]] == ["NAME", "sum REVENUE"] and ws["A3"].value == "Corp one" and ws.max_row == 5   # 3 companies + the note row
    # a dashboard: one section per tile; a filename of the operator's; a forced window
    res = await ex.execute(Step(type=StepType.REPORT, name="board", report="dashboard:board", app="shop", model="sales", filename="monthly", window_from="2026-01-01", window_to="2026-01-31"), _ctx())
    assert res.extras["file"] == "run1-monthly.xlsx"
    wb = load_workbook(io.BytesIO((Path(state.settings.jobs.files_dir) / "digest" / "run1-monthly.xlsx").read_bytes()))
    assert wb.sheetnames == ["Revenue by company"] and wb.active["A1"].value.startswith("period 2026-01-01 → 2026-01-31")
    assert wb.active.max_row == 3        # January: C1 only
    # a private object is not a job's business; an unknown preset fails clearly
    with pytest.raises(StepFailed, match="no shared analysis 'private'"):
        await ex.execute(Step(type=StepType.REPORT, name="p", report="analysis:private", app="shop", model="sales"), _ctx())
    with pytest.raises(StepFailed, match="unknown period preset"):
        await ex.execute(Step(type=StepType.REPORT, name="p", report="analysis:revenue-by-company", app="shop", model="sales", period="nope"), _ctx())


@pytest.mark.asyncio
async def test_report_step_under_a_user_or_a_role_visibility(state) -> None:
    ex = ReportStepExecutor(lambda: state)

    async def companies(**kw) -> list[str]:
        res = await ex.execute(Step(type=StepType.REPORT, name="r", report="analysis:revenue-by-company", app="shop", model="sales", **kw), _ctx("run2"))
        wb = load_workbook(io.BytesIO((Path(state.settings.jobs.files_dir) / "digest" / res.extras["file"]).read_bytes()))
        ws = wb[wb.sheetnames[0]]
        return [ws.cell(row=r, column=1).value for r in range(3, ws.max_row + 1)]

    assert await companies() == ["Corp one", "Corp three", "Corp two"]
    assert await companies(as_user="ann") == ["Corp one"]                      # her role's rule
    assert await companies(as_user="bob") == ["Corp one", "Corp three", "Corp two"]
    assert await companies(as_user="cid") == ["Corp two"]                      # a rule fed by her attribute
    assert await companies(as_role="c1") == ["Corp one"]
    assert await companies(as_role="attr") == []                               # no user: the attribute rule allows nothing
    with pytest.raises(StepFailed, match="unknown user"):
        await companies(as_user="ghost")
    with pytest.raises(StepFailed, match="unknown role"):
        await companies(as_role="ghost")


@pytest.mark.asyncio
async def test_notify_step_attaches_the_run_files(state) -> None:
    rep = ReportStepExecutor(lambda: state)
    await rep.execute(Step(type=StepType.REPORT, name="revenue", report="analysis:revenue-by-company", app="shop", model="sales"), _ctx("run3"))
    (Path(state.settings.jobs.files_dir) / "digest" / "other-run.xlsx").write_bytes(b"x")     # another run's file: not ours
    ex = NotifyStepExecutor(lambda: state)
    res = await ex.execute(Step(type=StepType.NOTIFY, name="mail", recipients=["role:cfo", "ops@corp.test"], subject="Digest {DATE} — {JOB}", body="Attached: {FILES}"), _ctx("run3"))
    call = state.notify.calls[-1]
    assert call["recipients"] == ["role:cfo", "ops@corp.test"] and call["channels"] == ["portal", "email"]
    assert call["attachments"] == [("Revenue_by_company.xlsx", call["attachments"][0][1])] and call["attachments"][0][1] > 0
    assert call["payload"]["FILES"] == "Revenue_by_company.xlsx" and call["link"] == "/nomaflow/runs/run3"
    assert res.extras["attached"] == ["Revenue_by_company.xlsx"] and res.rows_affected == 2
    # a named file that this run did not produce fails; no attachment at all is fine
    with pytest.raises(StepFailed, match="not produced by this run"):
        await ex.execute(Step(type=StepType.NOTIFY, name="m", recipients=["user:ann"], subject="s", attach=["ghost.pdf"]), _ctx("run3"))
    res = await ex.execute(Step(type=StepType.NOTIFY, name="m", recipients=["user:ann"], subject="s", attach=[]), _ctx("run3"))
    assert res.extras["attached"] == []


def test_schema_and_wiring() -> None:
    with pytest.raises(ValueError, match="analysis:<id>, dashboard:<id> or report:<scope>:<id>"):
        Step(type=StepType.REPORT, name="r", report="nope")
    with pytest.raises(ValueError, match="missing required field"):
        Step(type=StepType.REPORT, name="r", report="analysis:x")          # app + model
    with pytest.raises(ValueError, match="plugin report is report:<scope>:<id>"):
        Step(type=StepType.REPORT, name="r", report="report:onlyscope")
    with pytest.raises(ValueError, match="go together"):
        Step(type=StepType.REPORT, name="r", report="report:nomasx1:audit", window_from="2026-01-01")
    with pytest.raises(ValueError, match="missing required field"):
        Step(type=StepType.NOTIFY, name="n", subject="s")
    assert Step(type=StepType.NOTIFY, name="n", subject="s", recipients=["user:a"]).attach == ["*"]
    from liberty.connectors.config import ConnectorsFile
    from liberty.connectors.registry import ConnectorRegistry
    reg = ConnectorRegistry(ConnectorsFile())
    assert StepType.REPORT not in build_executors(reg)
    ex = build_executors(reg, state=lambda: None)
    assert isinstance(ex[StepType.REPORT], ReportStepExecutor) and isinstance(ex[StepType.NOTIFY], NotifyStepExecutor)
