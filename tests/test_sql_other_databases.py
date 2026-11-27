"""What the SQL connector says differently to SQL Server and Db2 — and that Oracle is told what it always was."""
from __future__ import annotations

from liberty.connectors.sql import _column_types_sql
from liberty.connectors.sqltext import _build_filter_predicate, _oracle_target_table


def test_the_column_types_are_asked_of_each_database_own_catalogue() -> None:
    sql, owner = _column_types_sql("oracle", "SY920")
    assert sql == "SELECT column_name, data_type, char_length FROM all_tab_columns WHERE owner = :o AND table_name = :t" and owner == "SY920"
    sql, owner = _column_types_sql("mssql", "JDE_PRODUCTION.DBO")
    assert "FROM JDE_PRODUCTION.INFORMATION_SCHEMA.COLUMNS" in sql and owner == "DBO"          # a table of another database: its own catalogue
    sql, owner = _column_types_sql("mssql", "DBO")
    assert "FROM INFORMATION_SCHEMA.COLUMNS" in sql and owner == "DBO"
    assert "SYSCAT.COLUMNS" in _column_types_sql("db2", "PRODDTA")[0] and "QSYS2.SYSCOLUMNS" in _column_types_sql("ibmi", "PRODDTA")[0]
    assert _column_types_sql("postgresql", "public") is None                                    # not known here: no bind hygiene, as before


def test_the_table_a_write_targets_may_be_named_with_its_database() -> None:
    assert _oracle_target_table("INSERT INTO SY920.F00950 (A) VALUES (:A)") == ("SY920", "F00950")
    assert _oracle_target_table("UPDATE JDE_PRODUCTION.dbo.F0101 SET ABALPH = :ABALPH") == ("JDE_PRODUCTION.DBO", "F0101")
    assert _oracle_target_table("DELETE FROM F0101 WHERE ABAN8 = :ABAN8") == (None, "F0101")


def test_a_text_filter_is_written_for_sql_server_its_own_way_only() -> None:
    for dialect in ("oracle", "postgresql", "db2"):
        assert "'%' ||" in _build_filter_predicate("NAME", None, "contains", dialect)
    said = _build_filter_predicate("NAME", None, "contains", "mssql")
    assert "||" not in said and "CONCAT('%', CAST(:NAME AS VARCHAR(4000)), '%')" in said
    assert "||" not in _build_filter_predicate("NAME", None, "startsWith", "mssql") and "||" not in _build_filter_predicate("NAME", None, "endsWith", "mssql")
    assert _build_filter_predicate("NAME", None, "equals", "mssql") == _build_filter_predicate("NAME", None, "equals", "db2")


# ── the Data Explorer's calculated fields, each database's own way ──
def _compiled(dialect: str, expr: str) -> str:
    from liberty.explorer.calc import Compiler, date_ref, render
    from liberty.explorer.model import ExplorerField

    fields = {"NAME": ExplorerField(key="t.NAME", entity="t", name="NAME", label="Name", type="string", kind="dimension"),
              "KIND": ExplorerField(key="t.KIND", entity="t", name="KIND", label="Kind", type="string", kind="dimension"),
              "FROM_DAY": ExplorerField(key="t.FROM_DAY", entity="t", name="FROM_DAY", label="From", type="date", kind="dimension", format="jdedate"),
              "TO_DAY": ExplorerField(key="t.TO_DAY", entity="t", name="TO_DAY", label="To", type="date", kind="dimension", format="jdedate"),
              "AMOUNT": ExplorerField(key="t.AMOUNT", entity="t", name="AMOUNT", label="Amount", type="number", kind="measure")}

    def resolve(name: str) -> tuple[ExplorerField, str]:
        fld = fields[name.split(".")[-1].upper()]
        return fld, date_ref(fld, f"e.{fld.name}")

    return render(Compiler(dialect, resolve).compile(expr))


def test_oracle_is_told_what_it_always_was() -> None:
    assert _compiled("oracle", "concat(NAME, ' / ', KIND)") == "(e.NAME || ' / ' || e.KIND)"
    assert _compiled("oracle", "length(NAME)") == "LENGTH(e.NAME)" and _compiled("oracle", "round(AMOUNT)") == "ROUND(e.AMOUNT)"
    # a JD Edwards date: the same conversion as ever, behind the test that a date left blank (0) is no date
    assert _compiled("oracle", "year(FROM_DAY)") == ("EXTRACT(YEAR FROM CASE WHEN e.FROM_DAY > 0 THEN "
                                                     "TO_DATE(TO_CHAR(1900 + TRUNC(e.FROM_DAY / 1000)) || LPAD(MOD(e.FROM_DAY, 1000), 3, '0'), 'YYYYDDD') END)")
    assert _compiled("oracle", "days(FROM_DAY, TO_DAY)").startswith("(TRUNC(CASE WHEN e.TO_DAY > 0 THEN TO_DATE(")
    assert _compiled("postgresql", "month(FROM_DAY)").startswith("EXTRACT(MONTH FROM CASE WHEN e.FROM_DAY > 0 THEN TO_DATE(")


def test_sql_server_and_db2_get_their_own_words() -> None:
    assert _compiled("mssql", "concat(NAME, ' / ', KIND)") == "CONCAT(e.NAME, ' / ', e.KIND)"
    assert _compiled("mssql", "length(NAME)") == "LEN(e.NAME)" and _compiled("mssql", "round(AMOUNT)") == "ROUND(e.AMOUNT, 0)"
    year = _compiled("mssql", "year(FROM_DAY)")
    assert year.startswith("DATEPART(year, CASE WHEN e.FROM_DAY > 0 THEN DATEADD(day,") and "DATEFROMPARTS" in year and "TO_DATE" not in year
    assert _compiled("mssql", "days(FROM_DAY, TO_DAY)").startswith("DATEDIFF(day, CASE WHEN e.FROM_DAY > 0")
    for dialect in ("db2", "ibmi"):
        assert _compiled(dialect, "concat(NAME, ' / ', KIND)") == "(e.NAME || ' / ' || e.KIND)"
        days = _compiled(dialect, "days(FROM_DAY, TO_DAY)")
        assert days.startswith("(DAYS(CASE WHEN e.TO_DAY > 0 THEN DATE(DAYS(DATE(") and "TO_DATE" not in days and "AS TEXT" not in days
        assert _compiled(dialect, "month(FROM_DAY)").startswith("EXTRACT(MONTH FROM CASE WHEN e.FROM_DAY > 0")


# ── a schema name the pool need not define ──
def test_an_optional_schema_name_takes_another_one_unless_the_pool_sets_it(tmp_path) -> None:
    from liberty.connectors import load_connectors

    def registry(schemas: str, links: str = "") -> object:
        p = tmp_path / "connectors.toml"
        p.write_text(f"""
[pools.default]
url = "sqlite+aiosqlite://"
schemas = {{ {schemas} }}
dblinks = {{ {links} }}

[connectors.app]
type = "sql"
pool = "default"
schema_defaults = {{ UDO_SEC = "SY" }}
""")
        return load_connectors(p).get("app")

    usual = registry('SY = "SY920"', 'SY = "@PROD"')
    assert usual.schemas()["UDO_SEC"] == "SY920" and usual.dblinks()["UDO_SEC"] == "@PROD"
    elsewhere = registry('SY = "SY910", UDO_SEC = "SY920"')
    assert elsewhere.schemas()["UDO_SEC"] == "SY920" and elsewhere.schemas()["SY"] == "SY910" and "UDO_SEC" not in elsewhere.dblinks()
    blank = registry('SY = "SY920", UDO_SEC = ""')
    assert blank.schemas()["UDO_SEC"] == "SY920"


# ── an application's function reads the environment picked on the page ──
def test_a_function_is_handed_the_connector_of_the_pool_of_the_moment(tmp_path) -> None:
    import liberty.jobs  # noqa: F401 — the package that completes liberty.plugins.invoke's imports, as the server loads it
    from liberty.connectors import load_connectors
    from liberty.connectors.api import current_pool
    from liberty.kept import answer_key
    from liberty.plugins.invoke import build_kwargs

    p = tmp_path / "connectors.toml"
    p.write_text("""
[pools.prod]
url = "sqlite+aiosqlite://"
[pools.test]
url = "sqlite+aiosqlite://"
[pools.other]
url = "sqlite+aiosqlite://"

[connectors.erp]
type = "sql"
pool = "prod"
pools = ["test"]

[connectors.own]
type = "sql"
pool = "other"
""")
    registry = load_connectors(p)

    def j_read(*, connectors, **_: object) -> tuple[str, str]:
        return connectors.get("erp").pool_name, connectors.sql("own").pool_name

    def run() -> tuple[str, str]:
        return j_read(**build_kwargs(j_read, {}, injections=(("connectors", registry),)))

    assert run() == ("prod", "other")                               # nothing picked: each connector's main database
    plain = answer_key("app.mod:j_x", {"a": 1})
    token = current_pool.set("test")
    try:
        assert run() == ("test", "other")                           # the pool picked, for the connector that has it
        assert answer_key("app.mod:j_x", {"a": 1}) != plain         # and an answer kept for it is its own
    finally:
        current_pool.reset(token)
    token = current_pool.set("elsewhere")
    try:
        assert run() == ("prod", "other")                           # a pool that is not the connector's: its main database
    finally:
        current_pool.reset(token)
    assert answer_key("app.mod:j_x", {"a": 1}) == plain
    assert "erp" in build_kwargs(j_read, {}, injections=(("connectors", registry),))["connectors"]


# ── two environments: the API connector's server follows the database the screens read ──
def _two_environments(tmp_path, sql_main: str, api_pool: str):
    from liberty.connectors import load_connectors

    other = "uf" if api_pool == "bf" else "bf"
    p = tmp_path / "connectors.toml"
    p.write_text(f"""
[pools.bf]
url = "sqlite+aiosqlite://"
[pools.uf]
url = "sqlite+aiosqlite://"

[connectors.erp]
type = "sql"
pool = "{sql_main}"
pools = ["{'uf' if sql_main == 'bf' else 'bf'}"]

[connectors.ais]
type = "api"
base_url = "https://ais-{api_pool}.example"
pool = "{api_pool}"

[connectors.ais.targets.{other}]
base_url = "https://ais-{other}.example"
""")
    return load_connectors(p)


def test_with_no_pool_picked_the_api_server_is_the_one_of_the_main_database(tmp_path) -> None:
    from liberty.connectors.api import current_pool

    def server(registry) -> str:
        return str(registry.api("ais").config.base_url)

    aligned = _two_environments(tmp_path, sql_main="bf", api_pool="bf")
    assert server(aligned) == "https://ais-bf.example"
    # the API connector's own server is UF's while the screens read BF by default: no pool picked still means BF
    crossed = _two_environments(tmp_path, sql_main="bf", api_pool="uf")
    assert server(crossed) == "https://ais-bf.example"
    for registry in (aligned, crossed):
        for pool, expected in (("uf", "https://ais-uf.example"), ("bf", "https://ais-bf.example")):
            token = current_pool.set(pool)
            try:
                assert server(registry) == expected
            finally:
                current_pool.reset(token)


# ── a job's environment: the step's, else the job's, else the page's ──
async def test_a_step_runs_for_the_steps_pool_then_the_jobs_then_the_pages() -> None:
    import contextvars

    from liberty.connectors.api import current_pool
    from liberty.jobs.schema import Job

    def picked(step_pool: str | None, job_pool: str | None, page_pool: str | None) -> str | None:
        """What the runner sets for a step (liberty.jobs.runner._run_step_with_retry)."""
        ctx = contextvars.copy_context()
        ctx.run(current_pool.set, page_pool)
        return ctx.run(lambda: step_pool or job_pool or current_pool.get())

    assert picked("step", "job", "page") == "step" and picked(None, "job", "page") == "job"
    assert picked(None, None, "page") == "page" and picked(None, None, None) is None
    job = Job.model_validate({"id": "j", "pool": "uf", "steps": [{"type": "sql_query", "name": "s", "connector": "c", "query": "q"}]})
    assert job.pool == "uf" and Job.model_validate({"id": "j", "steps": [{"type": "sql_query", "name": "s", "connector": "c", "query": "q"}]}).pool is None
