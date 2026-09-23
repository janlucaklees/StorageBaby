# OpenArchiver

The mail archive, reachable at `openarchiver.<host domain>` —
`openarchiver.home.klees.io` on storagebaby. Migrated from the old
`openarchiver/docker-compose.yml`: five containers that used to be a compose
project are one Podman **pod** now, plus the two things the platform adds by
declaration — a nightly database dump and a Kopia backup client.

## The pod

| Part                       | Image                                        | Port | Updates       |
| -------------------------- | -------------------------------------------- | ---- | ------------- |
| `openarchiver-app`         | `docker.io/logiclabshq/open-archiver:v0.6.0` | 3000 | pinned in git |
| `openarchiver-database`    | `docker.io/library/postgres:17-alpine`       | 5432 | auto          |
| `openarchiver-cache`       | `docker.io/valkey/valkey:8-alpine`           | 6379 | auto          |
| `openarchiver-meilisearch` | `docker.io/getmeili/meilisearch:v1.38`       | 7700 | pinned in git |
| `openarchiver-tika`        | `docker.io/apache/tika:3.2.2.0-full`         | 9998 | pinned in git |
| `openarchiver-backup`      | `docker.io/kopia/kopia:0.23.1`               | —    | generated     |

`openarchiver.pod` owns the network namespace all six share, so every part talks
to every other over `127.0.0.1` on its own upstream port — `REDIS_HOST` is
`127.0.0.1`, `MEILI_HOST` is `http://127.0.0.1:7700` and `TIKA_URL` is
`http://127.0.0.1:9998`, none of them a service name any more.

`openarchiver-backup` is **not** in this folder. The role generates it from
`backup:` in `service.yml` and joins it to this pod — which is why a service with
a `backup` block must define a `.pod`.

Three of the five parts are pinned. The app is, because an OpenArchiver upgrade
carries schema migrations. meilisearch is, because its on-disk index format
changes between minor versions and an upgraded binary will not open an older
index without a dump-and-restore — precisely the decision a nightly timer must
not make. tika is, because `3.2.2.0-full` is a specific build with the OCR and
parser set attachment extraction needs; `latest` would swap that out silently.
postgres and valkey ride `AutoUpdate=registry` within their major.

### The published port is 3001, the app's port is 3000

`PublishPort=127.0.0.1:3001:3000`. OpenArchiver's frontend listens on 3000 and
that stays as it is inside the pod; on the host, yuzukam already publishes 3000,
and two services cannot share a loopback port. So the route's port — the one in
`service.yml` and in the Traefik file — is 3001, and the pod's publish line is
the only place the two numbers meet.

### `AddHost=kopia.<domain>:host-gateway`

On the pod, for the backup sidecar: it connects to the Kopia server the way every
other client does, through Traefik on the host (`https://kopia.<domain>`), and
inside the pod there is no DNS that answers for that name. Harmless on a host
where DNS does answer.

## Secrets, and why three of them are shaped differently

OpenArchiver supports no `_FILE` variables at all: every credential it takes, it
takes from the environment. The old stack solved that by reading six gitignored
`.secret` files into plain compose variables. Here the same six values are
podman secrets, and they reach their consumers in three different ways.

| Secret                   | Consumer         | How                                                                |
| ------------------------ | ---------------- | ------------------------------------------------------------------ |
| `database_password`      | app              | file, read by the app's entrypoint shell                           |
| `database_password`      | database         | file, `POSTGRES_PASSWORD_FILE`                                     |
| `redis_password`         | cache, app       | file (cache), env-type secret (app)                                |
| `meili_master_key`       | meilisearch, app | env-type secret in both                                            |
| `jwt_secret`             | app              | env-type secret                                                    |
| `encryption_key`         | app              | env-type secret                                                    |
| `storage_encryption_key` | app              | env-type secret                                                    |
| `kopia_password`         | backup           | `hosts/<host>/secrets/kopia-clients.sops.yaml`, key `openarchiver` |

**Env-type secrets** (`Secret=<name>,type=env,target=<VAR>`) are podman's answer
to an application that only reads the environment. The unit carries the secret's
_name_, never its value, and podman resolves it from the service user's secret
store when the container starts. Measured on the test VM:

```
$ podman inspect openarchiver-app        # Config.Env, one line per variable
  "REDIS_PASSWORD=*******",
  "JWT_SECRET=*******",
  "TIKA_URL=http://127.0.0.1:9998",
```

Seven literal asterisks — podman masks an env secret in its inspect output, and
the unit file in `/etc/containers/systemd/` holds only
`Secret=jwt_secret,type=env,target=JWT_SECRET`. An `Environment=` line would have
put the value in both.

**The database password is the exception**, because OpenArchiver does not take
one. It takes `DATABASE_URL`, a single string with the password inside it. An
assembled URL in the unit would put the password into `/etc/containers/systemd/`
for anyone to read, and an env-type secret cannot help because the secret is only
a part of the value. So the unit replaces the image's entrypoint with a shell:

```ini
Entrypoint=/bin/sh
Exec=-c 'export DATABASE_URL="postgresql://openarchiver:$(cat /run/secrets/database_password)@127.0.0.1:5432/openarchiver"; exec docker-entrypoint.sh pnpm docker-start:oss'
```

systemd expands `$VAR` and `${VAR}` in an `ExecStart=` but leaves `$(` alone, so
the command substitution survives Quadlet's generated line and runs in the
container's shell, which is the only place the secret exists.

The `exec` at the end is not decoration: without it the shell stays around as pid
1 and swallows the signals systemd sends to stop the container. What it execs is
the image's own entrypoint and command, read off the image rather than guessed:

```
$ podman image inspect --format '{{.Config.Entrypoint}} {{.Config.Cmd}}' \
    docker.io/logiclabshq/open-archiver:v0.6.0
[docker-entrypoint.sh] [pnpm docker-start:oss]
```

A version bump therefore has to check that line again — if upstream changes its
command, this unit keeps running the old one.

The database password has to be **URL-safe**: it is a field inside
`DATABASE_URL`, so a `/`, `@` or `:` in it would change what the URL means. The
value carried over from the old stack is alphanumeric, and the test hosts'
generator writes hex.

An env-type secret does reach a `podman exec` into the container — measured, all
five are there with their real lengths. `DATABASE_URL` is the one that is not:
the entrypoint shell exports it into its own process, so pid 1 has it and an exec
session does not. Worth knowing before debugging the app by hand.

### `JWT_EXPIRES_IN`

Not a secret, but the same class of trap, and it was **not** in the old compose
file: `createServer` validates `JWT_SECRET` _and_ `JWT_EXPIRES_IN` and throws
naming both whichever is missing. The failure is close to invisible from outside — the frontend
starts anyway, binds 3000 and answers 500 from its own proxy, so the pod looks up
while nothing works:

```
[1] [0] ERROR (201): Failed to start the server:
[1] [0]     error: {}
[1] [1] Failed to get auth status: {"status":"error","statusCode":500, ... }
```

(The logged error is `{}` because pino has no serializer for it; the real message
comes out of running `createServer([])` by hand in the container.) The value is
`service.config.jwt_expires_in`, `7d` — upstream's own default — and it is a
config key rather than a literal so a host can shorten the session lifetime
without touching the unit.

**The valkey password** is the same problem in the other direction: valkey takes
it as a command-line flag, and argv is world-readable in `ps`. So the command is
a shell that reads the file, and so is the health probe:

```ini
Exec=sh -c 'exec valkey-server --requirepass "$(cat /run/secrets/redis_password)"'
HealthCmd=sh -c 'valkey-cli --no-auth-warning -a "$(cat /run/secrets/redis_password)" ping | grep -q PONG'
```

### The values are the real ones

Unlike paperless, this folder's `secrets.sops.yaml` is **not** `REPLACE_ME`: the
six values are the ones the old compose stack ran with, carried over from its
`openarchiver_*.secret` files and encrypted under the storagebaby rule. That is
not optional politeness — `encryption_key` and `storage_encryption_key` decrypt
data already in the archive, `jwt_secret` signs the sessions, and
`database_password` is the password of the database being migrated in. A new
value for any of them is data loss or a lockout.

`kopia_password` is the client half of the shared value the Kopia server knows as
`client_openarchiver`; `hosts/storagebaby/services/kopia/README.md` has the whole
mechanism. Test hosts generate all seven fresh per run.

Two of them have a **shape**, not just a length: `ENCRYPTION_KEY` and
`STORAGE_ENCRYPTION_KEY` must be 64 hex characters (32 bytes) or the app's
workers throw on startup and crash-loop while the pod itself looks healthy. That
is why the integration scenario generates 64-character hex for every test secret
rather than base64 — `tests/integration/molecule/test-ci/prepare.yml` says so at
the generator.

## Health checks

Every container declares one — the platform's contract, and the integration suite
runs `podman healthcheck run` against each of them. Three are worth a note:

- **tika** ships neither `curl` nor `wget`, and podman runs a `HealthCmd` through
  the image's `/bin/sh`, which there is dash and has no `/dev/tcp`. So the probe
  names the shell that does: `bash -c "exec 3<>/dev/tcp/127.0.0.1/9998"` — a bare
  TCP connect, which is all a "is the server listening" check needs.
- **meilisearch** answers `/health` without the master key; every other route
  needs it, so `/health` is the only endpoint a probe can use. The image ships
  `curl` for exactly this.
- **the app** has no `curl` either — its image is Alpine-based — but it does have
  busybox `wget`. Its probe asks **both** of the processes this container runs
  under `concurrently`, and the backend first:

  ```ini
  HealthCmd=sh -c 'wget -q -O /dev/null http://127.0.0.1:4000/ && wget -q -O /dev/null http://127.0.0.1:3000/'
  ```

  Asking only 3000 tested the API by accident. Measured on the test VM, with the
  backend process killed and the frontend left running:

  ```
  wget http://127.0.0.1:3000/   → HTTP/1.1 503 Service Unavailable   (exit 1)
  wget http://127.0.0.1:4000/   → connection refused                 (exit 1)
  ```

  So the old probe did fail — but only because rendering `/` proxies to the API.
  That is an upstream implementation detail, not a property of the check: a
  release that served that page from a cache, or a change of landing route, would
  leave a probe on 3000 reporting a healthy container in front of an application
  that can do nothing. Asking 4000 says what it means. The backend's own `GET /`
  answers `Backend is running!!` — there is no `/health` route in OpenArchiver
  0.6, and this is the one unauthenticated endpoint it has.

  It also gets `HealthStartPeriod=120s`: the first start runs `pnpm install` and
  the schema migrations, and without it `HealthOnFailure=kill` would kill a
  container that is merely still starting.

## Trusted proxies

There is nothing to set. OpenArchiver 0.6 has no trusted-proxy setting at all —
no `TRUSTED_PROXIES`, no `X-Forwarded-For` list — and the old compose stack set
none either. `ORIGIN` is what SvelteKit checks a form POST's `Origin` header
against, and it is the public URL, not a proxy address.

## The database dump

```
openarchiver-dump.timer    daily at 02:30
openarchiver-dump.service  podman exec openarchiver-database pg_dump -Fc … > /backups/openarchiver.dump
```

Plain systemd user units in `~svc-openarchiver/.config/systemd/user/`, not Quadlet
ones. 02:30 is half an hour before the snapshot at 03:00, so every snapshot
carries a dump from the same night.

The dump is written to `<name>.dump.tmp` and renamed, so `/backups` never holds a
half-written file for the sidecar to pick up — `mv` within one volume is atomic.

The unit is ordered `After=openarchiver-database.service` and, more importantly,
`Requisite=` it. Without that, a dump attempted while the pod is down would fail
its `podman exec` and leave **yesterday's** `openarchiver.dump` sitting in
`/backups` for the sidecar to snapshot as though it were tonight's. `Requisite=`
does not start the database — it refuses to run at all without it, which turns a
silently stale backup into a failed unit somebody can see.

**A database is backed up as a dump, never as its data directory.** `database` is
therefore not in `backup.paths`; `backups` is. Snapshotting a running postgres
data directory copies files mid-write and restores to a database that may not
open at all.

## Backups

```yaml
backup:
  paths: [data, backups]
  schedule: '03:00'
  retention: { latest: 3, daily: 7, weekly: 4, monthly: 12, annual: 3 }
```

The role generates `openarchiver-backup.container` from this, mounts the two
volumes read-only at `/data/<volume>`, connects to the Kopia server as
`openarchiver@<host>` and leaves a scheduler running, so the snapshots happen at
03:00 without a timer of their own. `ansible/roles/service/README.md` has the
sidecar's mechanics, and the server lists

```
openarchiver@<host>:/data/data
openarchiver@<host>:/data/backups
```

`data` is the archive itself — the mail bodies and attachments under
`STORAGE_LOCAL_ROOT_PATH`, encrypted at rest with `storage_encryption_key` —
and `backups` carries the nightly dump. `cache` and `meilisearch` are absent on
purpose: the first is a job queue, the second an index OpenArchiver rebuilds from
the archive and the database.

## Dropped from the compose stack

- **The Traefik labels.** Podman containers are invisible to Traefik's Docker
  provider; the role writes a file-provider route from `domain` and `port` in
  `service.yml` instead.
- **The Watchtower labels.** `AutoUpdate=registry` and the service user's
  `podman-auto-update.timer` replaced them.
- **`depends_on: condition: service_healthy`.** Inside a pod the parts share a
  namespace and start together; the app's `After=`/`Wants=` order them, and
  `HealthStartPeriod=120s` covers the window in which the database is not up yet.
- **The `cache` named volume is kept**, as `cache` — valkey's `/data` holds the
  job queue across a restart.

## Migrating the data

`openarchiver/docker-compose.yml`'s three named volumes and one bind map onto
this folder's five:

| Old (rootful compose)                      | New                                                    |
| ------------------------------------------ | ------------------------------------------------------ |
| `/pool/apps/openarchiver/volumes/data`     | `/pool/apps/openarchiver/data`                         |
| `openarchiver_database` (docker volume)    | `/var/lib/storagebaby/fast/openarchiver/database`      |
| `openarchiver_cache` (docker volume)       | `/var/lib/storagebaby/fast/openarchiver/cache`         |
| `openarchiver_meilisearch` (docker volume) | `/var/lib/storagebaby/fast/openarchiver/meilisearch`   |
| —                                          | `/var/lib/storagebaby/fast/openarchiver/backups` (new) |

A docker named volume lives at `/var/lib/docker/volumes/openarchiver_<name>/_data`.
The repo's root README has the recipe and the ownership rules: the moved trees
have to be chowned under `podman unshare` as `svc-openarchiver`, not on the host
directly, and the database directory has to end up owned by postgres's
in-container uid (70 in `postgres:17-alpine`), which is a subuid of
`svc-openarchiver` on the host.
