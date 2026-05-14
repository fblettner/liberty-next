#!/usr/bin/env bash
#
# Liberty Next — run / dev helper.
#
#   ./start.sh            build the frontend if stale, then run FastAPI serving the
#                         React SPA *and* the API on one port (default :8000). This is
#                         the "only FastAPI, routing to the React frontend" mode.
#   ./start.sh dev        same, but with auto-reload (best while iterating on the backend).
#   ./start.sh api        FastAPI only — skip the frontend build (API-only, or serve
#                         whatever's already in frontend/dist).  `./start.sh api dev` adds reload.
#   ./start.sh build      build the frontend into frontend/dist (no server).
#   ./start.sh frontend   run the Vite dev server (:5173, hot reload). It proxies the API
#                         paths to :8000 — run `./start.sh api dev` in another terminal.
#   ./start.sh init-db    bootstrap the auth store + an `admin` user (creates config/auth.toml with
#                         the default `[auth] backend = "toml"`; with `backend = "db"` it creates the
#                         ly2_* tables on the configured pool instead — needs that DB reachable).
#   ./start.sh init-config  copy config/{connectors,dictionary,menus,screens,charts,dashboards}.toml.example → the real files
#                         if they don't exist (these aren't committed — per-deployment / licensed-app
#                         config; run `liberty-migrate` to fill them from a v1 DB). `serve`/`dev` do
#                         this automatically too.
#
# The built React app lives in frontend/dist and is served by FastAPI (liberty/main.py),
# which mounts it at "/" after the API routes — no copying to a "public" folder needed.
# To serve the static files from elsewhere, set [app] static_dir in config/app.toml.
#
# Env overrides: HOST, PORT, VENV  (e.g. PORT=9000 ./start.sh).
set -euo pipefail
cd "$(dirname "$0")"

VENV="${VENV:-.venv}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
PY="$VENV/bin/python"

die() { echo "error: $*" >&2; exit 1; }

[ -x "$PY" ] || die "no virtualenv at '$VENV/' — create it:
  python3.12 -m venv $VENV && $VENV/bin/pip install -e '.[dev]'"

build_frontend() {
  [ -d frontend ] || { echo "(no frontend/ directory — running API-only)"; return 0; }
  command -v npm >/dev/null 2>&1 || die "npm not found — install Node.js (>= 20) to build the frontend"
  (
    cd frontend
    [ -d node_modules ] || { echo "==> installing frontend deps (npm install)…"; npm install; }
    echo "==> building frontend → frontend/dist …"
    npm run build
  )
}

frontend_stale() {
  # Rebuild when dist/ is missing or any source / config file is newer than the build.
  [ -f frontend/dist/index.html ] || return 0
  [ -n "$(find frontend/src frontend/index.html frontend/package.json frontend/vite.config.ts frontend/tsconfig.json \
            -newer frontend/dist/index.html 2>/dev/null)" ]
}

maybe_build_frontend() {
  [ -d frontend ] || return 0
  if frontend_stale; then build_frontend; else echo "==> frontend/dist is up to date"; fi
}

init_config() {  # copy the *.example templates to the real (uncommitted) config files when absent
  local f changed=0
  for f in connectors dictionary menus screens charts dashboards; do
    if [ ! -f "config/$f.toml" ] && [ -f "config/$f.toml.example" ]; then
      cp "config/$f.toml.example" "config/$f.toml"
      echo "==> created config/$f.toml from the template (edit it, or run liberty-migrate to fill it)"
      changed=1
    fi
  done
  [ "$changed" = 1 ] || true
}

run_api() {  # $1 = "dev" → enable --reload
  local extra=()
  [ "${1:-}" = "dev" ] && extra=(--reload)
  init_config
  [ -f config/auth.toml ] || echo "==> config/auth.toml missing — no users yet. Run: ./start.sh init-db   (bootstraps an 'admin')"
  [ -n "${LIBERTY_JWT_SECRET:-}" ] || echo "==> LIBERTY_JWT_SECRET unset → ephemeral JWT key (tokens won't survive a restart)"
  echo "==> FastAPI on http://$HOST:$PORT   (SPA: /   API: /api/…   docs: /docs)"
  exec "$PY" -m uvicorn liberty.main:app --host "$HOST" --port "$PORT" "${extra[@]}"
}

cmd="${1:-serve}"
case "$cmd" in
  serve)              maybe_build_frontend; run_api ;;
  dev)                maybe_build_frontend; run_api dev ;;
  api)                run_api "${2:-}" ;;
  build)              build_frontend ;;
  frontend)
    [ -d frontend ] || die "no frontend/ directory"
    command -v npm >/dev/null 2>&1 || die "npm not found"
    cd frontend
    [ -d node_modules ] || npm install
    exec npm run dev
    ;;
  init-db)            shift; exec "$VENV/bin/liberty-admin" init-db "$@" ;;
  init-config)        init_config; echo "done." ;;
  -h|--help|help)
    grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'
    ;;
  *)
    die "unknown command '$cmd' — try: serve | dev | api | build | frontend | init-db | init-config | help"
    ;;
esac
