# Liberty Next — the open framework image (public).
#
# Builds the React SPA, then a slim Python runtime that serves the SPA *and* the API on
# one port (FastAPI + Socket.IO, exactly what ./start.sh runs). This image carries NO
# licensed content — it runs as the open framework (restricted mode) with whatever config
# the operator mounts at /app/config (or via LIBERTY_APPS_DIR). The licensed product is a
# separate image that builds FROM this one (see liberty-apps/Dockerfile) and lives in a
# private registry; pull access to that image is the license gate.
#
#   docker build -t liberty-next:local .
#   docker run --rm -p 8000:8000 liberty-next:local        # → open framework on :8000
#
# Runtime DB defaults to SQLite (./liberty.db) so the bare image runs with no external DB.
# Point LIBERTY_DB_URL at Postgres for a real deployment (see docker-compose.yml).

# ── Stage 1 — build the frontend (Vite → frontend/dist) ──────────────────────────────
FROM node:20-slim AS frontend
WORKDIR /build/frontend
# Copy only the manifest + lockfile first so the install layer is cached across source-only
# edits. We use `npm install` (not `npm ci`) to match the project's own start.sh — the
# committed lockfile drifts from package.json, which `npm ci` rejects but `npm install`
# reconciles. --no-audit/--no-fund keep the build log clean.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build   # emits /build/frontend/dist (vite.config.ts: outDir "dist")

# ── Stage 2 — Python runtime (FastAPI + Socket.IO, serving the built SPA) ─────────────
FROM python:3.12-slim AS runtime

# Runtime system libs:
#   curl                         — container HEALTHCHECK hits /info
#   libpango-1.0-0, libpangoft2-1.0-0,
#   libharfbuzz0b, libfontconfig1 — WeasyPrint (PDF report rendering, liberty.reports.render)
# No DB client libs needed — asyncpg (Postgres), oracledb (thin mode), and psycopg2-binary
# (Alembic's sync runner) all ship self-contained wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

# libaio — the Oracle Instant Client's runtime dependency (libaio.so.1). Two Debian-trixie traps:
#   1. ``libaio1`` is now a transitional package; the real lib is ``libaio1t64``.
#   2. libaio1t64 ships the library as ``libaio.so.1t64`` (the time_t rename), but the (proprietary,
#      un-recompilable) Oracle client is linked against ``libaio.so.1`` → DPI-1047 "libaio.so.1:
#      cannot open shared object file" even though libaio IS installed.
# On 64-bit arches (amd64/arm64) time_t was already 64-bit, so libaio.so.1t64 is ABI-identical to
# libaio.so.1 — a compat symlink is safe and lets the client load. (On bookworm ``libaio1`` ships
# libaio.so.1 directly, so the find-loop is a no-op there.)
RUN apt-get update \
    && (apt-get install -y libaio1t64 || apt-get install -y libaio1) \
    && rm -rf /var/lib/apt/lists/* \
    && for f in $(find /usr/lib -name 'libaio.so.1t64' 2>/dev/null); do \
         ln -sf "$f" "$(dirname "$f")/libaio.so.1"; \
       done \
    && ldconfig

# ── Oracle Instant Client (thick OCI client) ─────────────────────────────────────
# python-oracledb THIN mode can't fetch a LOB over a database link (ORA-22992); the THICK
# client can. ``liberty.connectors.thick`` runs those few queries in a subprocess that turns
# on thick mode with this client — the main app stays thin/async.
#
# VENDORED, not downloaded at build time (Oracle's download is login-gated, so a build-time
# fetch fails in CI). Drop the **Linux** Instant Client Basic ZIP per platform into:
#   docker/instantclient/amd64/instantclient-basic-linux.x64-<ver>.zip     (Linux x86-64)
#   docker/instantclient/arm64/instantclient-basic-linux.arm64-<ver>.zip   (Linux aarch64)
# ⚠ These must be the LINUX zips — NOT the macOS client you may use for local `fastapi dev`.
# An arch you don't deploy can hold just its .gitkeep — the build still succeeds and thick mode
# is simply unavailable there (a clean ThickFetchError at runtime). Match the client version to
# your Oracle DB: a 19c client connects to 11.2 … 23ai; a 23ai client connects to 19c+ only.
# (The release builds linux/amd64 + linux/arm64; ``TARGETARCH`` selects the matching dir.)
ARG TARGETARCH
COPY docker/instantclient/${TARGETARCH}/ /tmp/ic/
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends unzip; \
    mkdir -p /opt/oracle/instantclient; \
    if ls /tmp/ic/*.zip >/dev/null 2>&1; then \
      unzip -q /tmp/ic/*.zip -d /tmp/icx; \
      cp -a /tmp/icx/instantclient_*/. /opt/oracle/instantclient/; \
      echo /opt/oracle/instantclient > /etc/ld.so.conf.d/oracle-instantclient.conf; \
      ldconfig; \
      echo "Instant Client installed for $TARGETARCH"; \
    else \
      echo "no Instant Client zip for $TARGETARCH — thick mode unavailable on this arch"; \
    fi; \
    rm -rf /tmp/ic /tmp/icx; \
    apt-get purge -y unzip; \
    rm -rf /var/lib/apt/lists/*
# thick.py reads LIBERTY_ORACLE_CLIENT_LIB for init_oracle_client(lib_dir=…); LD_LIBRARY_PATH is a belt-and-braces fallback.
ENV LIBERTY_ORACLE_CLIENT_LIB=/opt/oracle/instantclient \
    LD_LIBRARY_PATH=/opt/oracle/instantclient

WORKDIR /app

# Install the framework. Copy the package + build metadata first, install, then drop in the
# built SPA — keeps the (slow) pip layer cached when only frontend assets change.
COPY pyproject.toml ./
COPY liberty/ ./liberty/
RUN pip install --no-cache-dir .

COPY --from=frontend /build/frontend/dist ./frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/liberty-entrypoint
RUN chmod +x /usr/local/bin/liberty-entrypoint

# Default config templates — the entrypoint copies these into /app/config/ on first
# boot if nothing's mounted there. Mirrors what ``./start.sh init-config`` does in the
# dev shell so a brand-new container has a working [pools.default] without the operator
# having to seed any files manually. ``LIBERTY_APPS_DIR`` (when set) bypasses this —
# the external apps repo provides the per-section TOMLs.
COPY config/app.toml             /opt/liberty-defaults/app.toml
COPY config/connectors.toml.example  /opt/liberty-defaults/connectors.toml.example
COPY config/dictionary.toml.example  /opt/liberty-defaults/dictionary.toml.example
COPY config/menus.toml.example       /opt/liberty-defaults/menus.toml.example
COPY config/screens.toml.example     /opt/liberty-defaults/screens.toml.example
COPY config/charts.toml.example      /opt/liberty-defaults/charts.toml.example
COPY config/dashboards.toml.example  /opt/liberty-defaults/dashboards.toml.example

# 0.0.0.0 so the port is reachable from outside the container; SQLite default DB lives in
# /app (override LIBERTY_DB_URL for Postgres). LIBERTY_APPS_DIR unset → the framework reads
# its own ./config (the *.example-seeded open layout); the licensed image sets it.
ENV HOST=0.0.0.0 \
    PORT=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# /info is unauthenticated and reports connector/pool/screen counts — a good liveness probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/info" >/dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/liberty-entrypoint"]
CMD ["serve"]
