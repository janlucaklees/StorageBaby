# Paperless-ngx

The document archive, reachable at `paperless.<host domain>` —
`paperless.home.klees.io` on storagebaby. Migrated from the old
`paperless/docker-compose.yml`: five containers that used to be a compose project
are one Podman **pod** now, plus the two things the platform adds by declaration —
a nightly database dump and a Kopia backup client.

It is the platform's first pod, and the first service to use `host_secrets`,
`hooks.after_change`, a timer and a `backup` block.

## The pod

| Part                  | Image                                         | Port | Updates       |
| --------------------- | --------------------------------------------- | ---- | ------------- |
| `paperless-app`       | `ghcr.io/paperless-ngx/paperless-ngx:2.20.15` | 8000 | pinned in git |
| `paperless-database`  | `docker.io/library/postgres:17-alpine`        | 5432 | auto          |
| `paperless-broker`    | `docker.io/library/redis:alpine`              | 6379 | auto          |
| `paperless-gotenberg` | `docker.io/gotenberg/gotenberg:8`             | 3000 | auto          |
| `paperless-tika`      | `docker.io/apache/tika:latest`                | 9998 | auto          |
| `paperless-backup`    | `docker.io/kopia/kopia:0.23.1`                | —    | generated     |

`paperless.pod` owns the network namespace all six share, so every part talks to
every other over `127.0.0.1` on its own upstream port — `PAPERLESS_DBHOST` is
`127.0.0.1`, not a service name, and `PAPERLESS_REDIS` is `redis://127.0.0.1:6379`.
The pod's single `PublishPort=127.0.0.1:8000:8000` is the only host-visible port;
Traefik is the only thing that reaches it.

`paperless-backup` is **not** in this folder. The role generates it from
`backup:` in `service.yml` and joins it to this pod — which is why a service with
a `backup` block must define a `.pod`.

The app is pinned and the supporting parts are not: a paperless upgrade can carry
a database migration, so it is a line in git; the other four ride
`AutoUpdate=registry` with the service user's `podman-auto-update.timer`, which
rolls back an image that fails its health check. Two of them float inside a major
they name (`postgres:17-alpine`, `gotenberg:8`) and two do not name one at all —
`apache/tika:latest` and `redis:alpine` track whatever upstream calls current,
which is the trade the update table already makes: neither keeps state across a
restart (tika converts, redis's queue is rebuildable), so the health check and
the auto-update rollback are the whole safety net they need. Postgres is the one
that would carry a data directory across a major, which is why it names `17`.

### `AddHost=` — the pod has to be able to reach Traefik

Nothing in `quadlet/` writes these lines; the `service` role does, into a Quadlet
drop-in on the pod, for **every** route name placed on the host — `kopia.<domain>`,
which the backup sidecar connects to, along with everything else the host serves.
`ansible/roles/service/README.md`, "Reaching another service through Traefik", has the
mechanism.

## Health checks

Every container declares one — the platform's contract, and the integration suite
runs `podman healthcheck run` against each of them. Two are worth a note:

- **tika** ships neither `curl` nor `wget`, and podman runs a `HealthCmd` through
  the image's `/bin/sh`, which there is dash and has no `/dev/tcp`. So the probe
  names the shell that does: `bash -c "exec 3<>/dev/tcp/127.0.0.1/9998"` — a bare
  TCP connect, which is all a "is the server listening" check needs.
- **gotenberg** does have `curl`, so it uses `/health` like upstream.

The app gets `HealthStartPeriod=120s`: the first start runs the database
migrations, and without it `HealthOnFailure=kill` would kill a container that is
merely still migrating.

## Secrets

| Secret              | Where from                                                      |
| ------------------- | --------------------------------------------------------------- |
| `database_password` | `secrets.sops.yaml` in this folder                              |
| `secret_key`        | `secrets.sops.yaml` in this folder                              |
| `kopia_password`    | `hosts/<host>/secrets/kopia-clients.sops.yaml`, key `paperless` |

> **Both of this folder's secrets are `REPLACE_ME` and must be filled before the
> first deploy on storagebaby**, with
>
> ```sh
> make sops FILE=hosts/storagebaby/services/paperless/secrets.sops.yaml
> ```
>
> - `database_password` has to be **the password of the database that is migrated
>   in**. The postgres image only applies `POSTGRES_PASSWORD` when it initialises
>   an empty data directory; moved-in data keeps the old stack's password, and a
>   different value here means the app cannot log in to its own database.
> - `secret_key` has to be **the live one** from the old stack's
>   `paperless_secret_key.secret`. Django derives session and token signatures
>   from it, so a new value logs every user out and invalidates every API token.

`kopia_password` is the client half of the shared value the Kopia server knows as
`client_paperless`; `hosts/storagebaby/services/kopia/README.md` has the whole
mechanism. Test hosts generate all three fresh per run.

## Trusted proxies

`PAPERLESS_TRUSTED_PROXIES` is **not set**, on purpose, and that is a measurement
rather than an omission. The old compose stack passed Traefik's container address
(`TRAEFIK_CONTAINER_IP`); the equivalent here would be the address the app sees a
proxied request come from, which on the test VM is:

```
tcp 0 0 ::ffff:192.168.122.25:8000  ::ffff:192.168.122.25:60108  ESTABLISHED
```

— the **host's own address**. Traefik runs with `Network=host` and connects to
`127.0.0.1:8000`, and pasta rewrites that loopback source to the pod's address,
which under rootless Podman is the host's. So the value would have to be a
different literal IP per host.

It would also be the wrong kind of value. Paperless 2.20 uses the setting in one
place only — `signals.py`, `IpWare(proxy_list=settings.TRUSTED_PROXIES)`, to log
the client address of a **failed login**. python-ipware matches `proxy_list`
against the tail of the `X-Forwarded-For` chain, not against the peer address, and
requires the chain to be longer than the list (`ip_count - 1 < proxy_list_count`
→ rejected). Traefik sets `X-Forwarded-For` to the client and adds **no entry of
its own**, so with any one-element list the chain is always too short. Measured,
with `PAPERLESS_TRUSTED_PROXIES=127.0.0.1`:

```
[paperless.auth] Login failed for user `nobody`. Unable to determine IP address.
```

With the variable absent, `TRUSTED_PROXIES` is `[]`, ipware takes the addresses it
can see and names one. Same request, same failed login, variable gone:

```
[paperless.auth] Login failed for user `nobody` from private IP `192.168.122.25`.
```

So the unit renders the line only when a host sets
`service_config.paperless.trusted_proxies` — for a host that really does sit
behind a second proxy that adds itself to the chain.

Two caveats, both of which stop at the log line — no authorization decision in
paperless reads this. ipware prefers a private address over a loopback one, so a
request from the host itself is logged as the proxy's address rather than as
`127.0.0.1`. And a client that sends its own `X-Forwarded-For` can put whatever it
likes at the front of the chain, because Traefik appends to an existing header
rather than replacing it.

## The database dump

```
paperless-dump.timer    daily at 02:30
paperless-dump.service  podman exec paperless-database pg_dump -Fc … > /backups/paperless.dump
```

Plain systemd user units in `~svc-paperless/.config/systemd/user/`, not Quadlet
ones. 02:30 is half an hour before the snapshot at 03:00, so every snapshot
carries a dump from the same night.

The dump is written to `<name>.dump.tmp` and renamed, so `/backups` never holds a
half-written file for the sidecar to pick up — `mv` within one volume is atomic.

`Persistent=true` does **not** make the timer fire on the converge that enables it,
which is the one thing it could have got wrong here: the pod has just started and the
database is seconds old. Measured on the test VM right after a first converge, on all
three dump timers (paperless, openarchiver, nextcloud):
`ExecMainStartTimestampMonotonic=0` and an empty journal for every `*-dump.service` —
systemd has no missed elapse to catch up on until the timer has a stamp, so the first
run is the first real 02:30. (`nextcloud-cron.timer` is deliberately non-persistent
for a different reason: a missed cron run is not worth catching up.)

**A database is backed up as a dump, never as its data directory.** `database` is
therefore not in `backup.paths`; `backups` is. Snapshotting a running postgres
data directory copies files mid-write and restores to a database that may not
open at all.

### Restoring one

The old `paperless/Makefile` had `database_snapshot` and `database_restore` in
`--format=tar`; the timer replaced the first and this replaces the second, in the
custom format `pg_dump -Fc` writes. It runs as the service user, because the
container belongs to that user's podman:

```sh
/usr/local/sbin/podman-as svc-paperless podman exec -i paperless-database \
	pg_restore -U paperless -d paperless --no-owner < /path/to/paperless.dump
```

`-U` and `-d` are `config.database_user` and `config.database_name` in
`service.yml`. `--no-owner` because the roles in the dump are the old stack's, not
the ones this cluster initialised with. `pg_restore` does not empty what is already
there, so restore into a freshly initialised cluster, or drop and recreate the
database first with paperless stopped (`make stop SERVICE=paperless`). The file is
either `/var/lib/storagebaby/fast/paperless/backups/paperless.dump` on the host or
one restored out of a Kopia snapshot of the `backups` volume.

## Backups

```yaml
backup:
  paths: [data, media, backups]
  schedule: '03:00'
  retention: { latest: 3, daily: 7, weekly: 4, monthly: 12, annual: 3 }
```

The role generates `paperless-backup.container` from this, mounts the three
volumes read-only at `/data/<volume>`, connects to the Kopia server as
`paperless@<host>` and leaves a scheduler running, so the snapshots happen at
03:00 without a timer of their own. `ansible/roles/service/README.md` has the
sidecar's mechanics.

`data` and `media` are the archive itself; `backups` carries the nightly dump.
`database` and `broker` are deliberately absent — the first is dumped, the second
is a queue.

Being the first client, this service is what established that a Kopia repository
client speaks **gRPC** and that Traefik therefore has to reach the server over
TLS — `hosts/storagebaby/services/kopia/README.md` has the whole finding. Nothing
about it is visible here: the sidecar connects to `https://kopia.<domain>` like
any other client and the server lists

```
paperless@test-a:/data/data
paperless@test-a:/data/media
paperless@test-a:/data/backups
```

## The after-change hook

```yaml
hooks:
  after_change:
    - { container: paperless-app, command: 'touch /tmp/.hook-ran' }
```

A marker, not a maintenance command: paperless needs no post-upgrade step of its
own (the image runs its migrations from the entrypoint). It is here because the
hook mechanism needs something observable to be testable at all, and `touch` is
the one hook shape the integration suite can check — the harness removes
`/tmp/.hook-ran` with `podman exec`, pushes a change to the app unit, deploys,
and asserts the marker came back. Nextcloud's hooks, which do real work, ride the
same machinery.

`/tmp` **inside the container**, not a path under a volume, and both halves of
that matter. A marker under `data/` would be a stray file in one of the three
trees the Kopia sidecar snapshots — backed up nightly, restored with the data,
forever. And container-local is the stronger claim for the harness: `/tmp` is
gone when the container is recreated, so a marker found after a deploy that
restarted the app can only have been written after that restart.

What it costs on storagebaby is the health wait: **any** hook attaches one to its
service's converge. Before running a hook the role waits for
`podman healthcheck run paperless-app` to succeed, up to ten minutes, so every
converge that restarts this pod also waits for paperless to report healthy before
it moves on. A pod that never gets there skips its hooks and is named in the
failure the playbook raises at the end — the other services still converge.

`when` is left at its default `unit_changed`, so the hook runs on a converge that
actually restarted something and not on the nightly no-op.

## Dropped from the compose stack

- **`./consume` and `./export`.** The scanner path is `paperless-upload`, which
  posts documents through the API; nothing drops files into a consume directory
  any more. An export is `document_exporter` run by hand when it is wanted.
- **`USERMAP_GID=998`.** It existed so the host's scanner group could write into
  `./consume`. With no consume mount there is nothing to line up, and the image's
  default user is fine under the rootless mapping.
- **Traefik labels.** Podman containers are invisible to Traefik's Docker
  provider; the role writes a file-provider route from `domain` and `port` in
  `service.yml` instead.
- **Watchtower labels.** `AutoUpdate=registry` and the service user's
  `podman-auto-update.timer` replaced it.

## Migrating the data

`paperless/docker-compose.yml`'s four named volumes map onto this folder's five:

| Old (rootful compose)        | New                                                               |
| ---------------------------- | ----------------------------------------------------------------- |
| `paperless_data` (named)     | `/pool/apps/paperless/data`                                       |
| `paperless_media` (named)    | `/pool/apps/paperless/media`                                      |
| `paperless_database` (named) | `/var/lib/storagebaby/fast/paperless/database`                    |
| `paperless_broker` (named)   | — (a queue; start empty)                                          |
| —                            | `/var/lib/storagebaby/fast/paperless/backups` (new, for the dump) |

All four were ordinary Docker named volumes, not binds: rootful Docker keeps them
under `/var/lib/docker/volumes/<name>/_data`, and `<name>` is the compose project
(the directory the `docker-compose.yml` sat in) plus the volume's own name.

The repo's root README has the recipe and the ownership rules. The paperless
image runs the app as its own in-container user, so the moved trees have to be
chowned under `podman unshare` as `svc-paperless`, not on the host directly — and
the database directory has to end up owned by postgres's in-container uid (70 in
`postgres:17-alpine`), which is a subuid of `svc-paperless` on the host.
