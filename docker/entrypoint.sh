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
#   LIBERTY_AUTO_MIGRATE=1    — apply the apps' pending schema migrations at boot (default: only
#                               warn; `liberty upgrade` applies them).
#   <VAR>_FILE                — for any LIBERTY_* / POSTGRES_* variable: read its value from this
#                               file (Docker / Swarm / Kubernetes secrets) when <VAR> itself is unset.
#   LIBERTY_RUN_AS_ROOT=1     — keep running as root (default: fix the mounts' ownership, then drop to
#                               the `liberty` user, uid 1000).
#   LIBERTY_SKIP_INITDB=1     — skip the bootstrap (e.g. a read-replica / scaled-out worker
#                               where another instance already ran it).
#   LIBERTY_DB_WAIT_TRIES     — how many 1s probes to wait for the framework DB before
#                               init-db (default 30; 0 disables the wait — fine for SQLite).
set -euo pipefail

log() { echo "[entrypoint] $*"; }

load_file_secrets() {
  # VAR_FILE=/run/secrets/x → VAR=<content> (when VAR is unset). Only LIBERTY_* / POSTGRES_* /
  # PGADMIN_* names, so an unrelated *_FILE variable is never read as a secret.
  local name file
  while IFS='=' read -r name _; do
    case "$name" in LIBERTY_*_FILE|POSTGRES_*_FILE|PGADMIN_*_FILE) ;; *) continue ;; esac
    file="${!name}"
    local var="${name%_FILE}"
    if [ -z "${!var:-}" ] && [ -n "$file" ]; then
      if [ -r "$file" ]; then
        export "$var"="$(cat "$file")"
        log "$var read from $file"
      else
        log "warning: $name=$file is not readable — $var stays unset"
      fi
    fi
  done < <(env)
}

own_mounts() {
  # Volumes created by a root-running release and bind mounts written by the installer are
  # root-owned; the app (uid 1000) must be able to write its config, data and journal.
  local d
  for d in /app/config /data "${LIBERTY_APPS_DIR:+$(dirname "$LIBERTY_APPS_DIR")}"; do
    [ -n "$d" ] && [ -d "$d" ] || continue
    if [ "$(stat -c %u "$d")" != "1000" ]; then
      log "fixing ownership of $d for ${LIBERTY_USER:-liberty}…"
      chown -R 1000:1000 "$d" 2>/dev/null || log "warning: could not chown $d (read-only mount?)"
    fi
  done
}

drop_privileges() {
  # Re-exec this script as `liberty` once the mounts are owned by it. setpriv is util-linux
  # (present in python:*-slim); --init-groups resets supplementary groups.
  if [ "$(id -u)" = "0" ] && [ "${LIBERTY_RUN_AS_ROOT:-}" != "1" ] && command -v setpriv >/dev/null 2>&1; then
    own_mounts
    export HOME=/app
    exec setpriv --reuid=1000 --regid=1000 --init-groups "$0" "$@"
  fi
}

seed_config() {
  # Populate /app/config/ from the image's default templates on first boot. Mirrors
  # ``./start.sh init-config`` — only creates files that don't already exist, so an
  # upgrade with a populated config volume never overwrites the operator's edits.
  # When LIBERTY_APPS_DIR is set, an external apps repo provides every per-section
  # TOML (the licensed-customer pattern), so we skip the seed.
  if [ -n "${LIBERTY_APPS_DIR:-}" ]; then
    log "LIBERTY_APPS_DIR=${LIBERTY_APPS_DIR} — skipping local config seed."
    return 0
  fi
  local defaults="/opt/liberty-defaults" target="/app/config"
  if [ ! -d "$defaults" ]; then
    log "no $defaults — image was built without seed templates."
    return 0
  fi
  mkdir -p "$target"
  local seeded=0 src base
  for src in "$defaults"/*; do
    [ -f "$src" ] || continue
    base="$(basename "$src")"
    # connectors.toml.example → connectors.toml ; app.toml stays app.toml
    base="${base%.example}"
    if [ ! -f "$target/$base" ]; then
      cp "$src" "$target/$base"
      log "seeded $target/$base"
      seeded=$((seeded + 1))
    fi
  done
  if [ "$seeded" -gt 0 ]; then
    log "seeded $seeded config file(s) from $defaults — edit through Settings → … or by mounting /app/config to your own files."
  fi
}

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

check_migrations() {
  # The licensed apps ship Alembic migrations (plugins/<app>/db). They are applied by
  # `liberty upgrade` / `liberty-admin migrate`; at boot we only SAY when some are pending —
  # unless LIBERTY_AUTO_MIGRATE=1 asks for them to be applied right here.
  if [ "${LIBERTY_AUTO_MIGRATE:-}" = "1" ]; then
    log "applying pending app schema migrations (LIBERTY_AUTO_MIGRATE=1)…"
    liberty-admin migrate || log "warning: liberty-admin migrate reported a problem."
    return 0
  fi
  local rc=0
  liberty-admin migrate --dry-run >/dev/null 2>&1 || rc=$?
  case "$rc" in
    0) ;;
    2) log "WARNING: app schema migrations are PENDING — run './liberty migrate' (or 'liberty-admin migrate' in the container; LIBERTY_AUTO_MIGRATE=1 applies them at boot)." ;;
    *) log "warning: could not check the app schema migrations (liberty-admin migrate --dry-run exited $rc)." ;;
  esac
}

serve() {
  seed_config
  if [ "${LIBERTY_SKIP_INITDB:-}" != "1" ]; then
    wait_for_db
    local args=()
    [ -n "${LIBERTY_ADMIN_PASSWORD:-}" ] && args+=(--password-env LIBERTY_ADMIN_PASSWORD)
    # init-db is idempotent: re-running with users present is a no-op (it reports the
    # existing admin and ensures the nomaflow_* tables exist on the framework pool).
    log "bootstrapping auth + nomaflow schema (liberty-admin init-db)…"
    liberty-admin init-db "${args[@]}" || log "init-db reported a problem (continuing to serve)."
    check_migrations
  else
    log "LIBERTY_SKIP_INITDB=1 — skipping bootstrap."
  fi
  log "starting FastAPI + Socket.IO on ${HOST:-0.0.0.0}:${PORT:-8000}"
  exec uvicorn liberty.main:asgi_app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
}

load_file_secrets
case "${1:-serve}" in
  serve) drop_privileges "$@"; serve ;;
  *)     exec "$@" ;;
esac
