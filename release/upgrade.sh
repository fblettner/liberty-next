#!/usr/bin/env bash
#
# Liberty Next — upgrade helper.
#
# Pulls the newest image (or a pinned --tag), recreates the running stack, waits for health, and
# reports the version change. The change is also recorded automatically in the UI under
# Settings → History → Upgrades (for BOTH the framework and the licensed apps, when installed) —
# the running container detects the version bump on startup and journals it to the persistent
# config volume. Re-running at the same version is a no-op (nothing to pull, nothing recorded).
#
# Usage:
#   ./upgrade.sh                # pull 'latest' (or whatever LIBERTY_IMAGE_TAG is in .env) + restart
#   ./upgrade.sh --tag 7.0.30   # pin to a specific release, write it to .env, then upgrade
#
# Requires an existing .env (run ./install.sh first). Uses the COMPOSE_FILE recorded there, so the
# same layout / TLS overlays are kept.
set -euo pipefail

if [ -t 1 ]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; BOLD=''; NC=''
fi
info()  { printf "${BLUE}▸${NC} %s\n" "$*"; }
ok()    { printf "${GREEN}✔${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}!${NC} %s\n" "$*"; }
die()   { printf "${RED}✗${NC} %s\n" "$*" >&2; exit 1; }

cd "$(dirname "$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")")"

command -v docker >/dev/null 2>&1 || die "docker is not installed."
docker compose version >/dev/null 2>&1 || die "docker compose v2 is not available."
[ -f .env ] || die ".env not found — run ./install.sh first."

# ── args ────────────────────────────────────────────────────────────────────────
TAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tag) [ -z "${2:-}" ] && die "--tag requires a value (e.g. --tag 7.0.30)"; TAG="$2"; shift 2 ;;
    -h|--help) sed -n '4,20p' "$0"; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

# Pin COMPOSE_FILE from .env so we pull/recreate the SAME layout the operator installed.
COMPOSE_FILE="$(grep -E '^COMPOSE_FILE=' .env | cut -d= -f2- || true)"
[ -z "$COMPOSE_FILE" ] && COMPOSE_FILE="docker-compose.light.yml"
export COMPOSE_FILE

# Read the running framework + apps versions from inside the container (best-effort).
ver_fw()   { docker exec liberty-next python -c "import liberty;print(liberty.__version__)" 2>/dev/null || true; }
ver_apps() { docker exec liberty-next python -c "from importlib.metadata import version,PackageNotFoundError
try: print(version('liberty-apps'))
except PackageNotFoundError: print('')" 2>/dev/null || true; }

OLD_FW="$(ver_fw)"; OLD_APPS="$(ver_apps)"

# ── optional --tag pin (portable .env edit) ──────────────────────────────────────
if [ -n "$TAG" ]; then
  info "Pinning LIBERTY_IMAGE_TAG=$TAG in .env"
  grep -vE '^#? *LIBERTY_IMAGE_TAG=' .env > .env.tmp || true
  printf 'LIBERTY_IMAGE_TAG=%s\n' "$TAG" >> .env.tmp
  mv .env.tmp .env
fi

# ── pull + recreate ──────────────────────────────────────────────────────────────
info "Pulling images (COMPOSE_FILE=${COMPOSE_FILE})…"
docker compose pull
info "Recreating the stack…"
docker compose up -d

info "Waiting for liberty-next to report healthy…"
for i in $(seq 1 60); do
  status=$(docker inspect --format='{{.State.Health.Status}}' liberty-next 2>/dev/null || echo "starting")
  case "$status" in
    healthy)   ok "liberty-next is healthy."; break ;;
    unhealthy) die "liberty-next reported unhealthy — check 'docker compose logs liberty-next'" ;;
  esac
  sleep 2
  [ "$i" -eq 60 ] && warn "still not healthy after 120 s — check 'docker compose logs liberty-next'."
done

NEW_FW="$(ver_fw)"; NEW_APPS="$(ver_apps)"

# ── summary ──────────────────────────────────────────────────────────────────────
echo
printf "${BOLD}━━━ Upgrade complete ━━━${NC}\n"
echo
if [ -n "$NEW_FW" ]; then
  if [ -n "$OLD_FW" ] && [ "$OLD_FW" != "$NEW_FW" ]; then echo "  Framework:  ${OLD_FW} → ${NEW_FW}"; else echo "  Framework:  ${NEW_FW}"; fi
fi
if [ -n "$NEW_APPS" ]; then
  if [ -n "$OLD_APPS" ] && [ "$OLD_APPS" != "$NEW_APPS" ]; then echo "  Apps:       ${OLD_APPS} → ${NEW_APPS}"; else echo "  Apps:       ${NEW_APPS}"; fi
fi
echo
echo "  Version changes are journaled in Settings → History → Upgrades (with the release notes)."
echo
