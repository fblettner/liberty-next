"""``/admin`` — find usages / dependencies, packages (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from liberty.web.admin._base import Superuser, _snapshot_all_config, router
from liberty.web.admin.bundles import reload_connectors
from pydantic import BaseModel

from liberty.web.dependencies import Seed, collect_dependencies
from liberty.web.package import build_package_zip
from liberty.web.usages import find_usages

# ── Find usages ─────────────────────────────────────────────────────────────────────────────
# Every Settings editor surfaces a "Find usages" button that calls this. The reference graph
# lives in liberty.web.usages — walks the in-memory registries (no disk reads) and returns a
# grouped list of every place the named entity is referenced. Used to safely rename / delete
# and to spot orphans. See usages.py for the full kind list.


class FindUsagesBody(BaseModel):
    kind: str
    name: str
    scope: str | None = None


class SeedItem(BaseModel):
    kind: str
    name: str
    scope: str | None = None


class FindDependenciesBody(BaseModel):
    seeds: list[SeedItem]


@router.post("/find-dependencies", summary="Find dependencies")
async def find_dependencies_endpoint(body: FindDependenciesBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Walk the dependency closure for *seeds* — every connector / query / pool / DD entry
    / lookup / sequence / chart / screen / menu item the seeds transitively reach. Returns
    the manifest (counts, per-kind groups, missing refs, warnings) for the inspect UI to
    render as a tree. Same walker the package builder uses; this endpoint just doesn't
    serialise to a ZIP."""
    manifest = collect_dependencies(
        request.app.state,
        [Seed(kind=s.kind, name=s.name, scope=s.scope) for s in body.seeds],
    )
    return manifest.to_dict()


class IncludeItem(BaseModel):
    kind: str
    name: str
    scope: str | None = None


class BuildPackageBody(BaseModel):
    seeds: list[SeedItem]
    # Optional filter — only deps whose (kind, scope, name) is in this list go into the ZIP.
    # When absent or None, every dep ships (older behaviour). The frontend sends this with
    # the operator's per-dep checkbox state so connectors / pools / api_endpoints (which are
    # default-excluded in the UI) don't land on the target unless the operator opted in.
    include: list[IncludeItem] | None = None


@router.post("/build-package", summary="Build deployment package")
async def build_package_endpoint(body: BuildPackageBody, request: Request, _: Superuser):
    """Build a ZIP package of the dependency closure — returns ``application/zip`` ready to
    download. Optional ``include`` list filters the closure to the operator's per-dep
    selection (the frontend default-excludes connector / pool / api_endpoint).

    Apply on the target by unzipping into its ``config/`` and running ``POST /admin/reload``
    — or call ``POST /admin/import-package`` to merge / overwrite per a strategy. Secrets
    (``ENC:...`` / ``${VAR}``) pass through verbatim; the target must carry the same
    master_key + env vars (MANIFEST.md lists what's needed)."""
    from fastapi.responses import Response
    manifest = collect_dependencies(
        request.app.state,
        [Seed(kind=s.kind, name=s.name, scope=s.scope) for s in body.seeds],
    )
    include_set: set[tuple[str, str | None, str]] | None = None
    if body.include is not None:
        include_set = {(i.kind, i.scope, i.name) for i in body.include}
    from liberty import __version__
    src = getattr(request.app.state.settings.app, "name", "liberty-next")
    blob = build_package_zip(manifest, source_install=src, include=include_set,
                             settings=request.app.state.settings, source_version=__version__)
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="liberty-package.zip"'},
    )


# Hard cap on a deployment package upload. A real package is a few hundred KB of TOML
# (the biggest install's screens.toml is ~560 KB zipped to ~60 KB); 50 MB leaves room
# for presets / reports while keeping ``await upload.read()`` from buffering arbitrary
# bodies into memory. The unpacked size is capped separately in package_import.
MAX_PACKAGE_BYTES = 50 * 1024 * 1024


@router.post("/import-package", summary="Import deployment package")
async def import_package_endpoint(
    request: Request, _: Superuser,
    strategy: str = "overwrite",
    dry_run: bool = False,
):
    """Apply — or, with ``dry_run``, preview — a package ZIP (produced by /admin/build-package,
    here or on another instance) on this install's config files.

    Multipart upload — the ZIP is the request body's ``package`` file part; the strategy
    is a query param: ``merge`` / ``overwrite`` / ``replace_all`` (see
    liberty/web/package_import.py for semantics).

    Returns ``{report: {files: [...], warnings, summary, dry_run, source, reloaded}}``: per file
    the plan of its entities (``new`` / ``changed`` with a field diff / ``identical`` / ``kept`` /
    ``skipped`` / ``removed``). An apply snapshots every config file first and reloads after."""
    # ``request.form()`` yields Starlette's UploadFile; ``fastapi.UploadFile`` is a *subclass*
    # of it, so an isinstance check against the FastAPI class is always False for a real
    # upload — that made every import read as "missing 'package' file part" until 7.0.57.
    from starlette.datastructures import UploadFile

    from liberty.web.package_import import apply_package_zip
    if strategy not in ("merge", "overwrite", "replace_all"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"invalid strategy: {strategy!r} — pick merge / overwrite / replace_all")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_PACKAGE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                            detail=f"package upload exceeds {MAX_PACKAGE_BYTES // (1024 * 1024)} MB")
    form = await request.form()
    upload = form.get("package")
    if not isinstance(upload, UploadFile) or not upload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="missing 'package' file part")
    blob = await upload.read(MAX_PACKAGE_BYTES + 1)
    if len(blob) > MAX_PACKAGE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                            detail=f"package upload exceeds {MAX_PACKAGE_BYTES // (1024 * 1024)} MB")
    from liberty import __version__
    if not dry_run:
        _snapshot_all_config(request)  # cross-file mutation — snapshot all config first
    try:
        report = apply_package_zip(zip_bytes=blob, strategy=strategy, settings=request.app.state.settings,  # type: ignore[arg-type]
                                   dry_run=dry_run, version=__version__)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if dry_run:
        return {"report": report.to_dict()}
    # Reload — same path the structured PUT endpoints expect operators to call afterwards.
    try:
        await reload_connectors(request, _)
        report.reloaded = True
    except Exception as exc:  # noqa: BLE001 — surface reload failures but keep the import report
        report.warnings.append(f"reload failed after apply: {type(exc).__name__}: {exc}")
    return {"report": report.to_dict()}


class ExportJobBody(BaseModel):
    id: str


@router.post("/export-job", summary="Export job package")
async def export_job_endpoint(body: ExportJobBody, request: Request, _: Superuser):
    """Export ONE nomaflow job as a ZIP — minimal ``jobs.toml`` carrying just this job, plus
    a MANIFEST.md. Job deployment is intentionally separate from the screen / dashboard
    package: jobs are operational and the operator decides per-environment whether to ship
    them. Apply on the target by unzipping into the target's nomaflow plugin dir (the same
    ``LIBERTY_APPS_DIR/../plugins/nomaflow/jobs.toml`` path the loader watches) and reloading."""
    from fastapi.responses import Response
    jobs_registry = getattr(request.app.state, "jobs", None)
    if jobs_registry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="nomaflow registry not loaded")
    registry = getattr(jobs_registry, "registry", None) or jobs_registry
    try:
        job = registry.get(body.id)
    except Exception as exc:  # UnknownJobError
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"job {body.id!r} not found: {exc}") from exc
    # Minimal jobs.toml: only the single job, no meta / step_defaults — the target preserves
    # its own meta (retention policy is per-install).
    import tomli_w
    job_dict = job.model_dump(by_alias=True, exclude_defaults=True, exclude_none=True)
    jobs_toml = tomli_w.dumps({"jobs": [job_dict]})
    manifest_md = (
        f"# Nomaflow job export — `{body.id}`\n\n"
        f"**Source install:** `{request.app.state.settings.app.name}`\n\n"
        f"## Apply on the target\n\n"
        "1. Unzip into the target install's nomaflow jobs.toml location\n"
        "   (typically `${LIBERTY_APPS_DIR}/../plugins/nomaflow/jobs.toml` — see spec).\n"
        "2. Either replace the file (single-job target) or merge the `[[jobs]]` entry into\n"
        "   your target's existing jobs.toml.\n"
        "3. Reload nomaflow:\n"
        "   ```\n"
        "   curl -X POST -H 'Authorization: Bearer <superuser-token>' https://<target>/admin/reload\n"
        "   ```\n\n"
        "## Notes\n\n"
        "- Queries / connectors / pools referenced by this job are NOT bundled — make sure\n"
        "  the target install already has them. Use `Liberty Next → Deploy → Package` to ship the\n"
        "  underlying config slice separately if needed.\n"
        "- ENC: secrets and ${VAR} references pass through verbatim. The target needs the\n"
        "  same `[crypto] master_key` and any referenced env vars set.\n"
    )
    import io as _io
    import zipfile as _zipfile
    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, "w", _zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("jobs.toml", jobs_toml)
        zf.writestr("MANIFEST.md", manifest_md)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="nomaflow-job-{body.id}.zip"'},
    )


@router.post("/find-usages", summary="Find usages")
async def find_usages_endpoint(body: FindUsagesBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Return every reference to ``(kind, name, scope)`` across the loaded configs. Walks the
    in-memory state — call ``POST /admin/reload`` first if you've edited a file on disk and
    need fresh results. Empty ``usages`` means safe to rename / delete (no references)."""
    try:
        results = find_usages(request.app.state, kind=body.kind, name=body.name, scope=body.scope)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {
        "kind": body.kind, "name": body.name, "scope": body.scope,
        "usages": [u.to_dict() for u in results],
        "count": len(results),
    }
