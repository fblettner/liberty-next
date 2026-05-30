#!/usr/bin/env bash
#
# Liberty Next — install the licensed apps on top of a running liberty-next stack.
#
# What this does:
#   1. Clones (or reuses) the liberty-apps repo on the host.
#   2. Updates .env with APPS_HOST_PATH (absolute path to the clone) + optionally
#      LIBERTY_LICENSE_KEY.
#   3. Restarts the stack with docker-compose.apps.yml layered on top, which mounts
#      the apps into /apps inside the container and sets LIBERTY_APPS_DIR=/apps/config.
#
# Usage:
#   ./install-apps.sh                              # clones to ./apps/ (default)
#   ./install-apps.sh --path /opt/liberty-apps     # use an existing clone
#   ./install-apps.sh --license-key <jwt>          # also set LIBERTY_LICENSE_KEY in .env
#   ./install-apps.sh --layout full                # which base compose to layer onto (full|light)
#   ./install-apps.sh --repo <url>                 # override the git URL (default: github.com/fblettner/liberty-apps)
#
# Re-running is idempotent: keeps your clone, refreshes .env keys, re-applies compose.
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
APPS_PATH="./apps"
LICENSE_KEY=""
LAYOUT="full"
REPO_URL="https://github.com/fblettner/liberty-apps.git"

while [ $# -gt 0 ]; do
  case "$1" in
    --path)         [ -z "${2:-}" ] && die "--path requires a value"; APPS_PATH="$2"; shift 2 ;;
    --license-key)  [ -z "${2:-}" ] && die "--license-key requires a value"; LICENSE_KEY="$2"; shift 2 ;;
    --layout)       [ -z "${2:-}" ] && die "--layout requires a value (full|light)"; LAYOUT="$2"; shift 2 ;;
    --repo)         [ -z "${2:-}" ] && die "--repo requires a URL"; REPO_URL="$2"; shift 2 ;;
    --help|-h)      sed -n '4,18p' "$0"; exit 0 ;;
    *)              die "Unknown argument: $1" ;;
  esac
done

case "$LAYOUT" in light|full) ;; *) die "--layout must be 'light' or 'full' (got: $LAYOUT)" ;; esac

# ── prerequisites ─────────────────────────────────────────────────────────────
command -v docker >/dev/null || die "docker is not installed"
command -v git    >/dev/null || die "git is not installed (needed to clone liberty-apps)"
[ -f .env ] || die ".env not found. Run ./install.sh ${LAYOUT} first to bring up the base stack."
[ -f "docker-compose.${LAYOUT}.yml" ] || die "docker-compose.${LAYOUT}.yml not found"
[ -f "docker-compose.apps.yml"        ] || die "docker-compose.apps.yml not found"

# ── 1. clone or reuse the apps repo ───────────────────────────────────────────
if [ -d "$APPS_PATH/.git" ]; then
  info "Reusing existing liberty-apps clone at $APPS_PATH"
elif [ -d "$APPS_PATH" ] && [ -n "$(ls -A "$APPS_PATH" 2>/dev/null)" ]; then
  warn "$APPS_PATH exists but isn't a git clone — using it as-is (won't pull updates)."
else
  info "Cloning $REPO_URL → $APPS_PATH"
  echo "  (the repo is PRIVATE — you'll need read access via SSH key or PAT)"
  if ! git clone "$REPO_URL" "$APPS_PATH"; then
    die "Clone failed. If the repo is private, set up SSH access:
       git remote set-url origin git@github.com:fblettner/liberty-apps.git
     Or pass --repo with an https URL that includes your PAT:
       ./install-apps.sh --repo https://<token>@github.com/fblettner/liberty-apps.git"
  fi
fi
APPS_ABS="$(cd "$APPS_PATH" && pwd)"
[ -d "$APPS_ABS/config" ] || die "$APPS_ABS/config not found — is this really a liberty-apps clone?"

# ── 2. update .env ────────────────────────────────────────────────────────────
upsert_env() {
  local key="$1" val="$2"
  if grep -qE "^${key}=" .env; then
    # Use a different sed delimiter so paths with / don't need escaping.
    sed -i.bak "s|^${key}=.*|${key}=${val}|" .env && rm -f .env.bak
    info "Updated ${key} in .env"
  else
    printf '\n%s=%s\n' "$key" "$val" >> .env
    info "Added ${key} to .env"
  fi
}
upsert_env "APPS_HOST_PATH" "$APPS_ABS"
if [ -n "$LICENSE_KEY" ]; then
  upsert_env "LIBERTY_LICENSE_KEY" "$LICENSE_KEY"
else
  if ! grep -qE '^LIBERTY_LICENSE_KEY=.+$' .env; then
    warn "LIBERTY_LICENSE_KEY not set in .env — the framework will start in RESTRICTED mode (licensed connectors won't load)."
    echo "  Add it later:  ./install-apps.sh --license-key <jwt>"
  fi
fi
chmod 600 .env

# ── 3. restart with the apps overlay ──────────────────────────────────────────
info "Re-applying stack with docker-compose.${LAYOUT}.yml + docker-compose.apps.yml…"
docker compose \
  -f "docker-compose.${LAYOUT}.yml" \
  -f docker-compose.apps.yml \
  up -d

info "Waiting for liberty-next to report healthy…"
for i in $(seq 1 60); do
  status=$(docker inspect --format='{{.State.Health.Status}}' liberty-next 2>/dev/null || echo "starting")
  case "$status" in
    healthy) ok "liberty-next is healthy."; break ;;
    unhealthy) err "liberty-next reported unhealthy — check 'docker compose -f docker-compose.${LAYOUT}.yml logs liberty-next'"; exit 1 ;;
  esac
  sleep 2
  [ "$i" -eq 60 ] && warn "liberty-next not healthy after 120 s — continuing; check logs."
done

# ── summary ───────────────────────────────────────────────────────────────────
echo
printf "${BOLD}━━━ Liberty Apps mounted ━━━${NC}\n"
echo
echo "  Apps host path:   $APPS_ABS"
echo "  Container path:   /apps"
echo "  LIBERTY_APPS_DIR: /apps/config"
if grep -qE '^LIBERTY_LICENSE_KEY=.+$' .env; then
  echo "  License key:      set (licensed connectors loaded if the key is valid)"
else
  echo "  License key:      NOT set → restricted mode (only the open framework runs)"
fi
echo
echo "  Verify in the UI:"
echo "    open http://localhost/             # the SPA should now show the apps' menus"
echo "    open http://localhost/info         # 'license.mode' + 'connectors.licensed' count"
echo
echo "  Refresh the apps (after a git pull on the host clone):"
echo "    cd $APPS_ABS && git pull"
echo "    docker compose -f docker-compose.${LAYOUT}.yml -f docker-compose.apps.yml restart liberty-next"
echo
echo "  Documentation: https://docs.nomana-it.fr/liberty/getting-started/"
echo
