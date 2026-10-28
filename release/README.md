# Liberty Next — deployment

Three ready-to-run layouts + helper scripts. Pick the layout that matches your runtime.

| Layout | Runtime | Services | Use case |
|---|---|---|---|
| **[`docker-compose.light.yml`](docker-compose.light.yml)** | Docker Compose | liberty-next (SQLite DB) | Local trial, single-user demo, quick eval. |
| **[`docker-compose.full.yml`](docker-compose.full.yml)** | Docker Compose | liberty-next, Postgres 16, Traefik, pgAdmin, Portainer | Production / staging on a single host. |
| **[`docker-compose.swarm.yml`](docker-compose.swarm.yml)** | Docker Swarm | same five services | Single or multi-node swarm (overlay networking, declarative `deploy.*` rollout, manager placement). |

All layouts use the public image at [`ghcr.io/fblettner/liberty-next`](https://github.com/fblettner/liberty-next/pkgs/container/liberty-next).

The operator command is **[`liberty`](liberty)** (see [below](#the-liberty-command)); it wraps
the historic helper scripts, which still work on their own:

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
./liberty install                  # interactive — asks light vs full
# OR
./liberty install light            # single container, SQLite
./liberty install full             # full production stack (uses :latest)
./liberty install full --tag 7.0.2 # full stack, pinned to a specific release
```

### Hardening since 7.0.58

- The container runs the app as **`liberty` (uid 1000)**, not root. It still starts as root for one
  thing: fixing the ownership of the config volume and the apps mount (an install created by an
  older release, files written by the installer), then it drops privileges. `LIBERTY_RUN_AS_ROOT=1`
  opts out. `docker exec` runs as root unless you pass `-u liberty`; `./liberty` does.
- **Postgres is published on `127.0.0.1` only** (SSH tunnel for DBeaver). `POSTGRES_BIND=0.0.0.0`
  in `.env` opens it; firewall it then.
- The **Traefik dashboard** gets a random password per install (printed once; `./liberty
  dashboard-password` regenerates it). An install still on the historic default is flagged by
  `./liberty doctor`.
- The TLS overlays redirect HTTP → HTTPS and send security headers (`frameDeny`, `nosniff`,
  referrer policy) on the app.
- Every `LIBERTY_*` / `POSTGRES_*` / `PGADMIN_*` variable also reads from **`<NAME>_FILE`** (Docker /
  Swarm / Kubernetes secrets mounted as files): `LIBERTY_MASTER_KEY_FILE=/run/secrets/master_key`.
- **Logs**: every line carries the request's `X-Request-ID` (echoed in the response, or minted);
  `[app] log_format = "json"` in `app.toml` emits one JSON object per line for a log shipper.
- **Reproducible image**: Python dependencies are pinned in `requirements.txt` (a pip-compile lock
  the image, CI and the dev venv share); base images and the compose services (Postgres, Traefik,
  pgAdmin, Portainer) are version-pinned and bumped by Dependabot. The release attaches an SBOM and
  provenance to the image and fails on CRITICAL / HIGH vulnerabilities with a fix (Trivy).
- The Oracle Instant Client is fetched from Oracle at image build time and checked against a pinned
  SHA-256 (146 MB of zips left the git tree); a local copy under `docker/instantclient/<arch>/` is
  used instead for air-gapped builds.

### The `liberty` command

`release/liberty` is the one entry point for the whole lifecycle — the same shape as
`nomaubl install | upgrade | start`. Every historic script (`install.sh`, `install-apps.sh`,
`upgrade.sh`, `backup.sh`) still works on its own; `liberty` wraps them and, above all, **never
recomputes the compose chain**: the layout + TLS overlay + apps overlay live in `.env` as
`COMPOSE_FILE`, written at install time and changed only by `liberty ssl` / `liberty apps`.

| Command | What it does |
|---|---|
| `./liberty install [light\|full] [--tag X] [--ssl …] [--apps <wheel>]` | First install (= `install.sh`). Re-running on an existing `.env` keeps the secrets, the volumes and the recorded chain. |
| `./liberty start` / `stop` / `restart` | The stack, with the recorded chain. `stop` keeps volumes + `.env`. |
| `./liberty status` | Layout, TLS mode, apps overlay, image tag, chain, container health, framework + apps versions, `/ready`, pending schema migrations, last recorded upgrade. What `upgrade` prints at the end. |
| `./liberty doctor` | Where a support ticket starts: `.env` + chain + overlay files present, apps overlay in the chain when `APPS_HOST_PATH` is set, secrets length, TLS files, volumes, `/ready`, pending migrations, master key decrypts every pool password, disk. Each problem comes with its fix. Exit 1 when something is wrong. |
| `./liberty ssl letsencrypt --domain D --email E` | Enable / switch TLS **after** the install (full layout). Only the TLS overlay changes in the chain; the apps overlay and the secrets are kept. |
| `./liberty ssl provided --domain D --cert-dir DIR --cert-file F --key-file K` | Same with your own certificate. |
| `./liberty ssl off` | Back to plain HTTP. |
| `./liberty apps <wheel> [--force-config] [--dry-run]` | Link (or refresh) the licensed apps (= `install-apps.sh`): prints the per-entity **merge plan**, applies it, then runs the pending schema migrations. `--dry-run` prints the plan only. |
| `./liberty upgrade [--tag X] [--apps <wheel>] [--keep DAYS]` | `doctor` (advisory) → the running image is kept under a local `rollback-<stamp>` tag → backup (the upgrade aborts if the snapshot fails) → pull + recreate with the recorded chain → pending schema migrations → a **report** (JSON + Markdown under the config volume, shown in Settings → History → Upgrades) → `status`. |
| `./liberty rollback [--yes]` | Back to the image that ran before the last upgrade and the volumes it snapshotted just before. Asks for confirmation (the restore drops everything written since), records a `rollback` report. |
| `./liberty migrate [--dry-run]` | `liberty-admin migrate` inside the container — every app plugin's pending Alembic revisions (nomasx1 today). `--dry-run` lists them, exit 2 when some are pending. |
| `./liberty db` · `db dump [file]` · `db external --host H …` · `db embedded` | Where the database is; a `pg_dumpall` of the bundled Postgres; move the app to a PostgreSQL server outside the stack (or back) — see [External database](#external-database). |
| `./liberty backup [--keep DAYS]` / `./liberty restore <dir>` | Volume snapshots (= `backup.sh`) and the matching restore (stops the stack, restores every `<volume>.tar.gz` + the apps bind mount, starts again). |
| `./liberty logs [-f]` | The `liberty-next` service's logs. |

### Which image tag does the install pull?

| `LIBERTY_IMAGE_TAG` | When to use |
|---|---|
| `latest` (default) | Default — always the most recent release. Every merge to main creates a new release, so `:latest` always reflects current main. |
| `<version>` (e.g. `7.0.2`) | When you want to pin to a specific release and stay there. |

`./liberty install ... --tag X.Y.Z` sets `LIBERTY_IMAGE_TAG=X.Y.Z` in the generated `.env`.
Without it, you get `latest`.

**If `.env` already exists**, that flag is ignored — move to another version with
`./liberty upgrade --tag X.Y.Z` (backup first, rollback possible).

The install is idempotent — re-run it any time. If `.env` already exists it's kept
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
| `/traefik` | Traefik dashboard (basic-auth — `admin` / the random password the install prints once; `./liberty dashboard-password` regenerates it) |

Volumes:

| Volume | What |
|---|---|
| `liberty-config` | TOML files the operator edits |
| `pg-data` | Postgres database files |
| `pgadmin-data` | pgAdmin server registrations + preferences |
| `portainer-data` | Portainer state |
| `traefik-acme` | Let's Encrypt certificate storage (when TLS is wired) |

### Wiring TLS

Two modes, chosen at install time (`./liberty install full --ssl …`) or switched later
with `./liberty ssl …` (the `.env` secrets are kept; only the TLS overlay in `COMPOSE_FILE`
changes).

#### Let's Encrypt (demo / public-internet hosts)

```bash
./liberty install full \
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
./liberty install full \
    --ssl provided \
    --domain liberty.internal.example.com \
    --cert-dir  /etc/pki/tls \
    --cert-file liberty.crt \
    --key-file  liberty.key
```

Requirements:
- A directory on the host containing the cert (`.crt` / `.pem`) and the private
  key file. The install validates both exist before continuing.

What it does:
- Adds [`docker-compose.tls-provided.yml`](docker-compose.tls-provided.yml) to `COMPOSE_FILE`.
- Sets `CERT_HOST_PATH=<cert-dir>` in `.env` — Traefik bind-mounts that directory at `/etc/certs:ro`.
- Generates [`traefik/dynamic/tls.yml`](traefik/dynamic/) (gitignored) with the
  cert + key filenames substituted in. Traefik watches the file — edit it to add
  more certs / SNI rules without restarting.

#### Switching modes later

`./liberty ssl letsencrypt …` / `./liberty ssl provided …` / `./liberty ssl off` — with the
same secrets in place. Only the TLS overlay is swapped in `COMPOSE_FILE` (the apps overlay
stays), `tls.yml` is rewritten or removed, and `docker compose up -d` picks up the new config.
(`./liberty install full --ssl <mode> …` on an existing `.env` does the same.)

#### No SSL (default)

`./liberty install full` without `--ssl` runs HTTP-only on :80. Fine for local dev /
behind another reverse proxy that terminates TLS upstream.

### External database

The framework database (users, job runs, change packages, saved views…) and the licensed apps'
schemas can live on a PostgreSQL server **outside the stack** — a managed instance, a DBA-run
server. It is recorded like TLS: two overlays in `COMPOSE_FILE`
([`docker-compose.external-db.yml`](docker-compose.external-db.yml) passes the connection,
[`docker-compose.no-embedded-db.yml`](docker-compose.no-embedded-db.yml) keeps the bundled
Postgres from starting on the full layout) and the connection in `.env` (`POSTGRES_HOST`,
`POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD`). Every later `start`,
`upgrade` and `backup` follows it.

**At install** — nothing bundled to start:

```bash
./liberty install full --db external --db-host db.example.com [--db-port 5432] \
    [--db-user liberty] [--db-name liberty] [--db-password-env DB_PW]   # else prompted
```

**Later** — an install that started with the bundled Postgres moves its database:

```bash
./liberty db dump                         # pg_dumpall of the bundled Postgres → ./backups/…sql.gz
gunzip -c ./backups/pg-dumpall-….sql.gz | psql -h db.example.com -U postgres -d postgres
./liberty db external --host db.example.com [--port 5432] [--password-env DB_PW]
```

`db external` records the overlays and the connection, stops and removes the bundled `liberty-pg`
container (its `pg-data` volume stays), rewrites the app's default pool (`liberty-admin
set-default-pool`, password encrypted) and moves every pool that named the bundled server
(`liberty-admin retarget-pools` — the apps' nomasx1 / nomajde / nomademo pools), then restarts.
The **data is not copied**: restore the dump on the new server first. `./liberty db embedded`
goes back to the bundled Postgres (as `pg-data` was left) — data written on the external server
since is not copied back. With an external database, `./liberty backup` says so: back it up on
its server.

### Backing up

```bash
./liberty backup                    # → ./backups/YYYY-MM-DD_HHMMSS/
./liberty backup /mnt/nas/liberty   # → /mnt/nas/liberty/YYYY-MM-DD_HHMMSS/
./liberty backup --keep 30          # delete backups older than 30 days from the destination
./liberty restore ./backups/<dir>   # stop, restore every volume + the apps, start again
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
0 3 * * 0  cd /opt/liberty-next/release && ./liberty backup /mnt/nas/liberty --keep 60
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
./liberty upgrade                          # doctor → backup → pull → recreate → migrations → status
./liberty upgrade --tag 7.0.57             # pin a release (written to .env as LIBERTY_IMAGE_TAG)
./liberty upgrade --apps liberty_apps-….whl  # framework + a new apps wheel in one go
```

**Never `docker compose -f docker-compose.full.yml pull / up -d` by hand.** A `-f` names only
the base layout, so compose drops the TLS and apps overlays recorded in `.env`: the container
comes back without the `/apps` mount, seeds the empty `.example` templates, and the install
looks wiped. `liberty upgrade` (and `upgrade.sh`) reuse the recorded `COMPOSE_FILE` chain.

The entrypoint runs `liberty-admin init-db` on every boot — idempotent, adds any new
framework tables a newer release brings, leaves existing rows alone. The licensed apps'
schema migrations (Alembic, shipped in the apps wheel) are applied by the `migrate` step
of `liberty upgrade`; at boot the container only **warns** in its log when some are pending
(`LIBERTY_AUTO_MIGRATE=1` in `.env` applies them at boot instead); `liberty status` /
`liberty doctor` show the same. The container's healthcheck is `/ready` (the framework
database answers), not just "the process is up": a stack whose database is unreachable shows
`unhealthy` in `docker ps` and in `liberty status`.

Something went wrong? `./liberty rollback` restores the pre-upgrade snapshot and re-runs the
image that was running before (kept locally as `rollback-<stamp>`), then `./liberty status`.

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
| Reset the admin password | `docker compose exec -u liberty liberty-next liberty-admin set-password admin --password <new>` | `docker exec -u liberty $(docker ps -qf name=liberty_liberty-next) liberty-admin set-password admin --password <new>` |
| Add another superuser | `docker compose exec liberty-next liberty-admin create-user <name> --superuser` | `docker exec $(docker ps -qf name=liberty_liberty-next) liberty-admin create-user <name> --superuser` |
| Inspect the license key | `docker compose exec liberty-next liberty-license verify` | `docker exec $(docker ps -qf name=liberty_liberty-next) liberty-license verify` |
| Health, versions, pending migrations | `./liberty status` (`./liberty doctor` for the fix-it list) | — |
| Apply the apps' schema migrations | `./liberty migrate` (`--dry-run` to list) | `docker exec $(docker ps -qf name=liberty_liberty-next) liberty-admin migrate` |
| Tail logs | `./liberty logs -f` | `docker service logs -f liberty_liberty-next` |
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
./liberty install full --apps ./liberty_apps-X.Y.Z-py3-none-any.whl
```

That's everything — base stack + licensed apps + license key in one go.

### Or split it: base first, apps later

```bash
./liberty install full                                 # base stack
./liberty apps ./liberty_apps-X.Y.Z-py3-none-any.whl   # add the apps; paste the license key in Settings → App
```

### What `liberty apps` does (`install-apps.sh`)

**Config is merged per entity, never copied over.** Product additions (a screen, a dictionary entry,
a menu item, a query, a dashboard) are added; what you own is kept — entities flagged
`override = true`, entities you created, entities you changed; vendor entities you never touched
are refreshed; what the vendor withdrew is removed only when untouched. `[pools.*]` and every
`ENC:` secret always come from your live `connectors.toml`; `theme.toml` is yours. The shipment is
recorded under `<apps>/config/.bundled/` so the next upgrade can tell "untouched" from "edited".
`--force-config` additionally refreshes vendor entities you changed without the `override` flag
(it never touches overrides, your own entities or secrets). Entities kept because they changed
locally are listed at the end: set `override = true` on them in Settings to keep them for good.
The merge runs inside a throwaway container from the liberty-next image you already pulled.


1. **Materializes the wheel** into `./apps/` (via a throwaway `python:3.12-slim`
   container — your host needs no local pip / python install). The wheel ships with
   a `liberty-apps install --target DIR` CLI that copies `config/` + `plugins/` into
   the destination, preserving operator-edited TOMLs.
2. **Updates `.env`**: `APPS_HOST_PATH=<absolute path>` + appends the apps overlay
   to `COMPOSE_FILE`. The license key is pasted in Settings → App (stored in `app.toml`).
3. **Restarts the stack** — `docker compose up -d` picks up the apps overlay
   automatically via `COMPOSE_FILE` (no `-f` juggling).

The apps land at `./apps/config/` + `./apps/plugins/` on the host. The
[`docker-compose.apps.yml`](docker-compose.apps.yml) overlay bind-mounts `./apps`
into the container at `/apps` (read-write: the config builders write there) and sets `LIBERTY_APPS_DIR=/apps/config`.

### Common variations

```bash
./liberty apps ./liberty_apps-X.Y.Z.whl                       # no license → restricted mode
./liberty apps ./liberty_apps-X.Y.Z.whl --dry-run             # print the merge plan only
./liberty apps https://license-host/liberty_apps-X.Y.Z.whl    # download from a URL (curl)
./liberty apps ./liberty_apps-X.Y.Z.whl --target /opt/apps    # destination override
./liberty apps ./liberty_apps-X.Y.Z.whl --force-config        # also refresh vendor entities you changed (never overrides / secrets)
```

### Updating the apps later

Drop in a new wheel and re-run (or pass it to the framework upgrade:
`./liberty upgrade --apps <wheel>`):

```bash
./liberty apps ./liberty_apps-X.Y.Z-py3-none-any.whl   # merge plan, merge, migrations, restart
```

The merge is per entity (above): your edits, overrides and secrets are kept.

### Persistence + restart + backup — what's safe?

| Event | `./apps/` content | DB / pgadmin / portainer data |
|---|---|---|
| `docker compose restart` | safe — bind mount re-attaches | safe — named volumes intact |
| Host reboot | safe — `restart: unless-stopped` brings everything back, mount re-attaches | safe |
| `docker compose down` (no `-v`) | safe — mount source untouched | safe |
| `docker compose down -v` | safe — bind mount isn't a Docker volume, `-v` doesn't touch it | **WIPED** (named volumes destroyed) |
| `./liberty install full --reset` | safe — only named volumes are dropped | **WIPED** |
| `./liberty backup` | **included** — the backup reads `APPS_HOST_PATH` from `.env` and tars `./apps/` alongside the named volumes | included (`pg-data.tar.gz` etc.) |

A backup directory after running `./liberty backup` contains:

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

After `liberty apps` (`install-apps.sh`) runs, `.env` carries:
```
COMPOSE_FILE=docker-compose.full.yml:docker-compose.apps.yml
```
Every `docker compose <command>` (with NO `-f` flag) reads this and merges both
files automatically. **Don't pass `-f` manually** after `liberty apps` —
compose would replace the COMPOSE_FILE list and you'd lose the apps mount on
the next container recreate.

### What the override file actually does

[`docker-compose.apps.yml`](docker-compose.apps.yml) is a ~10-line additive override:

```yaml
services:
  liberty-next:
    volumes:
      - ${APPS_HOST_PATH}:/apps        # bind-mount the apps clone (read-write)
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
the apps' open parts. Add the key later in Settings → App
(idempotent — only touches the .env line, doesn't re-clone).

---

## Without Docker

If you don't want containers at all (small install on a single host, or you're locked
into a Python-only environment), install from PyPI via **pipx**:

```bash
pipx install 'liberty-next[all]'  # [all] = reports (PDF) + ldap + alembic extras
liberty-next                     # → API + SPA on http://localhost:8000
```

pipx puts Liberty Next in its own isolated venv so its dependencies can't conflict
with system Python or other tools. The CLI commands (`liberty-next`, `liberty-admin`,
`liberty-ops`, `liberty-license`, `liberty-crypto`) end up on the PATH. See the root
[`README.md`](../README.md#pypi) for full PyPI install instructions.

**`liberty-ops` is the Python twin of `./liberty`** — same verbs, for a pipx or venv install
with or without systemd (unit name from `LIBERTY_SERVICE`, default `liberty-next`):

| Command | What it does |
|---|---|
| `liberty-ops status` | Installer (pipx / pip), framework + apps versions, app.toml, config dir, `LIBERTY_APPS_DIR`, service state, `/ready`, pending migrations, last recorded upgrade. |
| `liberty-ops doctor` | Python version, install, secrets present and long enough, config files parse, apps dir + plugins, master key decrypts every pool password, service, `/ready`, pending migrations, disk. Each problem with its fix; exit 1 when something is wrong. |
| `liberty-ops start` / `stop` / `restart` / `logs [-f]` | `systemctl` / `journalctl` on the unit (prints what to run by hand when there is no systemd). |
| `liberty-ops backup [--dest DIR] [--keep DAYS]` | `<dest>/<stamp>/` with the config dir (+ `.versions/`), the apps dir (config + plugins + data), the SQLite file when the default pool is SQLite, a `pg_dump` when it is Postgres and `pg_dump` is on the PATH, and a manifest. Default dest: `<config dir>/../backups`. |
| `liberty-ops restore <dir> [--yes]` | The reverse (stops / starts the service around it). A Postgres dump is not restored automatically — the command to run is printed. |
| `liberty-ops upgrade [--version X] [--apps WHEEL] [--keep DAYS] [--no-restart]` | `doctor` (advisory) → backup gate → `pipx upgrade` / `pipx install --force liberty-next[all]==X` (or `pip` in a plain venv) → `pipx inject` + `liberty-apps install` when a wheel is given → `liberty-admin migrate` → `systemctl restart` + wait for `/ready` → report (Settings → History → Upgrades) → `status`. |
| `liberty-ops rollback [--yes] [--apps WHEEL]` | Previous framework version (recorded by `upgrade`) + the pre-upgrade backup; the apps bundle is downgraded only when you pass the previous wheel. The upgrade's report is kept across the restore. |
| `liberty-ops migrate [--dry-run]` | = `liberty-admin migrate`. |

Run it with the same environment the service has (`set -a; . /etc/liberty/secrets.env; set +a`
under systemd) so it sees `LIBERTY_MASTER_KEY`, `LIBERTY_APPS_DIR` and the rest.

For state persistence you'll point `LIBERTY_DB_URL` at an existing Postgres (or stick
with the default SQLite — ``./liberty.db`` in the working directory). Config TOMLs
are read from `./config/<name>.toml` relative to the working directory by default;
set `LIBERTY_APPS_DIR=/some/path` to keep them somewhere stable (e.g.
`/etc/liberty-next/`) and run `liberty-next` from anywhere.

---

## Documentation

Full deployment guide, configuration reference, and walkthroughs: <https://docs.nomana-it.fr/liberty/getting-started/>.
