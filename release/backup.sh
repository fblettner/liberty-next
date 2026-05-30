#!/usr/bin/env bash
#
# Liberty Next — volume backup.
#
# Tars every Liberty named volume into a timestamped directory. Safe to run while
# the stack is up (Docker handles the read consistency); for a cold-perfect backup,
# stop the stack first (``docker compose -f docker-compose.full.yml down``).
#
# Usage:
#   ./backup.sh                          # → ./backups/YYYY-MM-DD_HHMMSS/
#   ./backup.sh /path/to/backups         # → /path/to/backups/YYYY-MM-DD_HHMMSS/
#   ./backup.sh --layout full            # back up the full layout's volumes (default: auto)
#   ./backup.sh --layout light           # back up the light layout's volumes
#   ./backup.sh --keep 7                 # delete backup dirs older than N days from the target
#
# Restore: see ``release/README.md`` (it's a 2-line ``docker run`` per volume).
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
DEST="./backups"
LAYOUT="auto"
KEEP_DAYS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --layout) LAYOUT="$2"; shift 2 ;;
    --keep)   KEEP_DAYS="$2"; shift 2 ;;
    --help|-h) sed -n '4,18p' "$0"; exit 0 ;;
    -*)       die "Unknown flag: $1" ;;
    *)        DEST="$1"; shift ;;
  esac
done

# ── pick the volume set ───────────────────────────────────────────────────────
# Common to both layouts:
COMMON_VOLUMES=(liberty-config)
# Light-only:
LIGHT_VOLUMES=(liberty-data)
# Full-only:
# pg-logs is intentionally excluded — rotated log files (not data), they regenerate
# on the next boot and would inflate backups.
FULL_VOLUMES=(pg-data pgadmin-data portainer-data)

vol_exists() { docker volume inspect "$1" >/dev/null 2>&1; }

declare -a TARGETS=()
case "$LAYOUT" in
  auto)
    for v in "${COMMON_VOLUMES[@]}" "${LIGHT_VOLUMES[@]}" "${FULL_VOLUMES[@]}"; do
      vol_exists "$v" && TARGETS+=("$v")
    done
    ;;
  light)
    for v in "${COMMON_VOLUMES[@]}" "${LIGHT_VOLUMES[@]}"; do
      vol_exists "$v" || warn "$v doesn't exist — skipping"
      vol_exists "$v" && TARGETS+=("$v")
    done
    ;;
  full)
    for v in "${COMMON_VOLUMES[@]}" "${FULL_VOLUMES[@]}"; do
      vol_exists "$v" || warn "$v doesn't exist — skipping"
      vol_exists "$v" && TARGETS+=("$v")
    done
    ;;
  *) die "Unknown layout: $LAYOUT (expected: auto | light | full)" ;;
esac

[ "${#TARGETS[@]}" -gt 0 ] || die "No Liberty volumes found — nothing to back up. Is the stack up?"

# ── destination ───────────────────────────────────────────────────────────────
STAMP="$(date -u +"%Y-%m-%d_%H%M%S")"
TARGET_DIR="${DEST%/}/${STAMP}"
mkdir -p "$TARGET_DIR"
TARGET_ABS="$(cd "$TARGET_DIR" && pwd)"

info "Destination: $TARGET_ABS"
info "Volumes:     ${TARGETS[*]}"

# ── snapshot each volume ──────────────────────────────────────────────────────
# Run an alpine container that mounts the volume read-only and tars its contents to
# /backup (the host destination, bind-mounted). Compressed with gzip.
for v in "${TARGETS[@]}"; do
  out="${TARGET_ABS}/${v}.tar.gz"
  printf "  %-20s → " "$v"
  docker run --rm \
    -v "${v}:/data:ro" \
    -v "${TARGET_ABS}:/backup" \
    alpine \
    tar czf "/backup/${v}.tar.gz" -C /data . \
    >/dev/null
  printf "%s\n" "$(du -h "$out" | cut -f1)"
done

# Also stash the .env (without secrets value exposure — copy with restrictive perms)
# so a restore knows the password values the live stack was using. Operators can decide
# whether to keep this file alongside the volumes (NOT recommended for off-site backups).
if [ -f .env ]; then
  cp .env "${TARGET_ABS}/.env.snapshot"
  chmod 600 "${TARGET_ABS}/.env.snapshot"
  info "Captured .env snapshot (mode 0600) — strip before off-site upload if you don't want secrets there."
fi

# Capture the compose file in use, too — lets a restore stand up an identical stack.
for cf in docker-compose.light.yml docker-compose.full.yml; do
  [ -f "$cf" ] && cp "$cf" "${TARGET_ABS}/"
done

# ── retention sweep ───────────────────────────────────────────────────────────
if [ -n "$KEEP_DAYS" ]; then
  info "Sweeping backup dirs older than $KEEP_DAYS days from ${DEST%/}/…"
  # Match our own YYYY-MM-DD_HHMMSS naming so we don't nuke unrelated dirs.
  find "${DEST%/}" -mindepth 1 -maxdepth 1 -type d \
    -regex '.*/[0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}_[0-9]\{6\}$' \
    -mtime "+${KEEP_DAYS}" -print -exec rm -rf {} \;
fi

echo
ok "Backup complete: $TARGET_ABS"
echo
echo "To restore one volume:"
echo "  docker run --rm -v <volume>:/data -v $TARGET_ABS:/backup alpine \\"
echo "      sh -c 'rm -rf /data/* /data/.[!.]* && tar xzf /backup/<volume>.tar.gz -C /data'"
echo
echo "Stop the stack first (docker compose ... down) — overwriting a live volume is unsafe."
