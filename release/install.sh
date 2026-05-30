#!/usr/bin/env bash
#
# Liberty Next — install helper.
#
# Generates .env with secure random secrets (no ``$`` chars, no compose-substitution
# footguns), then brings up the chosen Docker Compose layout. Refuses to overwrite an
# existing .env — re-runs are idempotent only when the .env is already in place.
#
# Usage:
#   ./install.sh                # interactive — asks light vs full
#   ./install.sh light          # single container, SQLite
#   ./install.sh full           # 5 services (Traefik + pg + pgadmin + portainer)
#   ./install.sh prepare        # generate .env only — don't start anything
#
# Image tag selector (any of the layouts above):
#   --tag <version>             # pin to a specific release (e.g. 7.0.2)
# Without it, install.sh uses 'latest' — which is the most recent release (every
# merge to main creates a new release, so 'latest' always reflects current main).
#
# Fresh-start flag (DESTRUCTIVE, WIPE-AND-EXIT):
#   --reset                     # docker compose down -v + delete .env, then EXIT.
# Use when a previous run left stale named volumes (pg-data with the old password,
# pgadmin-data with the old admin login, …). Re-run install.sh AFTER --reset with
# the full flag set you actually want — --reset deliberately does NOT auto-install
# because earlier behaviour silently dropped the other flags into the void.
#
# Licensed apps (single-command install):
#   --apps <wheel-path-or-url>  # after bringing up the base stack, run install-apps.sh
#                                 with the given wheel (local file or http(s) URL).
#   --license-key <jwt>         # forwarded to install-apps.sh if --apps is set.
# Lets you do everything in one go:
#   ./install.sh full --apps ./liberty_apps-7.0.1.whl --license-key <jwt>
#
# Master key (encrypts/decrypts ENC: values in connectors.toml etc.):
#   --master-key <value>        # use this value instead of a freshly-generated random.
# Without it, install.sh generates a random 32-char master key (good for greenfield
# installs). Use --master-key when you need to import data that was already encrypted
# with a specific key (e.g. liberty-apps wheel's ENC: passwords, exports from another
# install). Re-running install.sh with a new --master-key on an existing install
# UPDATES .env + force-recreates liberty-next so the new value takes effect.
#
# TLS (full layout only — light has no Traefik):
#   --ssl letsencrypt --domain <hostname> --email <addr>
#       Auto-fetched cert via Let's Encrypt (ACME TLS-ALPN challenge — needs the host
#       reachable from the public internet on :80/:443). Recommended for demo servers.
#   --ssl provided --cert-dir <dir> --cert-file <crt> --key-file <key>
#       Operator-provided cert (corporate CA, internal PKI). install.sh copies/uses
#       the cert files from <dir> and writes traefik/dynamic/tls.yml referencing them.
#   --ssl none (default)
#       HTTP only on :80.
#
# Re-running:
#   .env already present + stack up → no-op (use ``docker compose pull && up -d`` to upgrade).
#   .env already present + stack down → starts the stack with the existing .env.
#   .env missing → generates a fresh one + starts.
set -euo pipefail

# ── colours ───────────────────────────────────────────────────────────────────
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

# ── prerequisites ─────────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || die "docker is not installed (https://docs.docker.com/engine/install/)"
docker compose version >/dev/null 2>&1 \
  || die "docker compose v2 is not available (try: docker compose version)"

# ── parse args ────────────────────────────────────────────────────────────────
LAYOUT=""
IMAGE_TAG=""             # empty → defaults to 'latest' in the generated .env
RESET="no"               # --reset → wipe volumes + .env before install
APPS_WHEEL=""            # --apps <wheel> → run install-apps.sh after base install
LICENSE_KEY=""           # --license-key <jwt> → forwarded to install-apps.sh
MASTER_KEY=""            # --master-key <value> → set LIBERTY_MASTER_KEY in .env (else generated)
SSL_MODE="none"          # --ssl none|letsencrypt|provided
SSL_DOMAIN=""            # --domain  (letsencrypt only — public hostname)
SSL_EMAIL=""             # --email   (letsencrypt only — ACME contact)
SSL_CERT_DIR=""          # --cert-dir  (provided only — abs host dir with cert files)
SSL_CERT_FILE=""         # --cert-file (provided only — cert filename within cert-dir)
SSL_KEY_FILE=""          # --key-file  (provided only — key  filename within cert-dir)

while [ $# -gt 0 ]; do
  case "$1" in
    light|full|prepare)
      [ -n "$LAYOUT" ] && die "Layout specified twice: '$LAYOUT' and '$1'"
      LAYOUT="$1"; shift ;;
    --tag)
      [ -z "${2:-}" ] && die "--tag requires a value (e.g. --tag 7.0.2)"
      IMAGE_TAG="$2"; shift 2 ;;
    --reset)
      RESET="yes"; shift ;;
    --apps)
      [ -z "${2:-}" ] && die "--apps requires a value (wheel path or http(s) URL)"
      APPS_WHEEL="$2"; shift 2 ;;
    --license-key)
      [ -z "${2:-}" ] && die "--license-key requires a value"
      LICENSE_KEY="$2"; shift 2 ;;
    --master-key)
      [ -z "${2:-}" ] && die "--master-key requires a value (use the value that encrypted your ENC: payloads)"
      MASTER_KEY="$2"; shift 2 ;;
    --ssl)
      [ -z "${2:-}" ] && die "--ssl requires a value (none|letsencrypt|provided)"
      SSL_MODE="$2"; shift 2 ;;
    --domain)
      [ -z "${2:-}" ] && die "--domain requires a value"
      SSL_DOMAIN="$2"; shift 2 ;;
    --email)
      [ -z "${2:-}" ] && die "--email requires a value"
      SSL_EMAIL="$2"; shift 2 ;;
    --cert-dir)
      [ -z "${2:-}" ] && die "--cert-dir requires a value"
      SSL_CERT_DIR="$2"; shift 2 ;;
    --cert-file)
      [ -z "${2:-}" ] && die "--cert-file requires a value"
      SSL_CERT_FILE="$2"; shift 2 ;;
    --key-file)
      [ -z "${2:-}" ] && die "--key-file requires a value"
      SSL_KEY_FILE="$2"; shift 2 ;;
    --help|-h)
      sed -n '4,46p' "$0"; exit 0 ;;
    *)
      die "Unknown argument: $1 (expected: light | full | prepare [flags])" ;;
  esac
done

# Cross-arg validation
[ -n "$APPS_WHEEL" ] && [ "$LAYOUT" = "prepare" ] && die "--apps cannot be combined with 'prepare' (apps need a running stack)."
[ -n "$LICENSE_KEY" ] && [ -z "$APPS_WHEEL" ] && warn "--license-key was passed without --apps — will be ignored (it's forwarded to install-apps.sh)."

# --reset is wipe-and-exit; combining with install flags is almost certainly a
# user-error (those flags would be silently lost when --reset returns 0). Reject
# loudly with the exact two-command sequence they probably meant.
if [ "$RESET" = "yes" ]; then
  combined=""
  [ -n "$APPS_WHEEL" ]            && combined="$combined --apps"
  [ -n "$LICENSE_KEY" ]           && combined="$combined --license-key"
  [ "$SSL_MODE" != "none" ]       && combined="$combined --ssl $SSL_MODE"
  if [ -n "$combined" ]; then
    die "--reset is wipe-and-exit; combining it with install flags ($combined) drops them silently.
       Run it as two commands instead:
         ./install.sh $LAYOUT --reset
         ./install.sh $LAYOUT$combined …"
  fi
fi

case "$SSL_MODE" in
  none) ;;
  letsencrypt)
    [ "$LAYOUT" != "full" ] && die "--ssl letsencrypt requires --layout full (light has no Traefik)."
    [ -z "$SSL_DOMAIN" ] && die "--ssl letsencrypt requires --domain <hostname>"
    [ -z "$SSL_EMAIL" ]  && die "--ssl letsencrypt requires --email <addr>"
    ;;
  provided)
    [ "$LAYOUT" != "full" ] && die "--ssl provided requires --layout full (light has no Traefik)."
    [ -z "$SSL_CERT_DIR" ]  && die "--ssl provided requires --cert-dir <abs-path>"
    [ -z "$SSL_CERT_FILE" ] && die "--ssl provided requires --cert-file <crt-filename>"
    [ -z "$SSL_KEY_FILE" ]  && die "--ssl provided requires --key-file <key-filename>"
    [ -d "$SSL_CERT_DIR" ]  || die "--cert-dir not found: $SSL_CERT_DIR"
    [ -f "$SSL_CERT_DIR/$SSL_CERT_FILE" ] || die "Cert not found: $SSL_CERT_DIR/$SSL_CERT_FILE"
    [ -f "$SSL_CERT_DIR/$SSL_KEY_FILE" ]  || die "Key not found:  $SSL_CERT_DIR/$SSL_KEY_FILE"
    SSL_CERT_DIR="$(cd "$SSL_CERT_DIR" && pwd)"   # normalize to absolute
    ;;
  *) die "Unknown --ssl mode: $SSL_MODE (expected: none | letsencrypt | provided)" ;;
esac

if [ -z "$LAYOUT" ]; then
  echo
  printf "${BOLD}Liberty Next — install${NC}\n"
  echo
  echo "Pick a layout:"
  echo "  1) light  — single container, SQLite DB (trial / demo)"
  echo "  2) full   — Traefik + liberty-next + Postgres + pgAdmin + Portainer (production)"
  echo
  read -r -p "Choose [1/2]: " choice
  case "$choice" in
    1) LAYOUT=light ;;
    2) LAYOUT=full ;;
    *) die "Invalid choice" ;;
  esac
fi

# ── helpers ───────────────────────────────────────────────────────────────────
# Generate a URL-safe-ish random string with NO ``$`` chars (so compose substitution
# never eats part of the value). 32 bytes → 64 hex chars; we use base32 here for shorter
# but case-insensitive-safe output. openssl is on every distro that has docker installed.
gen_secret() {
  local bytes="${1:-32}"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 "$bytes" | tr -d '\n=$+/' | head -c "$bytes"
  else
    head -c "$((bytes * 2))" /dev/urandom | base64 | tr -d '\n=$+/' | head -c "$bytes"
  fi
}

# ── reset (when requested) ────────────────────────────────────────────────────
# --reset wipes the named volumes + .env so the next install starts from a clean
# slate. Important for recovery from previous failed installs (postgres init only
# runs on a brand-new data dir; reusing an old pg-data with a fresh .env password
# means the DB still has the OLD password — auth fails forever).
ENV_FILE=".env"

if [ "$RESET" = "yes" ]; then
  warn "--reset: tearing down stack + wiping named volumes (pg-data, pgadmin-data, portainer-data, liberty-data, liberty-config)…"
  for cf in docker-compose.full.yml docker-compose.light.yml; do
    [ -f "$cf" ] && docker compose -f "$cf" down -v 2>/dev/null || true
  done
  # In case the stack was never created with compose (e.g. swarm), drop volumes by name.
  for v in liberty-config liberty-data pg-data pgadmin-data portainer-data traefik-acme; do
    docker volume rm "$v" 2>/dev/null || true
  done
  rm -f "$ENV_FILE"
  ok "Reset complete."
  # --reset is a destructive one-shot: stop here. The operator then re-runs install.sh
  # with the full set of flags they actually want (SSL mode, --apps wheel, --license-key,
  # etc.). Don't auto-continue with a "default" install — that swallowed flags before
  # and ended up with a half-configured stack.
  echo
  echo "  Next: re-run install.sh with the flags you want, e.g.:"
  case "$LAYOUT" in
    full)
      echo "    ./install.sh full \\"
      echo "        --ssl letsencrypt --domain <host> --email <addr> \\"
      echo "        --apps /path/to/liberty_apps-X.Y.Z.whl --license-key <jwt>"
      ;;
    light)
      echo "    ./install.sh light"
      ;;
    *)
      echo "    ./install.sh full [--ssl letsencrypt --domain X --email Y] [--apps wheel --license-key Z]"
      ;;
  esac
  exit 0
fi

# ── stale-volume detection ────────────────────────────────────────────────────
# When .env is being regenerated but a previous run's pg-data / pgadmin-data still
# exist, the fresh random secrets in the new .env WON'T match the credentials baked
# into those volumes (postgres + pgadmin only init on first start). Catch this BEFORE
# starting the stack so the user can choose --reset deliberately.
stale_volumes() {
  local found=()
  for v in pg-data pgadmin-data liberty-data; do
    docker volume inspect "$v" >/dev/null 2>&1 && found+=("$v")
  done
  printf '%s\n' "${found[@]}"
}

if [ ! -f "$ENV_FILE" ]; then
  STALE=$(stale_volumes | tr '\n' ' ')
  if [ -n "${STALE// /}" ]; then
    err "Stale Docker volumes from a previous install exist: ${STALE}"
    echo "  Generating fresh secrets now would leave postgres / pgadmin with their OLD"
    echo "  passwords (init scripts only run on brand-new volumes) → auth would fail."
    echo
    echo "  Two options:"
    echo "    1. Wipe + restart clean:    ./install.sh ${LAYOUT} --reset"
    echo "    2. Keep existing data:      restore the previous .env (it's not in git;"
    echo "                                 retrieve from backup, scrollback, or wipe the"
    echo "                                 volumes with option 1 above)."
    exit 1
  fi
fi

# ── compose-file chain (used both inside the heredoc + as shell env for install-time
# docker compose calls — compose v2's project discovery looks for compose.yaml in cwd
# BEFORE reading .env's COMPOSE_FILE, so we must set this in the shell env too).
COMPOSE_FILE_LINE="docker-compose.${LAYOUT}.yml"
case "$SSL_MODE" in
  letsencrypt) COMPOSE_FILE_LINE="${COMPOSE_FILE_LINE}:docker-compose.tls-letsencrypt.yml" ;;
  provided)    COMPOSE_FILE_LINE="${COMPOSE_FILE_LINE}:docker-compose.tls-provided.yml" ;;
esac

# ── .env generation ───────────────────────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
  warn ".env already exists — keeping it. Delete it first to regenerate."
  if [ -n "$IMAGE_TAG" ]; then
    warn "--tag was passed but .env already exists; edit LIBERTY_IMAGE_TAG=$IMAGE_TAG manually in .env, then re-run."
  fi

  # Repair: older .env files (pre-COMPOSE_FILE) cause compose to auto-discover the
  # WRONG file (or walk up to a stray docker-compose.yml elsewhere). Add COMPOSE_FILE
  # if missing.
  if ! grep -qE '^COMPOSE_FILE=' "$ENV_FILE"; then
    info "Existing .env lacks COMPOSE_FILE — adding 'COMPOSE_FILE=${COMPOSE_FILE_LINE}'"
    printf '\n# Added by install.sh: pins compose to the right layout (no -f juggling).\nCOMPOSE_FILE=%s\n' "$COMPOSE_FILE_LINE" >> "$ENV_FILE"
  fi

  # --master-key on an existing install: UPSERT the value in .env then force-recreate
  # liberty-next so the new key actually reaches the running container. Plain
  # ``docker compose up -d`` / ``docker restart`` does NOT re-evaluate env vars from .env
  # for the same service spec — operators expecting it to "just work" get a stale value.
  if [ -n "$MASTER_KEY" ]; then
    if grep -qE '^LIBERTY_MASTER_KEY=' "$ENV_FILE"; then
      sed -i.bak "s|^LIBERTY_MASTER_KEY=.*|LIBERTY_MASTER_KEY=${MASTER_KEY}|" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
      info "Replaced LIBERTY_MASTER_KEY in .env with the operator-supplied value."
    else
      printf '\nLIBERTY_MASTER_KEY=%s\n' "$MASTER_KEY" >> "$ENV_FILE"
      info "Added LIBERTY_MASTER_KEY to .env."
    fi
    if docker ps --format '{{.Names}}' | grep -qx liberty-next; then
      info "Force-recreating liberty-next so the new master key takes effect…"
      COMPOSE_FILE="$COMPOSE_FILE_LINE" docker compose up -d --force-recreate liberty-next
      ok "liberty-next recreated with the new LIBERTY_MASTER_KEY."
      warn "Note: any password you SAVED via the UI on the old key is now garbage — re-enter them via Settings → Pools / Connectors / Applications."
      exit 0
    fi
  fi
else
  info "Generating .env with random secrets…"

  LIBERTY_JWT_SECRET="$(gen_secret 48)"
  # Master key: operator-supplied via --master-key wins; else generate a fresh random
  # one. Generated is the right answer for a greenfield install (no pre-encrypted data
  # to read); supply --master-key when you need to decrypt content from elsewhere
  # (liberty-apps wheel's ENC: passwords, an export from another install, …).
  if [ -n "$MASTER_KEY" ]; then
    LIBERTY_MASTER_KEY="$MASTER_KEY"
    info "Using operator-supplied master key (--master-key)."
  else
    LIBERTY_MASTER_KEY="$(gen_secret 32)"
  fi
  POSTGRES_PASSWORD="$(gen_secret 24)"
  PGADMIN_PASSWORD="$(gen_secret 16)"
  LIBERTY_ADMIN_PASSWORD="$(gen_secret 16)"

  # Image tag line — if the operator passed --tag <ver>, write a real assignment;
  # otherwise leave commented so the compose default ('latest') kicks in.
  if [ -n "$IMAGE_TAG" ]; then
    LIBERTY_IMAGE_TAG_LINE="LIBERTY_IMAGE_TAG=${IMAGE_TAG}"
  else
    LIBERTY_IMAGE_TAG_LINE="# LIBERTY_IMAGE_TAG=latest"
  fi

  # COMPOSE_FILE_LINE was computed above (used both in the heredoc + as shell env
  # for install-time docker compose calls).

  # SSL env vars — only emit values relevant to the chosen mode; the rest stay
  # commented for documentation.
  SSL_ENV_BLOCK=""
  case "$SSL_MODE" in
    letsencrypt)
      SSL_ENV_BLOCK="$(cat <<SSL
# TLS — Let's Encrypt (ACME) mode. Host must be reachable on :80/:443 from the public
# internet for the cert challenge to succeed.
LIBERTY_DOMAIN=${SSL_DOMAIN}
ACME_EMAIL=${SSL_EMAIL}
# TRAEFIK_HTTPS_PORT=443
SSL
)"
      ;;
    provided)
      SSL_ENV_BLOCK="$(cat <<SSL
# TLS — operator-provided certificate. CERT_HOST_PATH is bind-mounted into Traefik
# at /etc/certs:ro; traefik/dynamic/tls.yml (generated by install.sh) references
# the specific cert + key filenames.
CERT_HOST_PATH=${SSL_CERT_DIR}
LIBERTY_DOMAIN=${SSL_DOMAIN:-localhost}
# TRAEFIK_HTTPS_PORT=443
SSL
)"
      ;;
    none)
      SSL_ENV_BLOCK="# TLS — disabled. Re-run install.sh with --ssl letsencrypt or --ssl provided to enable."
      ;;
  esac

  cat > "$ENV_FILE" <<EOF
# Generated by install.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# Every secret below was generated with 'openssl rand' — none contain '\$' so they
# pass through docker-compose substitution untouched.

# ── Compose discipline ─────────────────────────────────────────────────────
# COMPOSE_FILE is picked up by ``docker compose`` automatically — it tells compose
# WHICH files to merge for every command. install-apps.sh / --ssl flags append
# overlays here so subsequent ``docker compose pull / ps / up -d`` etc. (no -f
# flags) keep the right mounts + TLS config intact. NEVER pass -f manually.
COMPOSE_FILE=${COMPOSE_FILE_LINE}

# ── REQUIRED ───────────────────────────────────────────────────────────────
LIBERTY_JWT_SECRET=${LIBERTY_JWT_SECRET}
LIBERTY_MASTER_KEY=${LIBERTY_MASTER_KEY}

# ── FULL LAYOUT (ignored by the light compose) ─────────────────────────────
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
# POSTGRES_USER=liberty
# POSTGRES_DB=liberty
# POSTGRES_PORT=5432

PGADMIN_PASSWORD=${PGADMIN_PASSWORD}
# PGADMIN_EMAIL=admin@example.com

# ── OPTIONAL ───────────────────────────────────────────────────────────────
# First-boot Liberty Next admin password — generated here so you don't have to scrape
# it from the container logs. Change after login through Settings → Profile.
LIBERTY_ADMIN_PASSWORD=${LIBERTY_ADMIN_PASSWORD}

# Image tag — which ghcr.io/.../liberty-next:<tag> gets pulled:
#   <version>  — pinned (e.g. 7.0.1). Explicit version control.
#   latest     — default. Most recent release. Every merge to main → new release.
${LIBERTY_IMAGE_TAG_LINE}

# License key (RS256 JWT) — unlocks licensed connectors (nomasx1 / nomajde / nomaflow
# premium). Without a key, the framework runs in 'restricted' mode.
# LIBERTY_LICENSE_KEY=

# Anthropic API key — enables the AI assistant chat. Leave empty to disable.
# ANTHROPIC_API_KEY=

# OIDC (Single Sign-On) — set _ENABLED=true + fill provider details to enable.
# LIBERTY_OIDC_ENABLED=false
# LIBERTY_OIDC_PROVIDER_URL=
# LIBERTY_OIDC_CLIENT_ID=
# LIBERTY_OIDC_CLIENT_SECRET=

# Light layout — host port to expose liberty-next on.
# LIBERTY_PORT=8000

# Full layout — Traefik HTTP entrypoint port.
# TRAEFIK_HTTP_PORT=80

${SSL_ENV_BLOCK}
EOF
  chmod 600 "$ENV_FILE"     # secrets — owner-read-only
  ok "Generated .env (mode 0600)"
fi

# ── generate traefik/dynamic/tls.yml for --ssl provided ───────────────────────
# The file provider doesn't do env-var substitution — so we have to bake the cert
# filenames into the YAML at install time. Operators can edit this file later
# (e.g. for multi-cert setups); it's gitignored.
TLS_DYNAMIC="traefik/dynamic/tls.yml"
if [ "$SSL_MODE" = "provided" ]; then
  info "Writing $TLS_DYNAMIC referencing /etc/certs/${SSL_CERT_FILE} + /etc/certs/${SSL_KEY_FILE}"
  cat > "$TLS_DYNAMIC" <<TLSEOF
# Generated by install.sh --ssl provided on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# Edit freely — Traefik watches this file (file.watch=true), so changes apply live.
# Re-running install.sh --ssl provided OVERWRITES this file.

tls:
  stores:
    default:
      defaultCertificate:
        certFile: /etc/certs/${SSL_CERT_FILE}
        keyFile: /etc/certs/${SSL_KEY_FILE}
  certificates:
    - certFile: /etc/certs/${SSL_CERT_FILE}
      keyFile: /etc/certs/${SSL_KEY_FILE}
TLSEOF
elif [ "$SSL_MODE" = "letsencrypt" ] && [ -f "$TLS_DYNAMIC" ]; then
  # If switching from 'provided' to 'letsencrypt', the old tls.yml would still
  # reference non-existent certs and break Traefik startup. Remove it.
  info "Removing stale $TLS_DYNAMIC (letsencrypt mode uses the static-config resolver instead)"
  rm -f "$TLS_DYNAMIC"
fi

if [ "$LAYOUT" = "prepare" ]; then
  ok "Stopped before docker compose (you asked for 'prepare')."
  exit 0
fi

# ── compose up ────────────────────────────────────────────────────────────────
COMPOSE_FILE="docker-compose.${LAYOUT}.yml"
[ -f "$COMPOSE_FILE" ] || die "Compose file $COMPOSE_FILE not found"

# IMPORTANT: docker compose v2's project discovery looks for compose.yaml/yml in the
# cwd BEFORE reading .env's COMPOSE_FILE. Our cwd (release/) has no such file — so
# without the explicit COMPOSE_FILE env var here, compose walks up the tree looking
# for one and may find a stray compose.yaml elsewhere on disk. Pin it for the
# install-time commands. (Subsequent operator commands from release/ rely on .env's
# COMPOSE_FILE once a project is already established by these initial up -d.)
info "Pulling images (COMPOSE_FILE=${COMPOSE_FILE_LINE})…"
COMPOSE_FILE="$COMPOSE_FILE_LINE" docker compose pull

info "Starting the $LAYOUT stack…"
COMPOSE_FILE="$COMPOSE_FILE_LINE" docker compose up -d

info "Waiting for liberty-next to report healthy…"
for i in $(seq 1 60); do
  status=$(docker inspect --format='{{.State.Health.Status}}' liberty-next 2>/dev/null || echo "starting")
  case "$status" in
    healthy) ok "liberty-next is healthy."; break ;;
    unhealthy) err "liberty-next reported unhealthy — check 'docker compose logs liberty-next'"; exit 1 ;;
  esac
  sleep 2
  [ "$i" -eq 60 ] && warn "liberty-next still not healthy after 120 s — continuing; check logs if anything looks off."
done

# ── credentials summary ──────────────────────────────────────────────────────
echo
printf "${BOLD}━━━ Liberty Next ($LAYOUT) is up ━━━${NC}\n"
echo

LIBERTY_ADMIN_PASSWORD=$(grep -E '^LIBERTY_ADMIN_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)
PGADMIN_PASSWORD=$(grep -E '^PGADMIN_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)

case "$LAYOUT" in
  light)
    PORT=$(grep -E '^LIBERTY_PORT=' "$ENV_FILE" | cut -d= -f2- || true); PORT="${PORT:-8000}"
    echo "  SPA:        http://localhost:${PORT}"
    echo "  ReDoc:      http://localhost:${PORT}/redoc"
    echo "  Swagger:    http://localhost:${PORT}/docs"
    echo
    echo "  Sign in as:"
    echo "    username:  admin"
    echo "    password:  ${LIBERTY_ADMIN_PASSWORD}"
    ;;
  full)
    # Build the base URL — HTTPS host when TLS is on, plain HTTP otherwise.
    if [ "$SSL_MODE" != "none" ]; then
      DOMAIN=$(grep -E '^LIBERTY_DOMAIN=' "$ENV_FILE" | cut -d= -f2- || true); DOMAIN="${DOMAIN:-localhost}"
      HPORT=$(grep -E '^TRAEFIK_HTTPS_PORT=' "$ENV_FILE" | cut -d= -f2- || true); HPORT="${HPORT:-443}"
      BASE="https://${DOMAIN}${HPORT:+:$HPORT}"
      [ "$HPORT" = "443" ] && BASE="https://${DOMAIN}"
      echo "  TLS mode:   ${SSL_MODE}"
    else
      PORT=$(grep -E '^TRAEFIK_HTTP_PORT=' "$ENV_FILE" | cut -d= -f2- || true); PORT="${PORT:-80}"
      BASE="http://localhost${PORT:+:$PORT}"
      [ "$PORT" = "80" ] && BASE="http://localhost"
    fi
    echo "  Liberty:    ${BASE}/"
    echo "  ReDoc:      ${BASE}/redoc"
    echo "  pgAdmin:    ${BASE}/pgadmin"
    echo "  Portainer:  ${BASE}/portainer"
    echo "  Traefik:    ${BASE}/traefik     (basic-auth: admin / admin — change in traefik/dynamic/dynamic.yml)"
    echo
    echo "  Sign in to Liberty as:"
    echo "    username:  admin"
    echo "    password:  ${LIBERTY_ADMIN_PASSWORD}"
    echo
    echo "  pgAdmin:    admin@example.com / ${PGADMIN_PASSWORD}"
    ;;
esac

echo
echo "  All secrets are in .env (mode 0600, NOT in git). Keep it safe."
echo "  Backup the data volumes regularly: ./backup.sh"
echo
echo "  Docs:       https://docs.nomana-it.fr/liberty/getting-started/"
echo

# ── chain to install-apps.sh when --apps was passed ──────────────────────────
# Doing this AFTER the base summary keeps the credentials visible even if the apps
# step fails (operator still has the framework login).
if [ -n "$APPS_WHEEL" ]; then
  info "Chaining to install-apps.sh with wheel: $APPS_WHEEL"
  CMD=("./install-apps.sh" "$APPS_WHEEL" "--layout" "$LAYOUT")
  [ -n "$LICENSE_KEY" ] && CMD+=("--license-key" "$LICENSE_KEY")
  "${CMD[@]}"
fi
