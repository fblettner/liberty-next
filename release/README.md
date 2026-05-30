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

### Wiring TLS (Let's Encrypt)

1. Point your domain at the server.
2. In `.env`, set:
   ```
   LIBERTY_DOMAIN=liberty.example.com
   ACME_EMAIL=ops@example.com
   ```
3. In `docker-compose.full.yml`, uncomment the `websecure` entrypoint + the `certificatesresolvers.le.*` flags + the `:443` port mapping.
4. Add `traefik.http.routers.<name>.tls.certresolver: "le"` to each router label.
5. `docker compose -f docker-compose.full.yml up -d`. Traefik requests certs on first hit.

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
