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

# curl: container HEALTHCHECK hits /info. No DB client libs needed — asyncpg (Postgres),
# oracledb (thin mode, no Oracle client), and psycopg2-binary (Alembic's sync runner) all
# ship self-contained wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the framework. Copy the package + build metadata first, install, then drop in the
# built SPA — keeps the (slow) pip layer cached when only frontend assets change.
COPY pyproject.toml ./
COPY liberty/ ./liberty/
RUN pip install --no-cache-dir .

COPY --from=frontend /build/frontend/dist ./frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/liberty-entrypoint
RUN chmod +x /usr/local/bin/liberty-entrypoint

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
