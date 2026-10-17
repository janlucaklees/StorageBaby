# OpenProject

Project management. <https://openproject.klees.io>

Moved off the VPS (`CloudBaby/openproject`). It comes up **empty** — this folder is the
service, not the data.

## Stack

One pod, four containers and a timer:

- `openproject-web` — Rails on 8080, the only part the route reaches, and the one that
  migrates the database on start.
- `openproject-worker` — GoodJob background jobs. No HTTP at all.
- `openproject-database` — Postgres 17.
- `openproject-cache` — redis, the Rails cache store.
- `openproject-dump.timer` — `pg_dump` into `backups` at 02:30.

## The migration is the image's own, and the seeding is a hook

Two halves, in two places, and neither needs anything the platform does not already have.

**`MIGRATE=true` on the web container.** `docker/prod/web` reads it and runs
`rake db:migrate` before starting the server. Idempotent: a no-op on a current schema, and
on a version bump it runs the new image's migrations before the new code serves anything.

**`rake db:seed` as an `after_change` hook.** The flag covers migrations only, and a
migrated-but-unseeded OpenProject has no administrator, no work-package types and no
statuses. The hook is the right home for it because the role waits for the container's own
health check before running it — which is precisely "the migration has finished" — and
`db:seed` is idempotent, so a converge that changed a unit may run it again harmlessly.

Upstream's compose does it differently, and the difference is worth recording rather than
rediscovering. It ships a separate `seeder` service that runs `migrate` then `db:seed` and
exits, with web, worker and cron depending on it. **That shape cannot be expressed here:**
Cradle's `tests/static/test_quadlet_conventions.py` requires `Restart=always` and `HealthCmd=` of
every container unit, so a process that runs once and exits cannot be one — and a plain
`*.service.j2` beside it would be rendered but never started, because
Cradle's `ansible/roles/service/tasks/timers.yml` builds its enable list from `*.timer.j2` alone.
It could be pulled in by `Requires=` from web, which works; the flag is simply less
machinery for the same result, and it is the vendor's own code path rather than this
repository's invention.

The worker is ordered behind web for the same reason — web is what migrates — but with
`After=` alone and no `Requires=`, so a stopped web does not take the job queue with it.

## The cron container is deliberately absent

Upstream's compose runs a fifth container, `./docker/prod/cron`. Read it: the whole script is
a `while true` loop that calls `rake redmine:email:receive_imap` and sleeps
`IMAP_CHECK_INTERVAL`. With IMAP disabled it logs "IMAP email checking is disabled" and
sleeps 600 seconds, forever. It does nothing else — no other scheduled work.

So it is not here. OpenProject's own scheduled jobs run inside GoodJob, in the worker. If
inbound email by IMAP is ever wanted, it comes back as one container with the same image and
`Exec=./docker/prod/cron`, plus `IMAP_HOST` / `IMAP_USERNAME` in `config/app.env` and an
`imap_password` secret.

## The database URL is composed, not stored

OpenProject reads neither a password file nor a split set of `DB_*` variables — it takes one
`DATABASE_URL`. Storing that whole URL as a secret would put the password in the repository
twice, so instead the password is the only secret and web and worker compose the URL from it
at start:

```
Entrypoint=/bin/sh
Exec=-c '. /config/db-url.sh; exec ./docker/prod/entrypoint-slim.sh ./docker/prod/web'
```

openarchiver does the same thing for the same reason, and its unit is the model. Two details
make it work: the entrypoint the image would have run is passed through rather than replaced,
because it ends in `exec "$@"` and does the jemalloc and `PGBIN` setup; and `exec` keeps the
server as pid 1, so systemd's signals reach it instead of a shell.

**The composition lives in a sourced script, not in the unit**, and that is the whole reason
`config/db-url.sh` exists. `podman exec` starts a process with the container's _configured_
environment — `Environment=` and `Secret=…,type=env` — so a variable exported by the
entrypoint belongs to pid 1 alone. The `db:seed` hook ran without it and Rails fell back to a
local unix socket:

```
ActiveRecord::ConnectionNotEstablished: connection to server on socket
"/var/run/postgresql/.s.PGSQL.5432" failed: No such file or directory
```

One definition, three callers: web, the worker, and the hook. The config directory is mounted
read-only at `/config` to make it reachable from inside.

The password must be URL-safe, which is why every generated password here is hex. A value
with `@`, `:` or `/` in it would need escaping inside the URL field and would silently
produce a different connection.

## Shared configuration

`config/app.env` holds the environment all four processes share, read by the containers as
`EnvironmentFile=` and by the seeder as `podman run --env-file`. It carries no secret by
construction — it is world-readable under `/etc/cradle/openproject/`.

`config/` is copied verbatim and never templated, so `OPENPROJECT_HOST__NAME` is written out.
That is correct rather than a compromise: this service declares a full `klees.io` name, so the
value is identical on every host the folder is placed on.

## Volumes

| volume     | class | owner | what it holds                                   |
| ---------- | ----- | ----- | ----------------------------------------------- |
| `data`     | pool  | 1000  | attachments, avatars, exports (`APP_DATA_PATH`) |
| `database` | fast  | 70    | the Postgres data directory                     |
| `backups`  | pool  | —     | the nightly dump                                |

`1000` is the image's own `useradd -d /home/app -m app`, the first non-system user in a ruby
slim base, and the uid its `USER` line drops to. Redis gets no volume: a cache store is
regenerable by definition.

## Health probes use ruby

The image has no curl, no wget and no procps — upstream's own compose healthcheck calls curl
and cannot be working. Ruby is guaranteed, so web asks `/health_checks/default` through
`Net::HTTP`, which also checks the database. The worker serves no HTTP, so its probe is
whether GoodJob is still in a process cmdline, grepped out of `/proc` because there is no
`pgrep`.

## The legacy redirect is not here

`openproject.janlucaklees.de` → `https://openproject.klees.io` is Porkbun URL forwarding:
a 301 with "include the requested URI path" enabled. It needs no router, no middleware and no
certificate for that zone, so the platform gained nothing for it.

## Backup and restore

`data` and `backups` are snapshotted at 03:00; `database` never is.

```sh
doas /usr/local/sbin/podman-as svc-openproject podman exec -i openproject-database \
	psql --single-transaction --set ON_ERROR_STOP=on -U openproject -d openproject < openproject.sql
```

After a restore, start the service: web migrates whatever the dump's schema is behind on
before it serves anything.
