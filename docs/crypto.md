# Field encryption — `ENC:` secrets & the master key

v2 reuses **v1's exact field-encryption scheme and key** (AES-256-GCM, PBKDF2-HMAC-SHA512,
the `ENC:`-prefixed wire format). It does **not** re-encrypt anything — the point is that
v2 and your other scripts can read/write the same encrypted DB columns interoperably
(`SETTINGS_APPLICATIONS.password`, `ly_api_conn.conn_password`, …).

Implementation: [`liberty/crypto.py`](../liberty/crypto.py) · CLI: [`liberty/crypto_cli.py`](../liberty/crypto_cli.py) (`liberty-crypto`).

---

## Do I need to do anything right now?

**No.**

- The `admin` user you created with `liberty-admin init-db` uses **Argon2id** (a one-way
  *password hash*) — completely separate from `ENC:` encryption. The master key plays no
  role there. Nothing to migrate, nothing to set.
- `config/connectors.toml` currently has **no `ENC:` values**, so `LIBERTY_MASTER_KEY`
  being unset is fine — `crypto.configured` is just `false`, nothing breaks.

## When *will* I need the master key?

Set `LIBERTY_MASTER_KEY` (to your v1 `MASTER_KEY`) once **either** of these happens:

1. You migrate an app's **API connectors** — v1's `ly_api_conn.conn_password` comes over
   as an `ENC:…` blob in `connectors.toml`, and v2 decrypts it at runtime.
2. You point a v2 query/connector at a **column another script wrote encrypted**
   (`SETTINGS_APPLICATIONS.password`, etc.) and want v2 to decrypt it.

If the key is wrong or missing when an `ENC:` value is loaded, the connector still loads —
the value is just left as the `ENC:` blob and a warning is logged.

## Where the v1 `MASTER_KEY` lives

In v1's `secrets.json` (which v1 keeps encrypted as `secrets.json.enc`, Fernet key in
`encryption.key`) — the same value your other scripts already use to decrypt those columns.
Keep it out of this repo: v2 reads it from the `LIBERTY_MASTER_KEY` env var, never from a
committed file. (v2 does **not** read `secrets.json`.)

---

## Setting the key

`config/app.toml` has `master_key = "${LIBERTY_MASTER_KEY}"`, so v2 reads it from the
environment — same as `LIBERTY_DB_URL` / `LIBERTY_JWT_SECRET`:

```bash
export LIBERTY_MASTER_KEY='<your v1 MASTER_KEY>'
./start.sh
```

…or via a `.env` file, a systemd `Environment=` line, docker `-e LIBERTY_MASTER_KEY=…`, etc.
You can also pass it ad-hoc on the CLI with `--master-key` (below).

Check it's picked up: `GET /info` → `crypto.configured: true` (it never echoes the key).

---

## `liberty-crypto` CLI

```bash
# encrypt a value → ENC:…   (idempotent: feeding an ENC: value back returns it unchanged)
.venv/bin/liberty-crypto encrypt 'my secret' --master-key "$LIBERTY_MASTER_KEY"

# decrypt an ENC:… value (e.g. one straight out of the DB)
.venv/bin/liberty-crypto decrypt 'ENC:…' --master-key "$LIBERTY_MASTER_KEY"

# test for the ENC: marker — exit 0 if encrypted, 1 if not (for scripting)
.venv/bin/liberty-crypto is-encrypted 'ENC:…'; echo $?

# reads stdin when no value arg:
echo -n 'my secret' | .venv/bin/liberty-crypto encrypt --master-key "$LIBERTY_MASTER_KEY"
```

- `--master-key VALUE` overrides the configured key (otherwise it comes from `[crypto] master_key`
  in `config/app.toml` → `LIBERTY_MASTER_KEY`).
- `--config PATH` points at a different `app.toml`.
- Exit codes: `0` ok · `1` only for `is-encrypted` on a plaintext value · `2` on error
  (no master key, decrypting a non-`ENC:` value, bad ciphertext, …).

Sanity-check the key against a real v1 value before relying on it:

```bash
.venv/bin/liberty-crypto decrypt 'ENC:…value from your v1 DB…' --master-key "$LIBERTY_MASTER_KEY"
# should print the original plaintext
```

---

## In `connectors.toml`

API connectors can carry encrypted auth secrets — v2 decrypts `auth_username` / `auth_password` /
`auth_token` at load time if they start with `ENC:`:

```toml
[connectors.acme_api]
type = "api"
base_url = "https://api.acme.example"
auth_type = "basic"
auth_username = "svc-account"
auth_password = "ENC:…"        # decrypted at runtime with [crypto] master_key
```

Plaintext values are passed through untouched. `describe()` / `GET /api/connectors` never expose
credentials regardless. After editing `connectors.toml`, `POST /admin/reload` (superuser) re-reads
it with the same master key.

To produce a fresh `ENC:` value for a config:

```bash
.venv/bin/liberty-crypto encrypt 'the-real-password' --master-key "$LIBERTY_MASTER_KEY"
# → ENC:…   ← paste that into connectors.toml
```
