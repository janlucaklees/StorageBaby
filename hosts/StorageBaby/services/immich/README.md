# Immich

The photo library, reachable at `immich.<host domain>` — `immich.home.klees.io`
on storagebaby. Migrated from the old `immich/docker-compose.yml`: four
containers that used to be a compose project are one Podman **pod** now, and the
hand-written `backup/` sidecar is gone — the role generates it from `backup:` in
`service.yml`.

## The pod

| Part              | Image                                                  | Port | Updates       |
| ----------------- | ------------------------------------------------------ | ---- | ------------- |
| `immich-server`   | `ghcr.io/immich-app/immich-server:v3.2.0`              | 2283 | pinned in git |
| `immich-ml`       | `ghcr.io/immich-app/immich-machine-learning:v3.2.0`    | 3003 | pinned in git |
| `immich-database` | `ghcr.io/immich-app/postgres:14-vectorchord…` (digest) | 5432 | pinned in git |
| `immich-cache`    | `docker.io/library/redis:alpine`                       | 6379 | auto          |
| `immich-backup`   | `docker.io/kopia/kopia:0.23.1`                         | —    | generated     |

`immich.pod` owns the network namespace all five share, so `DB_HOSTNAME` and
`REDIS_HOSTNAME` are `127.0.0.1` rather than compose service names.

Everything but redis is pinned, and pinned together: server and machine learning
are one release, taken from `config.version` so a bump is one line, and the
database is not stock
postgres but Immich's build of it with `vectorchord` and `pgvectors` — the
extension versions belong to the server version, so the digest moves with it.
Upstream pins the same digest in its own compose file.

`immich-backup` is **not** in this folder; the role generates it into this pod.

### `AddHost=immich-machine-learning:127.0.0.1`

The machine-learning URL is not an environment variable — it is a system setting
stored in the database, and both a fresh install and the database migrated off
the compose stack hold the compose service name `immich-machine-learning`. In a
pod there is no such name and nothing that resolves it, so the pod aliases it to
the loopback address the ML container really listens on. The alternative would be
a hand edit in the admin UI as part of every deploy, which is exactly what this
platform is for not doing.

### `AddHost=` — the pod has to be able to reach Traefik

Nothing in `quadlet/` writes these lines; the `service` role does, into a Quadlet
drop-in on the pod, for **every** route name placed on the host — `kopia.<domain>`,
which the backup sidecar connects to, along with everything else the host serves.
`ansible/roles/service/README.md`, "Reaching another service through Traefik", has the
mechanism.

## Secrets

| Secret              | Where from                         |
| ------------------- | ---------------------------------- |
| `database_password` | `secrets.sops.yaml` in this folder |
| `kopia_password`    | `secrets.sops.yaml` in this folder |

> **`database_password` is `REPLACE_ME` and must be filled before the first
> deploy on storagebaby**, with
>
> ```sh
> mise run sops hosts/StorageBaby/services/immich/secrets.sops.yaml
> ```
>
> It has to be **the password of the database that is migrated in**. The postgres
> image only applies `POSTGRES_PASSWORD` when it initialises an empty data
> directory; moved-in data keeps the old stack's password, and a different value
> here means the server cannot log in to its own database.

`database_name` is `postgres`, not `immich`, for the same reason: that is what
the old stack's database is called.

`kopia_password` is the client half of the shared value the Kopia server knows as
`client_immich`; `hosts/StorageBaby/services/kopia/README.md` has the whole
mechanism. Test hosts generate both fresh per run.

## Health checks

`immich-server` answers `/api/server/ping` without authentication, which is the
one endpoint a probe can use, and its image carries `curl`.

`immich-ml`'s image carries neither `curl` nor `wget` — it is a Python service
and nothing else was installed — so the probe is the interpreter that is already
there:

```ini
HealthCmd=python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:3003/ping", timeout=5)'
```

`urlopen` raises on a non-2xx answer, which is the non-zero exit a health command
needs. A bare TCP connect would have been simpler and would have proved less: the
port is bound before the models are loaded.

Both get `HealthStartPeriod=120s` — the server's first start runs the schema
migrations against the vectorchord database, and the ML image is large enough
that loading its runtime takes a while before it binds.

## Trusted proxies

`IMMICH_TRUSTED_PROXIES` is **not set**, on purpose, and that is a measurement
rather than an omission. The old compose stack passed Traefik's container address
(`TRAEFIK_CONTAINER_IP`); the equivalent here is the address the server sees a
proxied request arrive from. Measured inside the pod while requests were in
flight through Traefik — Immich listens on IPv6, so the connections are in
`/proc/net/tcp6`, and the addresses are IPv4-mapped and word-swapped:

```
local_address                     rem_address
…FFFF0000107AA8C0:08EB            …FFFF0000107AA8C0:CE28    # ::ffff:192.168.122.16:2283
```

and the VM's own `eth0` is `192.168.122.16/24`. So the peer is the **host
itself**: Traefik runs with `Network=host` and connects to `127.0.0.1:2283`, and
pasta rewrites that loopback source to the pod's address, which under rootless
Podman is the host's. Paperless found the same thing on port 8000.

So the value would have to be a different literal IP per host, which is not
something a template can carry. And it would buy little: Immich hands the list to
Express's `trust proxy`, which only decides which entry of `X-Forwarded-For` is
reported as the client address — in the server's logs, in the admin session list
and for rate limiting. No authorization decision reads it.

The unit therefore renders the line only when a host sets
`service_config.immich.trusted_proxies`, for a host that really does sit behind a
second proxy.

## Backups

```yaml
backup:
  paths: [upload, backups]
  schedule: '03:00'
  retention: { latest: 3, daily: 7, weekly: 4, monthly: 12, annual: 3 }
  exclude:
    upload: ['/backups/']
```

Exactly the shape the other three pods have, and that is the point: `immich-dump.timer`
dumps the database into the `backups` volume at 02:30 and the sidecar snapshots it at
03:00. **Nothing about this backup depends on Immich being configured for it.** The role
generates `immich-backup.container`, mounts both volumes read-only under `/data`, connects
as `immich@<host>` and leaves a scheduler running. The server lists

```
immich@<host>:/data/upload
immich@<host>:/data/backups
```

This folder used to say Immich was the one service that backed its own database up, and
to instruct the operator to switch that on in the admin UI. That was the wrong shape:
an application setting decided whether the platform had a usable backup, and nothing
reported it when the answer was no — a version bump, a restored instance or a stray click
would have left the snapshots holding 500 GB of photos and no index to them.

### `exclude: { upload: ['/backups/'] }`

Immich's own backup job — **Administration → Settings → Backup** — writes gzipped dumps
into `upload/backups/` and keeps 14 of them by default. That is inside a snapshotted tree,
so without this rule every snapshot would carry the same database twice. The app's copy is
also the worse one to carry: gzip output differs in every byte from one night to the next,
so it dedups against nothing and each night's file is ~244 MB of new blocks in the
repository, where the uncompressed dump beside it is mostly unchanged pages.

The rule makes the job harmless rather than required. Turning it off in the UI is still
the tidier end state — it frees ~3.3 GB on the pool and stops the nightly work — but
nothing in this repository needs it off, and nothing breaks if someone turns it back on.

### Restoring

Plain SQL, so `psql` and not `pg_restore`. The `sed` is Immich's own documented
requirement and is why this database is dumped in plain format: the dump sets an empty
`search_path`, and the `vector` types the index is built on cannot be resolved under it.

The dump is `backups/postgres.sql` — named after the database, which this stack calls
`postgres` and not `immich`; `config.database_name` in `service.yml` has the why.

```sh
storagebaby-svc stop immich # the server; leave the database up
sed "s/SELECT pg_catalog.set_config('search_path', '', false);/SELECT pg_catalog.set_config('search_path', 'public, pg_catalog', true);/g" \
	/pool/apps/immich/backups/postgres.sql \
	| /usr/local/sbin/podman-as svc-immich podman exec -i immich-database \
		psql --username=immich --dbname=postgres --single-transaction --set ON_ERROR_STOP=on
```

`database` is deliberately **not** in `paths`. A database is backed up as a dump,
never as its data directory: snapshotting a running postgres data directory copies
files mid-write and restores to a cluster that may not open at all. `model-cache`
is absent too — it is downloadable weights. `upload/thumbs/` and `upload/encoded-video/`
are regenerable from the originals and are still carried: that is churn, not a second
copy of a backup, and dropping it is a decision about restore work, not about redundancy.

## Dropped from the compose stack

- **`backup/` and its `start.sh`.** The hand-written sidecar, its hostname pin and
  its `make backup_register` step are the role's `backup:` block now, and the
  client is registered by the kopia server from `client_immich` rather than by
  hand.
- **The Traefik labels.** Podman containers are invisible to Traefik's Docker
  provider; the role writes a file-provider route from `domain` and `port` in
  `service.yml` instead.
- **The Watchtower label on redis.** `AutoUpdate=registry` and the service user's
  `podman-auto-update.timer` replaced it.
- **The database and secret env on `immich-ml`.** The compose file gave the ML
  container the server's whole environment block through a YAML anchor; it
  connects to neither postgres nor redis and needs none of it.

## Migrating the data

| Old (rootful compose)                            | New                                             |
| ------------------------------------------------ | ----------------------------------------------- |
| `/pool/apps/immich/volumes/immich_upload`        | `/pool/apps/immich/upload`                      |
| `immich_database` (docker volume)                | `/var/lib/storagebaby/fast/immich/database`     |
| `immich_model-cache` (docker volume)             | `/var/lib/storagebaby/fast/immich/model-cache`  |
| `/pool/apps/immich/volumes/kopia/{config,cache}` | — (the role's `backup-config` / `backup-cache`) |

A docker named volume lives at `/var/lib/docker/volumes/immich_<name>/_data`. The
repo's root README has the recipe and the ownership rules; none of it is a chown
the operator runs any more. `database` declares `owner: 999` — this image is
`pgvector/pgvector`, which builds on `postgres:<major>-bookworm`, and Debian's
postgres is uid 999, not the 70 the Alpine variants the other pods use have — so
the role adopts that tree to the matching subuid of `svc-immich` on the converge
that finds it. `upload` and `model-cache` declare `owner: 0`: immich-server and
immich-machine-learning carry no `USER`, nor does their base image, so they write
as container root, which is `svc-immich` itself — and the rootful stack left
`upload` root-owned on the host, which the role only adopts when an owner is
declared, so 0 is spelled out rather than left as the default.

The kopia client's config is not migrated — the sidecar connects from scratch on
first start and the repository keeps the old snapshots under the same
`immich@storagebaby` identity.
