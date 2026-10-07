# Paperless-ngx

The document archive, reachable at `paperless.<host domain>` —
`paperless.home.klees.io` on storagebaby. Migrated from the old
`paperless/docker-compose.yml`: five containers that used to be a compose project
are one Podman **pod** now, plus the three things the platform adds by declaration —
a nightly database dump, a Kopia backup client, and since Phase 4 an FTP part the
scanner delivers into.

It is the platform's first pod, the first service to use `host_secrets`,
`hooks.after_change`, a timer and a `backup` block, and the first to claim
`tcp_ports`.

## The pod

| Part                  | Image                                         | Port | Updates       |
| --------------------- | --------------------------------------------- | ---- | ------------- |
| `paperless-app`       | `ghcr.io/paperless-ngx/paperless-ngx:2.20.15` | 8000 | pinned in git |
| `paperless-database`  | `docker.io/library/postgres:17-alpine`        | 5432 | auto          |
| `paperless-broker`    | `docker.io/library/redis:alpine`              | 6379 | auto          |
| `paperless-gotenberg` | `docker.io/gotenberg/gotenberg:8`             | 3000 | auto          |
| `paperless-tika`      | `docker.io/apache/tika:latest`                | 9998 | auto          |
| `paperless-ftp`       | `docker.io/stilliard/pure-ftpd:trixie-1.0.50` | 2121 | pinned in git |
| `paperless-backup`    | `docker.io/kopia/kopia:0.23.1`                | —    | generated     |

`paperless.pod` owns the network namespace all seven share, so every part talks to
every other over `127.0.0.1` on its own upstream port — `PAPERLESS_DBHOST` is
`127.0.0.1`, not a service name, and `PAPERLESS_REDIS` is `redis://127.0.0.1:6379`.
The pod publishes three things on loopback and nothing else: `8000` for the web
route, `2121` for the FTP control connection and `21100-21109` for its passive
data connections. Traefik is the only thing that reaches any of them.

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
- **the FTP part** has the same problem as tika and the same answer: `bash` is in
  the image, `/bin/sh` is dash, and the probe is a bare connect to `127.0.0.1:2121`.
  It counts against pure-ftpd's per-IP connection limit — see "One consequence of
  proxying FTP" below.

The app gets `HealthStartPeriod=120s`: the first start runs the database
migrations, and without it `HealthOnFailure=kill` would kill a container that is
merely still migrating.

## Secrets

| Secret              | Where from                                                      |
| ------------------- | --------------------------------------------------------------- |
| `database_password` | `secrets.sops.yaml` in this folder                              |
| `secret_key`        | `secrets.sops.yaml` in this folder                              |
| `ftp_password`      | `secrets.sops.yaml` in this folder                              |
| `kopia_password`    | `hosts/<host>/secrets/kopia-clients.sops.yaml`, key `paperless` |

> **All three of this folder's secrets are `REPLACE_ME` and must be filled before
> the first deploy on storagebaby**, with
>
> ```sh
> mise run sops hosts/StorageBaby/services/paperless/secrets.sops.yaml
> ```
>
> - `database_password` has to be **the password of the database that is migrated
>   in**. The postgres image only applies `POSTGRES_PASSWORD` when it initialises
>   an empty data directory; moved-in data keeps the old stack's password, and a
>   different value here means the app cannot log in to its own database.
> - `secret_key` has to be **the live one** from the old stack's
>   `paperless_secret_key.secret`. Django derives session and token signatures
>   from it, so a new value logs every user out and invalidates every API token.
> - `ftp_password` is a **free choice**, unlike the two above: nothing existing knows
>   it. Whatever is put here is what the scanner's FTP profile is configured with,
>   and those are the only two places it exists.

`kopia_password` is the client half of the shared value the Kopia server knows as
`client_paperless`; `hosts/StorageBaby/services/kopia/README.md` has the whole
mechanism. Test hosts generate all four fresh per run.

## The FTP drop

The scanner's path into the archive. It replaces `paperless-upload`, which watched
the `/pool/shared/scans` Samba share and posted what appeared there through the
API: the printer now logs in by FTP and writes straight into the directory
paperless's own consumer polls, so the hop, the API token and the share are all
gone. `/pool/shared/scans` stays on disk as data nothing serves.

```yaml
tcp_ports:
  - { port: 21, target: 2121 }
  - { range: [21100, 21109] }
config:
  ftp_user: scanner
secrets: [..., ftp_password]
volumes:
  consume: { class: fast }
```

Traefik listens on `<tcp_bind_address>:21` and forwards to the pod's `2121`; the ten
passive ports are forwarded port-to-port. `ansible/roles/service/README.md`, "TCP
ports", has the mechanism and the reason the entrypoints bind one concrete address
rather than the wildcard.

**`docker.io/stilliard/pure-ftpd:trixie-1.0.50`, and one added capability.** The
image takes exactly what this needs out of the environment — one virtual user
(`FTP_USER_NAME`, `FTP_USER_PASS`, `FTP_USER_HOME`, `FTP_USER_UID`/`GID`), a listen
port (`-S`), a passive range (`-p lo:hi`) and an advertised address (`-P`) — chroots
the user to its home by construction, and ships `bash` for the health probe. What it
does **not** do is start under podman's default capability set: every invocation, the
image's own documented command included, exits **252 with no diagnostic at all**
(pure-ftpd logs to syslog, and there is none in a container). Bisected on the test
VM, one capability at a time:

| run                                      | result       |
| ---------------------------------------- | ------------ |
| podman's default set                     | Exited (252) |
| `--security-opt seccomp=unconfined` only | Exited (252) |
| `--cap-add=sys_nice`                     | Exited (252) |
| `--cap-add=sys_resource`                 | Exited (252) |
| `--cap-add=dac_read_search`              | Exited (252) |
| `--cap-add=audit_write`                  | **Up**       |

So the unit carries `AddCapability=AUDIT_WRITE`. `CAP_AUDIT_WRITE` is in **Docker's**
default capability set and **not** in podman's, which is the whole of why the image's
own `docker run` line works and the same run under podman does not. Rootless it is a
capability inside `svc-paperless`'s own user namespace and nothing on the host — the
kernel's audit log answers to the initial namespace, not to this one — which is the
same argument `AddCapability=MKNOD` on nextcloud's Collabora already rests on.

Two other things the probe settled, both of which read fine and are wrong:
`-S ,2121` (the `address,port` form) makes pure-ftpd exit; the bare `-S 2121` is
what works. And the image has no `ENTRYPOINT` — its whole command is
`/bin/sh -c "/run.sh …"` — so `Exec=` in the unit replaces it outright.

### The advertised address

`-P {{ tcp_bind_address }}`. An FTP server puts an address into its `227` reply to
`PASV` and the client opens the data connection **to that address**; behind a TCP
proxy the address the server can see is never the one the client used. Unset, it
advertises the pod's, and every transfer hangs until it times out — a failure with
no error on either side. So it is the address Traefik's entrypoints bind, which is
the address the client reached: `tcp_bind_address`, defaulting to the host's
default-route address and overridable as a top-level key in `host.yml`. **If the
scanner connects, logs in, and then hangs, that key is the knob.**

The passive range is read out of `tcp_ports` in the template rather than written
twice, so the ports pure-ftpd advertises and the ports Traefik forwards cannot
drift. Each is forwarded to itself, which a passive range has no choice about.

`tests/integration/molecule/test-ci/tests/test_ftp.py` measures all of this from the
VM with `curl --disable-epsv --no-ftp-skip-pasv-ip`: both flags are load-bearing,
because curl prefers EPSV (whose `229` reply carries no address) and otherwise
ignores the address in a `227` anyway — so without them the one thing that can be
misconfigured here would go unmeasured.

### No TLS

Printers speak plain FTP on the LAN, and explicit FTPS cannot be terminated by a
Traefik TCP router — there is no TLS handshake to inspect at connection time. So the
credentials and the documents cross the LAN in clear. The exposure is bounded and
worth naming: one account, whose only right is to write into a spool directory that
a consumer empties, and whose password exists in exactly two places (this folder's
sops file and the printer's configuration). It is not an account that can read the
archive.

### One consequence of proxying FTP

`/run.sh` appends pure-ftpd's defaults `-c 5 -C 5` — five clients, and five
connections **per source address**. Behind Traefik every connection arrives from
`127.0.0.1`, so the per-IP limit is effectively the global one, and the health probe
is one of the five. Ample for one scanner; something to remember before pointing a
second device at it.

### The `consume` volume

`fast` class, and the only volume of this service meant to be empty: the consumer
deletes each file once the document is filed. It is deliberately **absent from
`backup.paths`** — a spool is not an archive, and a file that is still in it has not
been ingested yet.

`PAPERLESS_CONSUMER_POLLING=10` rather than inotify, because the file is written by
another container of the pod through the same bind mount and an inotify watch across
that boundary is not a thing to bet a scan on. `PAPERLESS_CONSUMER_RECURSIVE=false`,
which is also the default, spelled out because the FTP user can create directories
there and a recursive watch would follow them.

Both containers mount the directory read-write and both agree on the uid: the FTP
part creates its virtual user as uid/gid 1000, which is what paperless-ngx runs as,
and both resolve to the same host subuid of `svc-paperless`. Measured on the VM — an
uploaded file lands as `297610:297610`, exactly what `…/paperless/data` already
carries.

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
paperless-dump.service  podman exec paperless-database pg_dump … > /backups/paperless.sql
```

Plain systemd user units in `~svc-paperless/.config/systemd/user/`, not Quadlet
ones. 02:30 is half an hour before the snapshot at 03:00, so every snapshot
carries a dump from the same night.

The dump is written to `<name>.sql.tmp` and renamed, so `/backups` never holds a
half-written file for the sidecar to pick up — `mv` within one volume is atomic.

It is **plain SQL and uncompressed**, the same form all four of the platform's dump
timers use: the sidecar snapshots it into a deduplicating repository, and a compressed
dump differs in every byte from one night to the next so none of it dedups against
yesterday's. `ansible/roles/service/README.md`, "The dump form", has the reasoning.

`Persistent=true` does **not** make the timer fire on the converge that enables it,
which is the one thing it could have got wrong here: the pod has just started and the
database is seconds old. Measured on the test VM right after a first converge, on all
three dump timers (paperless, openarchiver, nextcloud):
`ExecMainStartTimestampMonotonic=0` and an empty journal for every `*-dump.service` —
systemd has no missed elapse to catch up on until the timer has a stamp, so the first
run is the first real 02:30. (`nextcloud-cron.timer` is deliberately non-persistent
for a different reason: a missed cron run is not worth catching up.)

**A database is backed up as a dump, never as its data directory.** `database` is
therefore not in `backup.paths`; `backups` is — and `backups` is class `pool`, because a
write-once nightly file has no business on a 120G SSD. Snapshotting a running postgres
data directory copies files mid-write and restores to a database that may not
open at all.

### Restoring one

The old `paperless/Makefile` had `database_snapshot` and `database_restore` in
`--format=tar`; the timer replaced the first and this replaces the second. The dump is
plain SQL, so it is `psql` and not `pg_restore`. It runs as the service user, because the
container belongs to that user's podman:

```sh
/usr/local/sbin/podman-as svc-paperless podman exec -i paperless-database \
	psql -U paperless -d paperless --single-transaction --set ON_ERROR_STOP=on \
	< /path/to/paperless.sql
```

`-U` and `-d` are `config.database_user` and `config.database_name` in
`service.yml`. `--single-transaction` with `ON_ERROR_STOP=on` is what makes the restore
all-or-nothing — without the pair `psql` reports each failed statement and carries on,
leaving a half-restored database. The dump itself carries `--clean --if-exists`, so it
drops what is there before recreating it, and `--no-owner`, so it does not insist on
roles this cluster may not have. Restore with paperless stopped
(`storagebaby-svc stop paperless`). The file is
either `/pool/apps/paperless/backups/paperless.sql` on the host or
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
TLS — `hosts/StorageBaby/services/kopia/README.md` has the whole finding. Nothing
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

- **`./export`.** An export is `document_exporter` run by hand when it is wanted.
  (`./consume` came back in Phase 4 as the `consume` volume — see "The FTP drop".)
- **`USERMAP_GID=998`.** It existed so the host's scanner group could write into
  `./consume` from outside the container. Nothing on the host writes there now:
  the only writer is `paperless-ftp`, inside the pod, as the same in-container uid
  the app runs as.
- **Traefik labels.** Podman containers are invisible to Traefik's Docker
  provider; the role writes a file-provider route from `domain` and `port` in
  `service.yml` instead.
- **Watchtower labels.** `AutoUpdate=registry` and the service user's
  `podman-auto-update.timer` replaced it.

## Migrating the data

`paperless/docker-compose.yml`'s four named volumes map onto this folder's six:

| Old (rootful compose)        | New                                                                |
| ---------------------------- | ------------------------------------------------------------------ |
| `paperless_data` (named)     | `/pool/apps/paperless/data`                                        |
| `paperless_media` (named)    | `/pool/apps/paperless/media`                                       |
| `paperless_database` (named) | `/var/lib/storagebaby/fast/paperless/database`                     |
| `paperless_broker` (named)   | — (a queue; start empty)                                           |
| —                            | `/pool/apps/paperless/backups` (new, for the dump)                 |
| —                            | `/var/lib/storagebaby/fast/paperless/consume` (new, the FTP spool) |

All four were ordinary Docker named volumes, not binds: rootful Docker keeps them
under `/var/lib/docker/volumes/<name>/_data`, and `<name>` is the compose project
(the directory the `docker-compose.yml` sat in) plus the volume's own name.

The repo's root README has the recipe and the ownership rules, and the chowns are
the role's: `data`, `media` and `consume` declare `owner: 1000`, the uid
paperless-ngx runs as, and `database` declares `owner: 70`, postgres's uid in
`postgres:17-alpine`. Each is mapped onto the matching subuid of `svc-paperless`
and adopted once, on the converge that finds a copied-in tree still owned by
whatever Docker left it as. `backups` declares none — the dump timer's
`podman exec` has no `-u`, so it writes as container root, which is the service
user.
