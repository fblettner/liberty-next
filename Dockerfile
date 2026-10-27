# Liberty Next — the open framework image (public).
#
# Builds the React SPA, then a slim Python runtime that serves the SPA *and* the API on
# one port (FastAPI + Socket.IO, exactly what ./start.sh runs). This image carries NO
# licensed content — it runs as the open framework (restricted mode) with whatever config
# the operator mounts at /app/config (or via LIBERTY_APPS_DIR). The licensed product is a
# separate wheel (liberty-apps: config + plugins) materialised next to this image and
# bind-mounted in by release/install-apps.sh; distribution of that wheel is the license gate.
#
#   docker build -t liberty-next:local .
#   docker run --rm -p 8000:8000 liberty-next:local        # → open framework on :8000
#
# Runtime DB defaults to SQLite (./liberty.db) so the bare image runs with no external DB.
# Point LIBERTY_DB_URL at Postgres for a real deployment (see docker-compose.yml).

# ── Stage 1 — build the frontend (Vite → frontend/dist) ──────────────────────────────
FROM node:26.12.1-slim AS frontend
WORKDIR /build/frontend
# Copy only the manifest + lockfile first so the install layer is cached across source-only
# edits. `npm ci` installs exactly the committed lockfile and FAILS if package.json and
# package-lock.json drift — which is what we want in an image build. (Until 2026-10-26 this
# was `npm install`: the lockfile carried an internally inconsistent yaml@1 entry that
# npm 10 rejected, and `npm install` silently repaired it on every build. The lock was
# regenerated; CI now runs the same `npm ci`.) --no-audit/--no-fund keep the log clean.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build   # emits /build/frontend/dist (vite.config.ts: outDir "dist")

# ── Stage 2 — Python runtime (FastAPI + Socket.IO, serving the built SPA) ─────────────
FROM python:3.15.0-slim AS runtime

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
# FETCHED AT BUILD TIME from Oracle's public download (no login since 19.x; ~75 MB per arch)
# and verified against a pinned SHA-256 — the 146 MB of zips used to be committed in git.
# Air-gapped build: drop the matching **Linux** Basic ZIP into docker/instantclient/<arch>/
# (gitignored) and it is used instead of the download. An arch without a client still builds;
# thick mode is then unavailable there (a clean ThickFetchError at runtime). Match the client
# to your Oracle DB: a 19c client connects to 11.2 … 23ai; a 23ai client to 19c+ only.
ARG TARGETARCH
ARG INSTANTCLIENT_VERSION=19.31.0.0.0
ARG INSTANTCLIENT_SHA256_AMD64=50007f6c5146882291530b68034ff0ed471d95e675b6bde2ad5cb1cae4ef892c
ARG INSTANTCLIENT_SHA256_ARM64=e71f91749d7d3446e37c767370c169acf65c2bd6485e8f8456f3a72d848ff3f6
COPY docker/instantclient/${TARGETARCH}/ /tmp/ic/
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends unzip; \
    mkdir -p /opt/oracle/instantclient /tmp/icx; \
    case "$TARGETARCH" in \
      amd64) oarch=x64;   sha="$INSTANTCLIENT_SHA256_AMD64" ;; \
      arm64) oarch=arm64; sha="$INSTANTCLIENT_SHA256_ARM64" ;; \
      *)     oarch="";    sha="" ;; \
    esac; \
    zip="$(ls /tmp/ic/*.zip 2>/dev/null | head -1 || true)"; \
    if [ -z "$zip" ] && [ -n "$oarch" ]; then \
      ver_path="$(echo "$INSTANTCLIENT_VERSION" | awk -F. '{printf "%s%s000", $1, $2}')"; \
      url="https://download.oracle.com/otn_software/linux/instantclient/${ver_path}/instantclient-basic-linux.${oarch}-${INSTANTCLIENT_VERSION}dbru.zip"; \
      echo "fetching $url"; \
      curl -fsSL --retry 3 -o /tmp/ic/client.zip "$url"; \
      echo "$sha  /tmp/ic/client.zip" | sha256sum -c -; \
      zip=/tmp/ic/client.zip; \
    fi; \
    if [ -n "$zip" ]; then \
      unzip -q "$zip" -d /tmp/icx; \
      cp -a /tmp/icx/instantclient_*/. /opt/oracle/instantclient/; \
      echo /opt/oracle/instantclient > /etc/ld.so.conf.d/oracle-instantclient.conf; \
      ldconfig; \
      echo "Instant Client ${INSTANTCLIENT_VERSION} installed for $TARGETARCH"; \
    else \
      echo "no Instant Client for $TARGETARCH — thick mode unavailable on this arch"; \
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
COPY pyproject.toml requirements.txt ./
COPY liberty/ ./liberty/
# requirements.txt is the pip-compile lock of `.[all]` (every extra: reports, ldap, alembic) —
# the image, CI and the dev venv resolve to the same versions. Refresh it with:
#   pip-compile --extra all --strip-extras --output-file requirements.txt pyproject.toml
RUN pip install --no-cache-dir -c requirements.txt ".[all]"

COPY --from=frontend /build/frontend/dist ./frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/liberty-entrypoint
RUN chmod +x /usr/local/bin/liberty-entrypoint
# The app runs as `liberty` (uid/gid 1000), not root. The image still STARTS as root: the
# entrypoint fixes the ownership of the mounted volumes (a volume created by an older,
# root-running release; the apps bind mount written by the installer), then drops to this
# user with setpriv (util-linux). LIBERTY_RUN_AS_ROOT=1 opts out. `docker exec` therefore
# runs as root unless you pass `-u liberty` — release/liberty does.
RUN groupadd --gid 1000 liberty && useradd --uid 1000 --gid 1000 --home-dir /app --no-create-home --shell /usr/sbin/nologin liberty \
    && mkdir -p /app/config /data && chown -R liberty:liberty /app /data
ENV LIBERTY_USER=liberty

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
    CMD curl -fsS "http://127.0.0.1:${PORT}/ready" >/dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/liberty-entrypoint"]
CMD ["serve"]
