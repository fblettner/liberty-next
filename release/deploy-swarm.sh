#!/usr/bin/env bash
#
# Liberty Next — Docker Swarm deploy helper.
#
# ``docker stack deploy`` doesn't support --env-file (it's a known limitation), so this
# script sources .env into the shell first, then runs the stack deploy. That gives the
# compose file's ``${VAR}`` substitutions actual values to substitute.
#
# Usage:
#   ./deploy-swarm.sh                        # deploy / update the 'liberty' stack
#   ./deploy-swarm.sh --stack mystack        # use a different stack name
#   ./deploy-swarm.sh --status               # show current service status (no deploy)
#   ./deploy-swarm.sh --rm                   # tear the stack down (volumes survive)
#
# Re-running deploys is the SAME as updating — Swarm reconciles the service spec against
# what's running and rolls each changed service forward according to its update_config.
# Use this script as both the install and upgrade entry point.
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

STACK="liberty"
ACTION="deploy"
while [ $# -gt 0 ]; do
  case "$1" in
    --stack)  STACK="$2"; shift 2 ;;
    --status) ACTION="status"; shift ;;
    --rm)     ACTION="rm"; shift ;;
    --help|-h) sed -n '4,18p' "$0"; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

COMPOSE_FILE="docker-compose.swarm.yml"
[ -f "$COMPOSE_FILE" ] || die "$COMPOSE_FILE not found in $(pwd)"

# ── status / rm short-circuits ────────────────────────────────────────────────
case "$ACTION" in
  status)
    docker stack services "$STACK"
    exit 0
    ;;
  rm)
    warn "Removing stack '$STACK' (volumes are preserved — use 'docker volume rm' to wipe data)."
    docker stack rm "$STACK"
    ok "Stack removed."
    exit 0
    ;;
esac

# ── prerequisites ─────────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || die "docker is not installed"

# Is the daemon running in swarm mode?
swarm_state="$(docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null || echo "unknown")"
case "$swarm_state" in
  active) ;;
  inactive)
    err "This Docker daemon is NOT a swarm node."
    echo "  Initialise on the manager:"
    echo "    docker swarm init                                # single-node"
    echo "    docker swarm init --advertise-addr <manager-ip>  # multi-node"
    exit 1
    ;;
  pending|error|locked)
    die "Swarm is in '$swarm_state' state — fix that first ('docker swarm leave --force' to reset)."
    ;;
  *)
    die "Could not determine swarm state ('docker info' reported: $swarm_state)"
    ;;
esac

# Are we on a manager? Workers can't deploy stacks.
node_role="$(docker info --format '{{.Swarm.ControlAvailable}}' 2>/dev/null || echo "false")"
[ "$node_role" = "true" ] || die "This node is a worker — run the deploy from a manager."

# ── load .env ─────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  err ".env not found. Generate one first:"
  echo "    ./install.sh prepare        # writes .env with random secrets (no $ chars)"
  exit 1
fi

info "Loading .env into the shell…"
# ``set -a`` auto-exports every var assigned by the source. ``set +a`` flips it off after.
# This is the standard pattern for stack deploy.
set -a
# shellcheck disable=SC1091
. .env
set +a

# Sanity-check the REQUIRED vars are now non-empty (the compose file's ``?`` validators
# will catch this too, but failing here gives a clearer error).
for v in LIBERTY_JWT_SECRET LIBERTY_MASTER_KEY POSTGRES_PASSWORD PGADMIN_PASSWORD; do
  [ -n "${!v:-}" ] || die "$v is empty in .env"
done

# ── deploy ────────────────────────────────────────────────────────────────────
info "Deploying stack '$STACK' from $COMPOSE_FILE…"
# --with-registry-auth forwards the manager's auth tokens to all nodes so the workers
# can pull private images (a no-op when the image is public but harmless either way).
# --prune removes any service that's in the live stack but no longer in the compose file.
docker stack deploy \
  --compose-file "$COMPOSE_FILE" \
  --with-registry-auth \
  --prune \
  --resolve-image always \
  "$STACK"

# ── wait for services to converge ────────────────────────────────────────────
info "Waiting for services to converge…"
deadline=$((SECONDS + 180))
while [ $SECONDS -lt $deadline ]; do
  pending=$(docker stack services "$STACK" --format '{{.Replicas}}' | awk -F/ '$1 != $2' | wc -l | tr -d ' ')
  [ "$pending" = "0" ] && { ok "All services converged."; break; }
  sleep 3
done
[ $SECONDS -ge $deadline ] && warn "Some services didn't converge in 180s — check 'docker stack services $STACK'."

# ── summary ───────────────────────────────────────────────────────────────────
echo
docker stack services "$STACK"
echo

PORT="${TRAEFIK_HTTP_PORT:-80}"
BASE="http://localhost${PORT:+:$PORT}"
[ "$PORT" = "80" ] && BASE="http://<this-host-or-VIP>"

echo "  Liberty:    ${BASE}/"
echo "  ReDoc:      ${BASE}/redoc"
echo "  pgAdmin:    ${BASE}/pgadmin"
echo "  Portainer:  ${BASE}/portainer"
echo "  Traefik:    ${BASE}/traefik       (basic-auth: admin / admin — change in traefik/dynamic/dynamic.yml)"
echo
echo "  Sign in to Liberty as:"
echo "    username:  admin"
echo "    password:  ${LIBERTY_ADMIN_PASSWORD:-<see .env or the first-boot container logs>}"
echo
echo "  Update one service to a new image tag:"
echo "    docker service update --image ghcr.io/fblettner/liberty-next:0.2.0 ${STACK}_liberty-next"
echo "  Roll back:"
echo "    docker service rollback ${STACK}_liberty-next"
echo
echo "  Docs:  https://docs.nomana-it.fr/liberty/getting-started/"
echo
