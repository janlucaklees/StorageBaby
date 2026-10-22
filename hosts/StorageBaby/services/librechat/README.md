# LibreChat

A chat front-end for commercial LLM providers, with conversation search and
chat-with-your-documents. <https://chat.mayu.klees.io> — Mayu's, under her own label, and
**public**: she opens it on a phone on mobile data, which is the one place the VPN is not
already on. The app's own login is therefore the only lock, which is what the closed
registration below is about. Upstream: <https://github.com/danny-avila/LibreChat>.

## Five parts, one name

| container            | image                                 | what it is                              |
| -------------------- | ------------------------------------- | --------------------------------------- |
| `librechat-app`      | `librechat-api:v0.8.8`                | the application and its API             |
| `librechat-database` | `mongo:8.0.20`                        | accounts, conversations, messages       |
| `librechat-search`   | `getmeili/meilisearch:v1.35.1`        | the conversation search index           |
| `librechat-rag`      | `librechat-rag-api-dev-lite:v0.9.0`   | embeds and retrieves uploaded documents |
| `librechat-vectordb` | `pgvector/pgvector:0.8.0-pg15-trixie` | the embeddings                          |

Only the app answers a browser; the other four are reached on the pod's own loopback, which
no other service user on this host can see. The app's tag is the newest release — **every**
LibreChat release is flagged pre-release upstream, so "the latest stable one" does not
exist and `v0.8.8` is simply the newest. The RAG service carries its own release numbering
(`v0.9.0` against the app's `v0.8.8`), which is the case the contract warns about: a
GitHub release tag and a published image tag are not the same thing, and here even the
repositories count differently. Upstream floats this one on `latest`; it is pinned here so
that a converge cannot move it.

Two parts of upstream's compose are deliberately absent. The **nginx client** exists to
terminate TLS and serve the static bundle, which is Traefik's job here and the app's own
server does the rest. The **admin panel** is a second web application on a second hostname,
and nothing about one account needs it.

## Read the image, not the repository's Dockerfile

The first converge shipped three defects with one cause, so it is worth stating as a rule:
**a published image is not the repository's root `Dockerfile`.** Both of these projects
build several artefacts, and the one pinned here is not the one that file describes.

| what was assumed         | what the image actually does                                                                                                                                |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Cmd `npm run backend`    | `node server/index.js` from `/app/api` — the other form exits 1 every five seconds with `Missing script: "backend"`                                         |
| `USER node`, so uid 1000 | no USER and no privilege drop: the process is container root, so volumes owned by the mapped 1000 are unwritable                                            |
| the RAG image has curl   | it has neither curl nor wget, so a curl health check kills the container every two and a half minutes while its own log says `Application startup complete` |

All three are invisible from outside: the pod runs, four of five containers are healthy, and
the route answers with Traefik's bad-gateway page. `podman image inspect` and one throwaway
`podman run --rm --entrypoint /bin/sh` answer all three questions in a minute, and that is
the pre-flight a version bump here needs as much as the first placement did.

## The certificate, and the DNS trap underneath it

The host declares `mayu.klees.io` in `cert_zones`, which is a line in `host.yml` and not
something this folder could decide — a certificate zone is the host's own fact. It is
needed because `*.klees.io` is one label deep and this name is two.

What makes that worth writing down is that **DNS needed nothing**. Porkbun answers the
existing `*.klees.io` wildcard at any depth, measured before the change:

```sh
dig +short nothing.mayu.klees.io A # 109.91.226.189 -- the same as anything.klees.io
```

So her name reached this host before anything could serve it a certificate that covers it,
and the only symptom would have been the browser's. Nothing in this repository checks the
pairing; Cradle's static suite refuses the combination for its own fixture hosts only.

`probe_path` is `/health`, and the app's own health check asks `/readyz` instead. The app
registers three endpoints ahead of every other route: `/health` and `/livez` answer 200
whenever the server is listening, while `/readyz` answers 503 until the post-listen
initialisation has finished (`api/server/index.js`). The first is the right question for
"does this route arrive", the second for "is this container healthy" — during that window
the app would serve the SPA shell and fail every request behind it.

## HOST=0.0.0.0, or nothing answers at all

The app binds `process.env.HOST || 'localhost'` (`api/server/index.js`). A pod's published
port arrives on the pod's interface address, not on its loopback — so with the default the
server would listen where Traefik cannot reach it, and nothing in the app's own log would
say so. Upstream's compose sets the same value for the same reason. This is the single line
whose absence looks exactly like a wrong port.

## Mongo has a password, which upstream does not do

Upstream runs `mongod --noauth` and relies on nothing being able to reach the port. That is
true here too — the pod's network namespace belongs to `svc-librechat` alone — so this is a
second lock rather than the only one. It costs one file, because the image takes it as one:
with `MONGO_INITDB_ROOT_USERNAME` and `MONGO_INITDB_ROOT_PASSWORD_FILE` set, the
entrypoint creates the user with the root role **and** adds `--auth` to mongod itself
("if we have a username/password, let's set `--auth`", `docker-entrypoint.sh`). The `_FILE`
form is read by the entrypoint's own `file_env`, so the value never enters any
environment.

Three consequences, all of them things to know before touching this service:

- **It acts on an empty `/data/db` only.** A volume that already holds a cluster keeps
  whatever users it had. Restoring one from a snapshot therefore does not recreate this
  user — see "Restoring" below.
- **The password must be URL-safe.** The app reads one `MONGO_URI` and no file form of it,
  so `config/mongo-uri.sh` composes the string from the password file and the app's unit
  sources it. A value containing `@`, `:` or `/` would silently produce a different
  connection, or none. Hence hex.
- **A `podman exec` into the app does not have `MONGO_URI`.** `podman exec` starts a
  process with the container's _configured_ environment — the `Environment=` and
  `Secret=…,type=env` lines — and a variable exported by a sourced script belongs to pid 1
  alone. So every operator command that talks to the database sources the script first,
  which is the form used throughout below. openproject's `config/db-url.sh` carries the
  error that taught this.

## The secrets, and how to fill them

Eight, in one file — set on storagebaby, and this is what to regenerate them with:

```sh
mise run sops hosts/StorageBaby/services/librechat/secrets.sops.yaml
```

| key                  | what it is                                                         | generate with           |
| -------------------- | ------------------------------------------------------------------ | ----------------------- |
| `mongo_password`     | the database's root user                                           | `openssl rand -hex 32`  |
| `vectordb_password`  | the vector database's user                                         | `openssl rand -hex 32`  |
| `meili_master_key`   | the search index's master key                                      | `openssl rand -hex 32`  |
| `creds_key`          | encrypts her stored provider keys — **64 hex characters, exactly** | `openssl rand -hex 32`  |
| `creds_iv`           | the matching IV — **32 hex characters, exactly**                   | `openssl rand -hex 16`  |
| `jwt_secret`         | signs her session, and the token the RAG service verifies          | `openssl rand -hex 32`  |
| `jwt_refresh_secret` | signs the refresh token                                            | `openssl rand -hex 32`  |
| `openai_api_key`     | the chat and embeddings key                                        | from the OpenAI account |

Hex throughout, and for one of them that is a requirement rather than a habit:
`mongo_password` is composed into a URL, so a base64 value's `+`, `/` or `=` could change
the connection or break it. The rest are hex because one rule is easier to keep than six,
and because a hex value never needs quoting in YAML.

`creds_key` and `creds_iv` are the two whose **length** is load-bearing: they are read as
hex into an AES-256-CBC key and IV, and a wrong length fails at the first attempt to save a
credential rather than at startup. `meili_master_key` must be at least 16 bytes, which
`MEILI_ENV=production` enforces by refusing to start without it — a loud failure, on
purpose.

One key covers both halves of the OpenAI bill: the app uses it for chat and the RAG service
uses the same value for embeddings (`text-embedding-3-small`, written out in its unit so
that an upstream change of default cannot quietly re-embed her documents against a
different model).

## The first account

Registration is closed from the first converge (`ALLOW_REGISTRATION=false`), so the account
is made once by hand. The script prompts for everything it is not given, which is how the
password stays out of the shell history:

```sh
doas /usr/local/sbin/podman-as svc-librechat podman exec -it librechat-app \
	/bin/sh -c '. /config/mongo-uri.sh; npm run --prefix /app create-user'
```

Two halves of that line are load-bearing, both measured rather than assumed:

- **`. /config/mongo-uri.sh`** — without it the command has no `MONGO_URI` and dies with
  "Please define the MONGO_URI environment variable". A `podman exec` does not inherit what
  pid 1's shell exported.
- **`--prefix /app`** — the container's working directory is `/app/api`, which is the
  backend workspace, and `create-user` is a script of the repository root's manifest. Run
  without the prefix, npm resolves the name against the workspace and answers
  `Missing script: "create-user"`.

`npm run --prefix /app reset-password` is the same shape when she forgets her password.

## Backups

The sidecar carries `images`, `uploads`, `skill` and `backups` at 03:00. `backups` holds
what `librechat-dump.timer` writes at 02:30: a `mongodump` archive of the whole database
and a plain `pg_dump` of the embeddings, both uncompressed so they dedup against yesterday
in the kopia repository, both written to a `.tmp` name and renamed, because the sidecar may
read the directory at any moment.

Not carried, each on purpose: the **Mongo and Postgres data directories** (a database is
backed up as its dump), and the **search index** (regenerable from the database by
definition). What that means in practice is that a restore brings back her conversations,
her files and their embeddings, and the search index rebuilds itself.

One timer for two databases, which is the platform's convention of one `<name>-dump` pair
per service. They run in order and the unit stops at the first failure — the nightly
maintenance mail lists every failed unit of every service manager, so a dump that did not
happen is reported rather than silently skipped.

### Restoring

Mongo first, from the archive. `--drop` because restoring over a live collection otherwise
merges rather than replaces:

```sh
doas /usr/local/sbin/podman-as svc-librechat podman exec -i librechat-database \
	sh -c 'mongorestore --drop --archive=/backups/LibreChat.archive \
	--username=librechat --password="$(cat /run/secrets/mongo_password)" \
	--authenticationDatabase=admin'
```

Then the embeddings, with the form every postgres restore on this platform uses — plain
SQL, so `psql` and not `pg_restore`:

```sh
doas /usr/local/sbin/podman-as svc-librechat podman exec -i librechat-vectordb \
	psql --single-transaction --set ON_ERROR_STOP=on --username=librechat \
	--dbname=librechat --file=/backups/librechat.sql
```

**If you restore the Mongo _volume_ instead of the archive**, the root user is not
recreated: `MONGO_INITDB_ROOT_*` only acts on an empty data directory, so the cluster comes
back with whatever credentials it had when it was snapshotted. The archive path above
avoids the problem entirely, which is why it is the documented one.

## What is not here, and why

- **No `librechat.yaml`.** The app runs on its defaults, which is enough for one user and
  one provider. It would go in `config/` with `CONFIG_PATH=/config/librechat.yaml`, and
  that is where a restricted model list or a different file-upload policy would be
  written.
- **No volume for `/app/data`.** Upstream gives it a named volume; what it holds is the
  temporary-credentials file (`LIBRECHAT_TEMP_CREDENTIALS_PATH`), and the image creates the
  directory itself. Losing it costs a re-authorisation, not data.
- **No volume for the logs.** The app logs to stdout as well, which is the journal:
  `cradle-svc logs librechat` follows the pod.
- **`MEILI_ENV=production`.** The alternative is `development`, where the master key is
  optional — and an unkeyed index on a shared loopback is a worse default than a container
  that refuses to start.
- **No redis.** `SCHEDULES_SINGLE_PROCESS=true` in the app's unit makes the claim a redis
  container would otherwise have to prove: that exactly one app process runs here. Without
  either, the scheduler never arms and every schedule write answers 503 for the life of the
  process. The claim holds as long as this pod has one `librechat-app` — so a second app
  container here would need `USE_REDIS_STREAMS` and the container behind it, not a copy of
  this line.

## Before the first converge

Three things neither this folder nor Cradle can check. The first two are settled; the
third is for the first start.

- **Fill the secrets before this folder reaches master.** Done for storagebaby, and it is
  first here because of what it costs to get wrong on the next host: both databases bake
  their password into their data directory at first start, and the deploy timer converges
  within five minutes of a push — so a placeholder that arrives first _becomes_ the
  password, and the value set afterwards is ignored for the life of the volume. The only
  fix then is wiping `database` and `vectordb` and restoring. The failure is **silent**:
  `meili_master_key` refuses to start on a bad value and `openai_api_key` fails at the
  first message, but Mongo and Postgres accept a placeholder and run happily on it.
- **MongoDB 8 needs AVX**, or `mongod` dies with `Illegal instruction` and no other
  message. Settled on this host: `lscpu` reports `avx` and `avx2`.
- **Memory.** Five containers, two databases and a search index are a real addition to this
  machine. Worth a `free -g` before and after the first start.
