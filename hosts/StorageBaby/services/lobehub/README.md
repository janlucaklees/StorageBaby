# LobeHub

A self-hosted workspace for AI agents — chat, agent builder, knowledge base, MCP client.
<https://lobehub.home.klees.io> — private, so a VPN client or another service on this host,
and nothing from the internet. Upstream: <https://github.com/lobehub/lobehub>.

This replaced Hermes, which held the same slot: an agent that is itself a service, so the
MCP adapters it uses live beside it rather than on a laptop. Nothing of Hermes' shape
survives — it was one container with its state in a single volume, this is a pod of six with
a database, a file store and three public names.

## Three routes, because three parts answer a browser

A Traefik router here matches on hostname alone; there is no path-based routing. So each part
the browser talks to directly needs a name of its own:

| name                            | `container_port` | what the browser does with it                     |
| ------------------------------- | ---------------- | ------------------------------------------------- |
| `lobehub.home.klees.io`         | 3210             | the application itself                            |
| `lobehub-gateway.home.klees.io` | 8787             | the websocket that streams server-side agent runs |
| `lobehub-files.home.klees.io`   | 9000             | presigned uploads and downloads of attachments    |

Those are the ports the three processes listen on **inside the pod**, which is the only
thing this folder says about ports. The loopback port Traefik dials is Cradle's, derived
from the service name and each route's declared domain, so the file store keeps its own
default 9000 and no two services can argue about a number. Inside the pod every part
addresses every other on `127.0.0.1:<container port>` — 3210, 8787, 8788, 9000, 8080, 6379
and 5432 — and none of those is a route port.

Everything else upstream's compose publishes stays unpublished: the database, redis, the file
store's admin console (turned off in the unit instead) and the device gateway's 8788, which
exists for desktop and CLI clients joined with `lh connect` and has none here.

## The parts

| container          | image                           | what it is                                   |
| ------------------ | ------------------------------- | -------------------------------------------- |
| `lobehub-app`      | `lobehub/lobehub:2.2.19`        | the Next.js application and its API          |
| `lobehub-gateway`  | `lobehub-gateway:0.3.2`         | relays server-side agent runs to the browser |
| `lobehub-database` | `paradedb/paradedb:0.26.1-pg17` | postgres with `pg_search` and `pg_cron`      |
| `lobehub-cache`    | `redis:alpine`                  | cache, prefix `lobehub`                      |
| `lobehub-files`    | `rustfs/rustfs:1.0.1`           | the S3-compatible attachment store           |
| `lobehub-search`   | `searxng/searxng:2026.10.15-…`  | the agent's web search backend               |

ParadeDB is not interchangeable with plain postgres: the app's full-text search is
`pg_search`, which only this image ships. Its tag is pinned to a version rather than
upstream's `latest-pg17` so that a converge cannot move the cluster's extensions on its own.
The gateway is pinned to what upstream's compose pins **at the app's own release tag** —
0.4.0 and 0.5.0 exist in the registry and are not what 2.2.19 shipped against; a bump moves
both lines together.

Elasticsearch, which upstream offers as an optional full-text backend behind a compose
profile, is deliberately absent: `pg_search` in the database above is the default path and
the node alone wants 2 GB of RAM.

## The bucket is prepared by a hook, not by a seventh container

Upstream creates the bucket, its anonymous read policy and its CORS rule in a one-off
`rustfs/rc` container that runs three commands and exits. That has no legal home in this
repository — every `*.container.j2` must declare `Restart=always` and a `HealthCmd=`, which a
process that finishes can give neither.

So it is `hooks.after_change`, and it execs into **the app** rather than into the file store:

- `rustfs/rustfs` installs ca-certificates, coreutils, curl and tzdata. No `rc`, no
  scripting runtime, nothing that can sign an S3 request.
- the app's image is busybox plus a distroless node, and it already holds the S3 credentials
  — `podman exec` starts a process with the container's _configured_ environment, which is
  exactly the `Secret=…,type=env` lines in its unit.

`config/s3-init.js` therefore signs its own requests with node's crypto and no dependency
(reaching into the app's bundled `node_modules/.pnpm` would break on a version bump). It is
idempotent: creating an existing bucket is success either way — S3 answers 409, RustFS
answers plain 200, and the script accepts both — and the policy and CORS rule are
whole-document PUTs. Its signing math was checked against AWS's published SigV4
`get-vanilla` test vector before it ever ran on a host, and a hand re-run in a VM printed
`s3-init: bucket lobe ready (create 200)` against the bucket it had already made.

## The gateway's key pair, and what it means on a test host

Gateway mode runs agents server-side and streams them to the browser. Three secrets wire it:

| secret                  | where it goes                                             |
| ----------------------- | --------------------------------------------------------- |
| `gateway_service_token` | both containers — the shared token the app presents       |
| `jwks_key`              | the app only: an RS256 key **pair** as a JWKS JSON string |
| `jwks_public_key`       | the gateway only: the same key without its private fields |

The gateway verifies the browser's session token itself with the public half, which is what
lets the websocket skip a round trip. The two halves must be halves of _one_ pair. Generate
them with upstream's own one-liner (`docker-compose/setup.sh`, `generate_jwks_key_pair`):

```sh
node -e 'const c=require("crypto");const {privateKey,publicKey}=c.generateKeyPairSync("rsa",{modulusLength:2048});const meta={alg:"RS256",kid:c.randomBytes(8).toString("hex"),use:"sig"};console.log(JSON.stringify({keys:[{...privateKey.export({format:"jwk"}),...meta}]}));console.log(JSON.stringify({keys:[{...publicKey.export({format:"jwk"}),...meta}]}))'
```

First line is `jwks_key`, second is `jwks_public_key`.

**A wrong value here is silent, which is the thing to know.** Neither side parses the key
at startup: the gateway reads `JWKS_PUBLIC_KEY` into a plain config struct and only
unmarshals it the first time it verifies a token (`internal/gateway/auth.go`), so with a
value that is not a JWKS document both containers still start, both still answer their
health checks, all three routes still serve — and every agent run through the gateway
fails. Measured with a placeholder value in a VM. The app says so once, in its own startup
banner, and that line is the only warning there is:

```
⚠️ Gateway Mode is not configured
  • JWKS_KEY (not valid JWKS JSON)
  • JWKS_PUBLIC_KEY
```

So after filling these three, read the app's first journal page rather than trusting a
green health check.

## The app binds what `HOSTNAME` says, and podman decides that

The one defect this folder cost a VM cycle on, written down because it is invisible from
outside. Next.js standalone binds `process.env.HOSTNAME || '0.0.0.0'`, and podman injects
the container's (here the pod's) hostname into the runtime environment _after_ the
configured one — so the server came up on the pod's interface address alone:

```
tcp 0 0 192.168.122.175:3210 0.0.0.0:* LISTEN 22/next-server
```

The pod's `PublishPort` forwards to exactly that address, so the route answered 200 through
Traefik while `127.0.0.1:3210` inside the pod was refused — which breaks the app's own
health check and the gateway's `LOBE_API_BASE_URL` with it. An `Environment=HOSTNAME=0.0.0.0`
line does **not** fix it, and the two views disagree in a way worth knowing:
`podman exec … printenv HOSTNAME` reports the configured `0.0.0.0` while the server's
`/proc/<pid>/environ` holds `HOSTNAME=lobehub`. The fix is one `export` in the entrypoint
shell that already composes `DATABASE_URL`, because a variable exported there belongs to the
process it execs.

## Secrets

| secret                  | what it is                                                    |
| ----------------------- | ------------------------------------------------------------- |
| `database_password`     | postgres superuser; must be hex, see below                    |
| `key_vaults_secret`     | `openssl rand -base64 32` exactly — not hex, see below        |
| `auth_secret`           | signs the app's own sessions                                  |
| `gateway_service_token` | shared between app and gateway                                |
| `jwks_key`              | the RS256 pair, app side                                      |
| `jwks_public_key`       | the same pair without private fields, gateway side            |
| `s3_access_key`         | the file store's root credential, and the app's S3 access key |
| `s3_secret_key`         | the matching secret key                                       |
| `searxng_secret`        | the search instance's own signing secret                      |

The backup sidecar's credential is **not** in that list and must not be: Cradle generates it
on the host, one `openssl rand -base64 32` under `/var/lib/cradle/backup-clients/lobehub`,
and syncs the same string into this service's `kopia_password` and the kopia built-in's
`client_lobehub`. A service that declares the name is refused.

`database_password` **must be hex** or otherwise URL-safe: it is a field inside
`DATABASE_URL`, and a value containing `@`, `:` or `/` silently produces a different
connection. The URL is assembled inside the container by a shell that replaces the image's
entrypoint and then execs what it would have run — the password is mounted as a file, never
written into a world-readable unit.

`key_vaults_secret` is the one value that may **not** be hex like the rest. The app decodes
it as base64 and demands 16, 24 or 32 bytes; a 96-character hex string decodes to 48 and is
rejected. The failure is quiet in the worst way — the server still starts and still answers
its health check, and only key-vault operations fail, which looked like this
every few seconds:

```
[GatewayManager] Sync error for telegram: Error: `KEY_VAULTS_SECRET` must be 16, 24, or 32
bytes (128, 192, or 256 bits) when base64 decoded, got 48 bytes.
```

So generate it with `openssl rand -base64 32`, which is what upstream's own setup script
does. A hex value of the kind every other secret here uses produces exactly the log above.

The database user is `postgres`, the superuser, and not a role of its own: the app's
migrations create the `pg_search`, `vector` and `pg_cron` extensions, which is a superuser
operation. That is upstream's shape, not a shortcut taken here.

## What the first start looks like

The app runs its migrations before it serves, and it does not wait for the database to
accept connections: `After=`/`Wants=` order the units, not their readiness. So a first start
against an empty cluster logs

```
[Database] Start to migration...
❌ Database migrate failed … cause: Error: connect ECONNREFUSED 127.0.0.1:5432
```

and exits. `Restart=always` brings it back, the cluster finishes initialising ParadeDB's
three extensions, and a later attempt migrates and serves — measured in a VM, where the
service was healthy well inside its 300 s `HealthStartPeriod`. Worth knowing so the first
journal is not mistaken for a broken deployment.

## Volumes

| volume     | class | owner | what it holds                                |
| ---------- | ----- | ----- | -------------------------------------------- |
| `files`    | pool  | 10001 | every attachment and knowledge-base document |
| `database` | fast  | 999   | the postgres cluster                         |
| `cache`    | fast  | 999   | redis' append-only log                       |
| `backups`  | pool  | —     | the nightly SQL dump                         |

`files` is on the pool because it grows without a ceiling and the SSD is 120G. `999` on the
database and not `70`: `paradedb/paradedb:*-pg17` is `FROM postgres:17-trixie`, a Debian
base, where the postgres user is 999 — the alpine postgres images used elsewhere in this
repo are 70. Verify once on the running container rather than trusting this table:

```sh
doas /usr/local/sbin/podman-as svc-lobehub podman exec lobehub-database id -u postgres
```

## The provider keys live in the database, not in a secret

Model provider keys are entered in the UI and stored encrypted with `key_vaults_secret`.
Deliberate: a provider can be swapped without a commit. The consequence worth knowing rather
than discovering is that losing `key_vaults_secret` loses those keys — not the
conversations, which are plain rows.

## Login

The app's own email/password, with `AUTH_ALLOWED_EMAILS` closing registration to one
address (`service.yml`'s `config.allowed_emails`). An empty value would let anyone who
reaches the route sign themselves up, and the route is only as private as the VPN in front of
it. Email verification stays off, which is what makes this work with no SMTP configuration
at all — turn it on (`AUTH_EMAIL_VERIFICATION=1`) only together with the `SMTP_*` variables.

## Reaching the MCP hub

Not configured yet, and the next thing worth doing. LobeHub is an MCP client; the hub's one
endpoint aggregates every adapter behind six meta-tools, so it is the single server to
register rather than four. The token is created on the host and printed once —
`hosts/StorageBaby/services/mcp/README.md`, "Connecting a client" — and the registration
itself is a UI step, so it is an operator action and not a file this folder renders.

## Backup

`files` and `backups` are snapshotted at 03:00 by the generated sidecar. The database goes in
as the 02:30 `pg_dump` in `backups`, never as its data directory, and `cache` is not carried
at all — everything in it is regenerable by definition.

Restore the database into a running cluster:

```sh
doas /usr/local/sbin/podman-as svc-lobehub podman exec -i lobehub-database \
	psql --single-transaction --set ON_ERROR_STOP=on -U postgres -d lobechat < lobechat.sql
```

Plain SQL and uncompressed on purpose: the sidecar snapshots it into a deduplicating
repository, where a compressed dump dedups against nothing. `psql`, not `pg_restore`.
