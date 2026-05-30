# Liberty Next — deployment

Three ready-to-run layouts + helper scripts. Pick the layout that matches your runtime.

| Layout | Runtime | Services | Use case |
|---|---|---|---|
| **[`docker-compose.light.yml`](docker-compose.light.yml)** | Docker Compose | liberty-next (SQLite DB) | Local trial, single-user demo, quick eval. |
| **[`docker-compose.full.yml`](docker-compose.full.yml)** | Docker Compose | liberty-next, Postgres 16, Traefik, pgAdmin, Portainer | Production / staging on a single host. |
| **[`docker-compose.swarm.yml`](docker-compose.swarm.yml)** | Docker Swarm | same five services | Single or multi-node swarm (overlay networking, declarative `deploy.*` rollout, manager placement). |

All layouts use the public image at [`ghcr.io/fblettner/liberty-next`](https://github.com/fblettner/liberty-next/pkgs/container/liberty-next).

Helper scripts:

| Script | What it does |
|---|---|
| **[`install.sh`](install.sh)** | Generates `.env` with cryptographically-random secrets (none contain `$` — so compose substitution never eats them), pulls images, brings up the Compose stack, prints credentials. |
| **[`deploy-swarm.sh`](deploy-swarm.sh)** | Sources `.env` into the shell (`docker stack deploy` has no `--env-file`), validates swarm state, deploys / updates the stack, waits for convergence, prints credentials. |
| **[`backup.sh`](backup.sh)** | Tar-snapshots every Liberty named volume into `./backups/YYYY-MM-DD_HHMMSS/`. Works for Compose AND Swarm — volumes are named the same way. |

---

## Quickstart

```bash
git clone https://github.com/fblettner/liberty-next.git
cd liberty-next/release
./install.sh                       # interactive — asks light vs full
# OR
./install.sh light                 # single container, SQLite
./install.sh full                  # full production stack (uses :latest)
./install.sh full --tag 7.0.2      # full stack, pinned to a specific release
```

### Which image tag does install.sh pull?

| `LIBERTY_IMAGE_TAG` | When to use |
|---|---|
| `latest` (default) | Default — always the most recent release. Every merge to main creates a new release, so `:latest` always reflects current main. |
| `<version>` (e.g. `7.0.2`) | When you want to pin to a specific release and stay there. |

`./install.sh ... --tag X.Y.Z` sets `LIBERTY_IMAGE_TAG=X.Y.Z` in the generated `.env`.
Without it, you get `latest`.

**If `.env` already exists**, that flag is ignored — edit `LIBERTY_IMAGE_TAG` in
`.env` directly, then `docker compose -f docker-compose.<layout>.yml pull && up -d`.

`install.sh` is idempotent — re-run it any time. If `.env` already exists it's kept
(delete it first to regenerate secrets); if the stack is already up the script just
re-applies the compose file. It prints the generated `admin` password on first run.

---

## Light — what you get

On port 8000:

| Path | What |
|---|---|
| `/` | React SPA — sign in with `admin` + the password the logs printed |
| `/docs` | Swagger UI |
| `/redoc` | ReDoc API reference |
| `/openapi.json` | OpenAPI 3 spec |
| `/info` | Public liveness + counts (Docker `HEALTHCHECK` hits this) |

Persisted state lives in two named volumes:

- `liberty-data` — SQLite DB (auth users + nomaflow run history) + `auth.toml` (Argon2 hashes)
- `liberty-config` — every TOML file the operator edits (connectors / dictionary / menus / screens / charts / dashboards)

---

## Full — what you get

URL routing (everything on port 80):

| Path | Service |
|---|---|
| `/` (catchall) | liberty-next (SPA + API + admin + docs) |
| `/pgadmin` | pgAdmin (Postgres GUI) |
| `/portainer` | Portainer (Docker UI) |
| `/traefik` | Traefik dashboard (basic-auth — `admin`/`admin` by default; **change it in `traefik/dynamic/dynamic.yml`**) |

Volumes:

| Volume | What |
|---|---|
| `liberty-config` | TOML files the operator edits |
| `pg-data` | Postgres database files |
| `pgadmin-data` | pgAdmin server registrations + preferences |
| `portainer-data` | Portainer state |
| `traefik-acme` | Let's Encrypt certificate storage (when TLS is wired) |

### Wiring TLS

Two modes, both wired by `install.sh --ssl`. Choose at install time or re-run later
(re-running keeps `.env` secrets but updates the SSL config + `COMPOSE_FILE`).

#### Let's Encrypt (demo / public-internet hosts)

```bash
./install.sh full \
    --ssl letsencrypt \
    --domain liberty.example.com \
    --email ops@example.com
```

Requirements:
- The hostname must resolve to this host (DNS A/AAAA record).
- :80 and :443 must be reachable from the public internet (Let's Encrypt's
  TLS-ALPN challenge needs them).

What it does:
- Adds [`docker-compose.tls-letsencrypt.yml`](docker-compose.tls-letsencrypt.yml) to `COMPOSE_FILE`.
- Sets `LIBERTY_DOMAIN` + `ACME_EMAIL` in `.env`.
- Traefik handles cert request + renewal via the ACME resolver. Certs persist in the `traefik-acme` named volume.

#### Operator-provided certs (corporate / air-gapped hosts)

```bash
./install.sh full \
    --ssl provided \
    --domain liberty.internal.example.com \
    --cert-dir  /etc/pki/tls \
    --cert-file liberty.crt \
    --key-file  liberty.key
```

Requirements:
- A directory on the host containing the cert (`.crt` / `.pem`) and the private
  key file. `install.sh` validates both exist before continuing.

What it does:
- Adds [`docker-compose.tls-provided.yml`](docker-compose.tls-provided.yml) to `COMPOSE_FILE`.
- Sets `CERT_HOST_PATH=<cert-dir>` in `.env` — Traefik bind-mounts that directory at `/etc/certs:ro`.
- Generates [`traefik/dynamic/tls.yml`](traefik/dynamic/) (gitignored) with the
  cert + key filenames substituted in. Traefik watches the file — edit it to add
  more certs / SNI rules without restarting.

#### Switching modes later

Re-run `./install.sh full --ssl <new-mode> …` with the same secrets in place. The
script swaps the overlay in `COMPOSE_FILE`, rewrites `tls.yml` (or removes it for
LE mode), and `docker compose up -d` picks up the new config.

#### No SSL (default)

`./install.sh full` without `--ssl` runs HTTP-only on :80. Fine for local dev /
behind another reverse proxy that terminates TLS upstream.

### Backing up

```bash
./backup.sh                    # → ./backups/YYYY-MM-DD_HHMMSS/
./backup.sh /mnt/nas/liberty   # → /mnt/nas/liberty/YYYY-MM-DD_HHMMSS/
./backup.sh --keep 30          # delete backups older than 30 days from the destination
```

Each run creates one directory with:

- `liberty-config.tar.gz` — every TOML the operator edited (connectors, screens, dictionary, …)
- `pg-data.tar.gz` — Postgres database files (full layout only)
- `pgadmin-data.tar.gz`, `portainer-data.tar.gz` — their state (full layout only)
- `liberty-data.tar.gz` — SQLite DB + auth.toml (light layout only)
- `.env.snapshot` — the env file used by the live stack (mode 0600 — strip before off-site sync if you don't want secrets in the backup)
- `docker-compose.*.yml` — the compose file(s) in this directory

Run weekly from cron:

```cron
0 3 * * 0  cd /opt/liberty-next/release && ./backup.sh /mnt/nas/liberty --keep 60
```

Backups are safe while the stack is running (Docker handles read consistency). For a
cold-perfect snapshot, `docker compose -f docker-compose.full.yml down` first.

#### Restoring one volume

The script prints the exact command on success. The general shape:

```bash
docker compose -f docker-compose.full.yml down            # MUST be down — never restore on a live volume
docker volume rm pg-data                                  # wipe (skip if you want to overlay)
docker run --rm -v pg-data:/data -v "$PWD/backups/<dir>:/backup" alpine \
    sh -c 'rm -rf /data/* /data/.[!.]* && tar xzf /backup/pg-data.tar.gz -C /data'
docker compose -f docker-compose.full.yml up -d
```

### Upgrading (v2 → v2)

```bash
./backup.sh                                # snapshot first (always)
docker compose -f docker-compose.full.yml pull
docker compose -f docker-compose.full.yml up -d
```

The entrypoint runs `liberty-admin init-db` on every boot — idempotent, adds any new
framework tables a newer release brings, leaves existing rows alone. Schema migrations
are bundled inside the image; no manual step.

To pin a specific version (recommended for production), set `LIBERTY_IMAGE_TAG` in
`.env` (e.g. `LIBERTY_IMAGE_TAG=0.2.0`) — the `pull` then fetches that exact tag.
Roll forward by bumping the value + running `pull && up -d` again.

---

## Docker Swarm

Same five services as the Compose full layout, but adapted for Swarm's runtime model.
Works for single-node swarms (a one-VM staging install) and multi-node swarms (one
manager + N workers).

### Why a separate compose file

`docker stack deploy` ignores several Compose-only constructs (`container_name`,
`depends_on: condition: service_healthy`, `restart: unless-stopped`) and needs others
that Compose doesn't (`deploy.*`, `--providers.swarm`, overlay networks). The
[`docker-compose.swarm.yml`](docker-compose.swarm.yml) file is the same stack, ported.

### The env var question

`docker stack deploy` doesn't have a `--env-file` flag. It still performs `${VAR}`
substitution at deploy time, but it reads from the **shell environment**, not `.env`.
The [`deploy-swarm.sh`](deploy-swarm.sh) helper bridges the gap with the standard
pattern:

```bash
set -a; source .env; set +a              # export every KEY=value into the shell
docker stack deploy -c docker-compose.swarm.yml liberty
```

Once values are substituted in, they're baked into the service spec in the swarm raft
store. Changing `.env` after deploy has NO effect — re-run `./deploy-swarm.sh` to push
new values. (For sensitive long-term secrets, Docker Secrets is the swarm-native
alternative — see the comment block at the bottom of `docker-compose.swarm.yml`.)

### Quickstart

```bash
# One-time, on the manager:
docker swarm init                                    # single-node
docker swarm init --advertise-addr <manager-ip>      # multi-node

# Generate .env if you don't already have one (writes random secrets — no $ chars):
./install.sh prepare

# Deploy / update the stack:
./deploy-swarm.sh                                    # stack name defaults to 'liberty'
./deploy-swarm.sh --stack mystack                    # custom stack name
./deploy-swarm.sh --status                           # show current service state, no deploy
./deploy-swarm.sh --rm                               # tear the stack down (volumes survive)
```

### Updating one image

`docker stack deploy` is also the update mechanism — re-running it reconciles the spec
against what's running. To bump just one service to a specific tag without touching the
others:

```bash
docker service update --image ghcr.io/fblettner/liberty-next:0.2.0 liberty_liberty-next
# Roll back if something looks wrong:
docker service rollback liberty_liberty-next
```

### Placement constraints

The swarm compose pins **every service to a manager** by default — fine for single-node
swarms and small clusters. For larger multi-node setups, customise:

- **Postgres** must be pinned to a specific node so its volume reattaches in place.
  Add `node.hostname == <your-pg-node>` to its `placement.constraints`.
- **Liberty-next** can move to workers — change its constraint to `node.role == worker`
  if you have dedicated app-tier nodes.
- **Traefik** must stay on a manager (its `--providers.swarm` reads the Docker socket,
  which only managers expose).

### Multi-replica notes

`replicas: 1` is the default for every service. Stateful services (pg, pgadmin,
portainer) should stay there — none have built-in replication. `liberty-next` keeps
Socket.IO state in-process, so bumping its replicas without a shared backplane (Redis
adapter) will give clients an inconsistent view of live dashboards / chat streams. The
Traefik sticky cookie helps but isn't a substitute. Scale once Redis is wired in.

### Backups + restores

[`backup.sh`](backup.sh) works the same — Liberty's volume names (`liberty-config`,
`pg-data`, …) are identical across Compose and Swarm. Run it from the manager. Restore
follows the same `docker run --rm -v <vol>:/data …` pattern as the Compose section
above; just stop the stack first with `./deploy-swarm.sh --rm` (volumes survive).

---

## Common operations

| Need | Compose | Swarm |
|---|---|---|
| Reset the admin password | `docker compose exec liberty-next liberty-admin set-password admin <new>` | `docker exec $(docker ps -qf name=liberty_liberty-next) liberty-admin set-password admin <new>` |
| Add another superuser | `docker compose exec liberty-next liberty-admin create-user <name> --superuser` | `docker exec $(docker ps -qf name=liberty_liberty-next) liberty-admin create-user <name> --superuser` |
| Inspect the license key | `docker compose exec liberty-next liberty-license verify` | `docker exec $(docker ps -qf name=liberty_liberty-next) liberty-license verify` |
| Tail logs | `docker compose -f docker-compose.full.yml logs -f liberty-next` | `docker service logs -f liberty_liberty-next` |
| Open a shell | `docker compose exec liberty-next bash` | `docker exec -it $(docker ps -qf name=liberty_liberty-next) bash` |
| List services | `docker compose ps` | `docker stack services liberty` |
| Reload config (TOML change) | hit `POST /admin/reload` (Settings UI button does this) | same |

See `liberty-admin --help` / `liberty-license --help` for the full CLI.

---

## Adding the licensed apps (liberty-apps)

The liberty-next image ships the **open framework** only. Customer-facing content
(nomasx1 / nomajde / nomaflow / …) lives in the separate **liberty-apps** package,
delivered as a Python wheel (`liberty_apps-X.Y.Z-py3-none-any.whl`) and unlocked
by a license key (`LIBERTY_LICENSE_KEY`).

### Single-command install (fresh host)

```bash
./install.sh full \
    --apps ./liberty_apps-7.0.1-py3-none-any.whl \
    --license-key <your-rs256-jwt>
```

That's everything — base stack + licensed apps + license key in one go.

### Or split it: base first, apps later

```bash
./install.sh full                                                          # base stack
./install-apps.sh ./liberty_apps-7.0.1-py3-none-any.whl --license-key <jwt>   # add the apps
```

### What `install-apps.sh` does

1. **Materializes the wheel** into `./apps/` (via a throwaway `python:3.12-slim`
   container — your host needs no local pip / python install). The wheel ships with
   a `liberty-apps install --target DIR` CLI that copies `config/` + `plugins/` into
   the destination, preserving operator-edited TOMLs.
2. **Updates `.env`**: `APPS_HOST_PATH=<absolute path>` + appends the apps overlay
   to `COMPOSE_FILE` + `LIBERTY_LICENSE_KEY=<jwt>` (if you passed `--license-key`).
3. **Restarts the stack** — `docker compose up -d` picks up the apps overlay
   automatically via `COMPOSE_FILE` (no `-f` juggling).

The apps land at `./apps/config/` + `./apps/plugins/` on the host. The
[`docker-compose.apps.yml`](docker-compose.apps.yml) overlay bind-mounts `./apps`
into the container at `/apps:ro` and sets `LIBERTY_APPS_DIR=/apps/config`.

### Common variations

```bash
./install-apps.sh ./liberty_apps-X.Y.Z.whl                       # no license → restricted mode
./install-apps.sh https://license-host/liberty_apps-X.Y.Z.whl    # download from a URL (curl)
./install-apps.sh ./liberty_apps-X.Y.Z.whl --target /opt/apps    # destination override
./install-apps.sh ./liberty_apps-X.Y.Z.whl --layout light        # layer onto the light stack
./install-apps.sh ./liberty_apps-X.Y.Z.whl --force-config        # overwrite operator-edited TOMLs (re-install)
```

### Updating the apps later

Drop in a new wheel and re-run:

```bash
./install-apps.sh ./liberty_apps-7.0.2-py3-none-any.whl
docker compose restart liberty-next       # picks up the new TOMLs
```

Operator-edited TOMLs are preserved by default (the wheel's `liberty-apps install`
CLI only overwrites when `--force-config` is passed). If `hot_reload = true` in
`app.toml`, you don't even need the restart — file edits are picked up live.

### Persistence + restart + backup — what's safe?

| Event | `./apps/` content | DB / pgadmin / portainer data |
|---|---|---|
| `docker compose restart` | safe — bind mount re-attaches | safe — named volumes intact |
| Host reboot | safe — `restart: unless-stopped` brings everything back, mount re-attaches | safe |
| `docker compose down` (no `-v`) | safe — mount source untouched | safe |
| `docker compose down -v` | safe — bind mount isn't a Docker volume, `-v` doesn't touch it | **WIPED** (named volumes destroyed) |
| `./install.sh full --reset` | safe — only named volumes are dropped | **WIPED** |
| `./backup.sh` | **included** — backup.sh reads `APPS_HOST_PATH` from `.env` and tars `./apps/` alongside the named volumes | included (`pg-data.tar.gz` etc.) |

A backup directory after running `./backup.sh` contains:

```
backups/2026-05-30_170000/
  liberty-config.tar.gz       — framework config (seeded TOMLs the operator edited via UI)
  liberty-apps.tar.gz         — your liberty-apps clone (only when APPS_HOST_PATH is set)
  pg-data.tar.gz              — Postgres data files
  pgadmin-data.tar.gz         — pgAdmin state
  portainer-data.tar.gz       — Portainer state
  .env.snapshot               — env vars + secrets (mode 0600)
  docker-compose.*.yml        — compose files in use
```

### COMPOSE_FILE discipline

After `install-apps.sh` runs, `.env` carries:
```
COMPOSE_FILE=docker-compose.full.yml:docker-compose.apps.yml
```
Every `docker compose <command>` (with NO `-f` flag) reads this and merges both
files automatically. **Don't pass `-f` manually** after install-apps.sh —
compose would replace the COMPOSE_FILE list and you'd lose the apps mount on
the next container recreate.

### What the override file actually does

[`docker-compose.apps.yml`](docker-compose.apps.yml) is a ~10-line additive override:

```yaml
services:
  liberty-next:
    volumes:
      - ${APPS_HOST_PATH}:/apps:ro     # bind-mount the apps clone
    environment:
      LIBERTY_APPS_DIR: /apps/config   # redirect TOML reads to the mount
```

You can run it directly without `install-apps.sh` if you've already set
`APPS_HOST_PATH` in `.env` yourself:

```bash
docker compose -f docker-compose.full.yml -f docker-compose.apps.yml up -d
```

### Without a license key

The framework still starts — it just runs in **restricted mode** (connectors flagged
`licensed = true` in the apps' `connectors.toml` aren't loaded). Useful for testing
the apps' open parts. Add the key later with `./install-apps.sh --license-key <jwt>`
(idempotent — only touches the .env line, doesn't re-clone).

---

## Without Docker

If you don't want containers at all (small install on a single host, or you're locked
into a Python-only environment), install from PyPI via **pipx**:

```bash
pipx install liberty-next
liberty-next                     # → API + SPA on http://localhost:8000
```

pipx puts Liberty Next in its own isolated venv so its dependencies can't conflict
with system Python or other tools. The four CLI commands (`liberty-next`,
`liberty-admin`, `liberty-license`, `liberty-crypto`) end up on the PATH; upgrades are
`pipx upgrade liberty-next`. See the root [`README.md`](../README.md#pypi) for full
PyPI install instructions.

For state persistence you'll point `LIBERTY_DB_URL` at an existing Postgres (or stick
with the default SQLite — ``./liberty.db`` in the working directory). Config TOMLs
are read from `./config/<name>.toml` relative to the working directory by default;
set `LIBERTY_APPS_DIR=/some/path` to keep them somewhere stable (e.g.
`/etc/liberty-next/`) and run `liberty-next` from anywhere.

---

## Documentation

Full deployment guide, configuration reference, and walkthroughs: <https://docs.nomana-it.fr/liberty/getting-started/>.
