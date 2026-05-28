#!/usr/bin/env bash
#
# Container entrypoint for the liberty-next image (and, by inheritance, the licensed
# liberty-apps image). Bootstraps the framework DB on `serve`, then execs uvicorn.
#
#   serve            (default) — init-db (idempotent), then run FastAPI + Socket.IO.
#   <anything else>            — exec it verbatim (so `docker run … liberty-admin create-user …`
#                                or `… bash` work for one-off admin / debugging).
#
# Env:
#   PORT, HOST                — bind address (Dockerfile defaults 0.0.0.0:8000).
#   LIBERTY_DB_URL            — framework pool (auth + nomaflow). Default: SQLite in /app.
#   LIBERTY_ADMIN_PASSWORD    — when set, the bootstrap admin gets this password
#                               (else init-db generates a random one and prints it once).
#   LIBERTY_SKIP_INITDB=1     — skip the bootstrap (e.g. a read-replica / scaled-out worker
#                               where another instance already ran it).
#   LIBERTY_DB_WAIT_TRIES     — how many 1s probes to wait for the framework DB before
#                               init-db (default 30; 0 disables the wait — fine for SQLite).
set -euo pipefail

log() { echo "[entrypoint] $*"; }

wait_for_db() {
  # Only meaningful for a networked DB. SQLite (the default) needs no wait — the URL has no
  # host, so we skip. We probe with a tiny Python connect against the framework pool URL.
  local url="${LIBERTY_DB_URL:-}"
  case "$url" in
    ""|sqlite*) return 0 ;;
  esac
  local tries="${LIBERTY_DB_WAIT_TRIES:-30}"
  [ "$tries" -eq 0 ] 2>/dev/null && return 0
  log "waiting for framework DB (up to ${tries}s)…"
  for ((i = 1; i <= tries; i++)); do
    if python - "$url" <<'PY' 2>/dev/null
import sys, asyncio
from sqlalchemy.ext.asyncio import create_async_engine
async def main(u):
    e = create_async_engine(u)
    async with e.connect():
        pass
    await e.dispose()
asyncio.run(main(sys.argv[1]))
PY
    then
      log "framework DB reachable."
      return 0
    fi
    sleep 1
  done
  log "warning: framework DB still unreachable after ${tries}s — init-db will likely fail."
  return 0
}

serve() {
  if [ "${LIBERTY_SKIP_INITDB:-}" != "1" ]; then
    wait_for_db
    local args=()
    [ -n "${LIBERTY_ADMIN_PASSWORD:-}" ] && args+=(--password-env LIBERTY_ADMIN_PASSWORD)
    # init-db is idempotent: re-running with users present is a no-op (it reports the
    # existing admin and ensures the nomaflow_* tables exist on the framework pool).
    log "bootstrapping auth + nomaflow schema (liberty-admin init-db)…"
    liberty-admin init-db "${args[@]}" || log "init-db reported a problem (continuing to serve)."
  else
    log "LIBERTY_SKIP_INITDB=1 — skipping bootstrap."
  fi
  log "starting FastAPI + Socket.IO on ${HOST:-0.0.0.0}:${PORT:-8000}"
  exec uvicorn liberty.main:asgi_app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
}

case "${1:-serve}" in
  serve) serve ;;
  *)     exec "$@" ;;
esac
