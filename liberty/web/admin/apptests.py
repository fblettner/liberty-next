"""The tests of the installation's applications (:mod:`liberty.apptests`): the suites and their
cases, edited and played from the Tests page — a case is drawn as a flow of calls, each with what
it expects."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, Field

from liberty.apptests import runner as r
from liberty.apptests import store
from typing import Annotated

from fastapi import Depends

from liberty.auth.dependencies import CurrentPrincipal
from liberty.auth.principal import Principal
from liberty.web.admin._base import router


def _may(*permissions: str):
    """Who holds one of *permissions* (a superuser holds them all): the tests are an application's
    matter, not the installation's — a functional team writes and plays them without being
    administrators. ``tests:view`` reads suites and results, ``tests:run`` plays them (and may
    read), ``tests:edit`` writes them (and may read and play)."""
    def guard(principal: CurrentPrincipal) -> Principal:
        if not any(principal.has_permission(p) for p in permissions):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {permissions[0]}")
        return principal
    return guard


Viewer = Annotated[Principal, Depends(_may("tests:view", "tests:run", "tests:edit"))]
Runner = Annotated[Principal, Depends(_may("tests:run", "tests:edit"))]
Editor = Annotated[Principal, Depends(_may("tests:edit"))]


def _suite(request: Request, name: str) -> dict[str, Any]:
    settings = request.app.state.settings
    try:
        suite = store.read_suite(settings, name)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no test suite {name!r}") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return {"name": name, "suite": suite.model_dump(mode="json"), "results": store.results(settings, name),
            "has_reference": store.reference(settings, name) is not None}


@router.get("/test-results", summary="How the tests went")
async def tests_results(request: Request, _: Viewer) -> dict[str, Any]:
    """Per suite: its last runs (passed / failed / changed / skipped, the time taken, who) and each
    case with its last statuses and what its last run found — and the totals by last result."""
    settings = request.app.state.settings
    return {"suites": store.history(settings), "totals": store.totals(settings)}


@router.get("/tests", summary="List the test suites")
async def tests_list(request: Request, _: Viewer) -> dict[str, Any]:
    """Every suite with its cases and each case's last result."""
    return {"suites": store.overview(request.app.state.settings)}


@router.get("/tests/{suite}", summary="Read a test suite")
async def tests_read(suite: str, request: Request, _: Viewer) -> dict[str, Any]:
    """The suite whole — its cases and their steps — with the last runs of each case."""
    try:
        return _suite(request, suite)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


class SuiteBody(BaseModel):
    connector: str | None = None
    label: str | None = None
    case: list[dict[str, Any]] = Field(default_factory=list)


@router.put("/tests/{suite}", summary="Save a test suite")
async def tests_save(suite: str, body: SuiteBody, request: Request, _: Editor) -> dict[str, Any]:
    """Write the suite (created when new): validated as a whole, the former file kept in the
    configuration's history. 422 with what is wrong — nothing is written then."""
    state = request.app.state
    try:
        store.write_suite(state.settings, suite, body.model_dump(exclude_none=True), versions=getattr(state, "config_versions", None))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return _suite(request, suite)


@router.delete("/tests/{suite}", summary="Delete a test suite")
async def tests_delete(suite: str, request: Request, _: Editor) -> dict[str, Any]:
    state = request.app.state
    try:
        store.delete_suite(state.settings, suite, versions=getattr(state, "config_versions", None))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return {"deleted": suite}


class RunBody(BaseModel):
    case: str | None = None                 # one case; None = the whole suite
    mode: str = "compare"                   # compare with the reference | record: this run becomes the reference
    allow_writes: bool = False


@router.post("/tests/{suite}/run", summary="Play a test suite or one case")
async def tests_run(suite: str, body: RunBody, request: Request, principal: Runner) -> dict[str, Any]:
    """Play the suite's cases (or one) as saved: checked, compared with the reference run when
    there is one, the results kept. ``mode = record`` keeps what the cases ``keep`` as the new
    reference. A case that writes is skipped unless *allow_writes*."""
    state = request.app.state
    if body.mode not in ("compare", "record"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="mode: compare or record")
    try:
        loaded = store.read_suite(state.settings, suite)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no test suite {suite!r}") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    if body.case and not any(c.id == body.case for c in loaded.case):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no case {body.case!r} in {suite}")
    reference = store.reference(state.settings, suite) if body.mode == "compare" else None
    played = await r.run_suite(state.connectors, loaded.case, connector=loaded.connector, reference=reference,
                               allow_writes=body.allow_writes, only=[body.case] if body.case else None)
    store.keep_results(state.settings, suite, played, mode=body.mode, who=principal.username)
    if body.mode == "record":
        store.keep_reference(state.settings, suite, played)
    return {"results": [c.to_dict() for c in played], "summary": r.summary(played), "mode": body.mode}


class TryBody(BaseModel):
    connector: str
    endpoint: str
    params: dict[str, Any] = Field(default_factory=dict)
    rows: str | None = None


@router.post("/tests/try", summary="Play one call now")
async def tests_try(body: TryBody, request: Request, _: Runner) -> dict[str, Any]:
    """What the endpoint answers NOW to these parameters — to fill what a step expects from it:
    the number of rows and the first row by column name. A call that changes data is not made
    from here (the case is run for that); a failing call is reported, not raised."""
    connectors = request.app.state.connectors
    try:
        api = connectors.api(body.connector)
    except Exception:  # noqa: BLE001 — an unknown or non-API connector
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no API connector {body.connector!r}") from None
    known = r.endpoint_of(api, body.endpoint)
    if known is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no endpoint {body.endpoint!r} on {body.connector}")
    if getattr(known, "writable", False):
        return {"ok": False, "error": f"{body.endpoint} changes data: it is not called from here — save the step and run the case"}
    step = r.Step(name="try", endpoint=body.endpoint, params=body.params, rows=(body.rows or "").strip() or None)
    problems, answer = await r.play(api, step, {})
    if problems:
        return {"ok": False, "error": "; ".join(problems)[:800]}
    try:
        rows = r.rows_of(step, answer, known)
    except Exception as exc:  # noqa: BLE001 — the rows are not where the endpoint says
        return {"ok": False, "error": str(exc)}
    if rows is None:
        return {"ok": True, "rows": None, "first_row": {}}
    first = rows[0] if rows and isinstance(rows[0], dict) else {}
    return {"ok": True, "rows": len(rows),
            "first_row": {str(k): (v.strip() if isinstance(v, str) else v) for k, v in first.items() if not isinstance(v, dict | list) and v not in (None, "")}}


class StarterBody(BaseModel):
    connector: str
    endpoint: str
    taken: list[str] = Field(default_factory=list)      # the ids of the suite's cases: the new one takes another


@router.post("/tests/starter", summary="Prepare a first case from an endpoint")
async def tests_starter(body: StarterBody, request: Request, _: Runner) -> dict[str, Any]:
    """A case to start from, for one endpoint: a step calling it with the example values of its
    variables, expecting what it answers NOW (rows, or the keys that hold something). Nothing is
    saved: the page adds the case to the suite being edited. An endpoint that changes data is not
    called — its case comes marked ``writes``; a call that fails is said in ``note`` and the case is
    given all the same, to be filled."""
    connectors = request.app.state.connectors
    try:
        api = connectors.api(body.connector)
    except Exception:  # noqa: BLE001 — an unknown or non-API connector
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no API connector {body.connector!r}") from None
    known = r.endpoint_of(api, body.endpoint)
    if known is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no endpoint {body.endpoint!r} on {body.connector}")
    if getattr(known, "writable", False):
        return {"case": r.starter_case(known, taken=body.taken), "played": False,
                "note": f"{body.endpoint} changes data: it was not called — say what the call must answer, and add a step that puts things back"}
    step = r.Step(name="call", endpoint=body.endpoint, params=r.starter_params(known))
    problems, answer = await r.play(api, step, {})
    if problems:
        return {"case": r.starter_case(known, taken=body.taken), "played": False,
                "note": ("the call did not work with the example values — fill the step's variables, then Try: " + "; ".join(problems))[:800]}
    try:
        rows = r.rows_of(step, answer, known)
    except Exception:  # noqa: BLE001 — the rows are not where the endpoint says: the case is given without them
        rows = None
    return {"case": r.starter_case(known, answer=answer, rows=rows, taken=body.taken), "played": True, "note": None}
