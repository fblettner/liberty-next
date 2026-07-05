# Vendored Oracle Instant Client

The Docker image bundles the Oracle **Instant Client (Basic)** so python-oracledb can run in
**thick (OCI) mode** — needed to fetch a LOB over a database link, which thin mode can't do
(ORA-22992). Only `liberty.connectors.thick` (a subprocess) uses it; the main app stays thin/async.

It's **vendored here** rather than downloaded at build time because Oracle's download is
login-gated (a build-time `curl` fails in CI).

## What to put here

For each platform you deploy, drop the **Linux** Instant Client Basic **ZIP** (as-is, no need to
unzip — the Dockerfile unzips it at build) into:

```
docker/instantclient/amd64/instantclient-basic-linux.x64-<ver>.zip     # Linux x86-64
docker/instantclient/arm64/instantclient-basic-linux.arm64-<ver>.zip   # Linux aarch64
```

⚠ **These must be the Linux zips.** The macOS client you may use for a local `fastapi dev` test is
a *different* build — don't put it here; the image is Linux.

An arch you **don't** deploy can keep just its `.gitkeep`; the build still succeeds and thick mode
is unavailable on that arch (a clean `ThickFetchError` at runtime — you won't hit it if you don't
run there).

## Which version

Match the client to your Oracle **database** version:

- **19c Instant Client** — connects to Oracle **11.2 → 23ai** (safe broad choice; use this if JDE
  prod is 11g/12c).
- **23ai Instant Client** — connects to **19c+ only**.

Download: <https://www.oracle.com/database/technologies/instant-client/linux-x86-64-downloads.html>
(and the ARM64/aarch64 page). "Basic" (not "Basic Light") so all JDE character sets are covered.

## Size / git

The zips are large (~50 MB per arch). If the repo size matters, track them with **git LFS**
(`git lfs track "docker/instantclient/**/*.zip"`) before committing.

The Dockerfile copies `docker/instantclient/${TARGETARCH}/`, unzips the client into
`/opt/oracle/instantclient`, and sets `LIBERTY_ORACLE_CLIENT_LIB=/opt/oracle/instantclient` —
which `thick.py` passes to `init_oracle_client(lib_dir=…)`.
