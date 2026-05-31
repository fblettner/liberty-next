#!/usr/bin/env bash
#
# Liberty Next — install the licensed apps on top of a running liberty-next stack.
#
# What this does (no git clone — uses the licensed wheel you were delivered):
#   1. Materializes the wheel's payload (config + plugins) into ./apps/ via a throwaway
#      python:3.12-slim container — your host doesn't need python or pip.
#   2. Updates .env: APPS_HOST_PATH + COMPOSE_FILE (adds the apps overlay).
#   3. Restarts the stack so liberty-next picks up the new mount.
#   4. Runs install-time jobs (deploy-databases / init-db / import-reference …).
#
# Usage:
#   ./install-apps.sh ./liberty_apps-7.0.1-py3-none-any.whl                    # local wheel
#   ./install-apps.sh https://your-license-host/liberty_apps-7.0.1.whl        # remote wheel (curl)
#   ./install-apps.sh ./liberty_apps-X.Y.Z.whl --target /opt/liberty-apps     # custom destination
#   ./install-apps.sh ./liberty_apps-X.Y.Z.whl --layout light                 # layer onto light stack
#
# Set the license key AFTER install via the UI: Settings → App → License → Set.
# (The key is encrypted at rest in app.toml with the install's LIBERTY_MASTER_KEY.)
#
# Re-running is idempotent: rematerializes the wheel (operator edits are preserved
# unless you pass --force-config), refreshes .env keys, re-applies compose.
set -euo pipefail

if [ -t 1 ]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; BOLD=''; NC=''
fi
info()  { printf "${BLUE}▸${NC} %s\n" "$*"; }
ok()    { printf "${GREEN}✔${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}!${NC} %s\n" "$*"; }
err()   { printf "${RED}✗${NC} %s\n" "$*" >&2; }
die()   { err "$*"; exit 1; }

cd "$(dirname "$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")")"

# ── args ──────────────────────────────────────────────────────────────────────
WHEEL=""
APPS_PATH="./apps"
LAYOUT="full"
FORCE_CONFIG="no"

while [ $# -gt 0 ]; do
  case "$1" in
    --target)        [ -z "${2:-}" ] && die "--target requires a value"; APPS_PATH="$2"; shift 2 ;;
    --layout)        [ -z "${2:-}" ] && die "--layout requires a value (full|light)"; LAYOUT="$2"; shift 2 ;;
    --force-config)  FORCE_CONFIG="yes"; shift ;;
    --license-key)
      die "--license-key was removed — set the license via Settings → App → License after install (encrypted at rest in app.toml)." ;;
    --help|-h)       sed -n '4,23p' "$0"; exit 0 ;;
    -*)              die "Unknown flag: $1" ;;
    *)               [ -n "$WHEEL" ] && die "Wheel specified twice: '$WHEEL' and '$1'"
                     WHEEL="$1"; shift ;;
  esac
done

[ -n "$WHEEL" ] || die "Usage: $0 <wheel-path-or-url> [--target DIR] [--layout full|light]"
case "$LAYOUT" in light|full) ;; *) die "--layout must be 'light' or 'full' (got: $LAYOUT)" ;; esac

# ── prerequisites ─────────────────────────────────────────────────────────────
command -v docker >/dev/null || die "docker is not installed"
[ -f .env ] || die ".env not found. Run ./install.sh ${LAYOUT} first to bring up the base stack."
[ -f "docker-compose.${LAYOUT}.yml" ] || die "docker-compose.${LAYOUT}.yml not found"
[ -f "docker-compose.apps.yml"        ] || die "docker-compose.apps.yml not found"

# ── 1. fetch the wheel (if URL) into a local file ────────────────────────────
WHEEL_LOCAL=""
WHEEL_TMP=""
case "$WHEEL" in
  http://*|https://*)
    WHEEL_TMP="$(mktemp -d)/$(basename "${WHEEL%%\?*}")"
    info "Downloading $WHEEL → $WHEEL_TMP"
    if ! curl -fsSL "$WHEEL" -o "$WHEEL_TMP"; then
      die "Download failed. If the URL is auth-gated, pre-download the wheel and pass the local path."
    fi
    WHEEL_LOCAL="$WHEEL_TMP"
    ;;
  *)
    [ -f "$WHEEL" ] || die "Wheel not found: $WHEEL"
    WHEEL_LOCAL="$(cd "$(dirname "$WHEEL")" && pwd)/$(basename "$WHEEL")"
    ;;
esac
case "$WHEEL_LOCAL" in *.whl) ;; *) die "Not a wheel file: $WHEEL_LOCAL (expected *.whl)" ;; esac

# ── 2. materialize the wheel into $APPS_PATH ─────────────────────────────────
# Run pip inside a throwaway python:3.12-slim container. The container mounts the
# wheel read-only + the destination read-write, installs the wheel into a tmp prefix,
# then invokes the liberty-apps CLI which copies config/ and plugins/ into $APPS_PATH.
# Nothing python-related is left on the host afterwards.
APPS_ABS="$(mkdir -p "$APPS_PATH" && cd "$APPS_PATH" && pwd)"
info "Materializing wheel into $APPS_ABS via a throwaway python:3.12-slim container…"

EXTRA_FLAGS=""
[ "$FORCE_CONFIG" = "yes" ] && EXTRA_FLAGS="--force-config"

WHEEL_DIR="$(dirname "$WHEEL_LOCAL")"
WHEEL_NAME="$(basename "$WHEEL_LOCAL")"

docker run --rm \
  -v "${WHEEL_DIR}":/wheel:ro \
  -v "${APPS_ABS}":/dest \
  python:3.12-slim \
  bash -c "
    set -e
    pip install --quiet --no-cache-dir --root-user-action=ignore '/wheel/${WHEEL_NAME}'
    liberty-apps install --target /dest/config ${EXTRA_FLAGS}
  "

# Clean up downloaded wheel if we fetched it
[ -n "$WHEEL_TMP" ] && rm -rf "$(dirname "$WHEEL_TMP")"

[ -d "$APPS_ABS/config" ] || die "Materialization didn't produce $APPS_ABS/config — check the wheel."
[ -d "$APPS_ABS/plugins" ] || warn "$APPS_ABS/plugins not found — wheel may not include plugins."
ok "Apps materialized in $APPS_ABS (config/ + plugins/)"

# ── 3. update .env ────────────────────────────────────────────────────────────
upsert_env() {
  local key="$1" val="$2"
  if grep -qE "^${key}=" .env; then
    sed -i.bak "s|^${key}=.*|${key}=${val}|" .env && rm -f .env.bak
    info "Updated ${key} in .env"
  else
    printf '\n%s=%s\n' "$key" "$val" >> .env
    info "Added ${key} to .env"
  fi
}
upsert_env "APPS_HOST_PATH" "$APPS_ABS"

# Append docker-compose.apps.yml to COMPOSE_FILE (colon-separated) so every
# subsequent ``docker compose <command>`` automatically includes the overlay.
add_compose_overlay() {
  local overlay="docker-compose.apps.yml"
  local current
  current="$(grep -E '^COMPOSE_FILE=' .env | head -1 | cut -d= -f2-)"
  [ -z "$current" ] && current="docker-compose.${LAYOUT}.yml"
  case ":$current:" in
    *":$overlay:"*) info "COMPOSE_FILE already includes $overlay." ;;
    *)              upsert_env "COMPOSE_FILE" "${current}:${overlay}" ;;
  esac
}
add_compose_overlay
chmod 600 .env

# ── 4. restart the stack ──────────────────────────────────────────────────────
# Read the merged COMPOSE_FILE back out of .env and pass it via shell env (compose
# v2's project discovery checks cwd for compose.yaml BEFORE reading .env, so we
# can't rely on .env's COMPOSE_FILE alone here).
COMPOSE_FILE_ENV="$(grep -E '^COMPOSE_FILE=' .env | head -1 | cut -d= -f2-)"
info "Re-applying stack (COMPOSE_FILE=${COMPOSE_FILE_ENV})…"
COMPOSE_FILE="$COMPOSE_FILE_ENV" docker compose up -d

info "Waiting for liberty-next to report healthy…"
for i in $(seq 1 60); do
  status=$(docker inspect --format='{{.State.Health.Status}}' liberty-next 2>/dev/null || echo "starting")
  case "$status" in
    healthy) ok "liberty-next is healthy."; break ;;
    unhealthy) err "liberty-next reported unhealthy — check 'docker compose logs liberty-next'"; exit 1 ;;
  esac
  sleep 2
  [ "$i" -eq 60 ] && warn "liberty-next not healthy after 120 s — continuing; check logs."
done

# ── 5. run install-time jobs (deploy-databases, init-db, import-reference, …) ─
# Every job tagged ``install_step = N`` in the apps' jobs.toml fires here in
# order. Idempotent: jobs with a prior SUCCEEDED run are skipped (re-running
# the installer on an already-deployed host is a no-op for the jobs). Pass
# --force to liberty-admin to re-run everything.
info "Running install-time jobs (deploy-databases / init-db / init-schema / import-reference …)…"
if ! docker exec liberty-next liberty-admin run-install-jobs; then
  err "Install jobs failed — see the per-job output above. Recover with:"
  err "   docker exec liberty-next liberty-admin run-install-jobs           (skips done jobs)"
  err "   docker exec liberty-next liberty-admin run-install-jobs --force   (re-runs everything)"
  err "   docker logs liberty-next 2>&1 | tail -200                         (more context)"
  exit 1
fi
ok "Install jobs completed."

# ── summary ───────────────────────────────────────────────────────────────────
echo
printf "${BOLD}━━━ Liberty Apps installed ━━━${NC}\n"
echo
echo "  Apps host path:   $APPS_ABS"
echo "  Container path:   /apps"
echo "  LIBERTY_APPS_DIR: /apps/config"
echo
echo "  Next: set the license key via the UI (encrypted at rest in app.toml):"
echo "    open http://localhost/             # Settings → App → License → Set"
echo "  Without a license key, licensed connectors stay in restricted mode."
echo
echo "  Verify in the UI:"
echo "    open http://localhost/info         # 'license.mode' + 'connectors.licensed' count"
echo
echo "  Refresh the apps (after receiving a new wheel):"
echo "    ./install-apps.sh /path/to/liberty_apps-NEW.whl"
echo
echo "  Documentation: https://docs.nomana-it.fr/liberty/getting-started/"
echo
