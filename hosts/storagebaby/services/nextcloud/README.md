# Nextcloud

The file cloud, reachable at `nextcloud.<host domain>` —
`nextcloud.home.klees.io` on storagebaby — with its office backend at
`collabora.<host domain>`. Migrated from the old `nextcloud/docker-compose.yml`:
five containers that used to be a compose project are one Podman **pod** now,
plus the four things the platform adds by declaration — a second route, a nightly
database dump, the five-minute cron job that ofelia used to run, and a Kopia
backup client.

It is the largest service on the platform and the only one with two routes, two
timers and a real `hooks.after_change`.

## The pod

| Part                  | Image                                       | Port | Updates       |
| --------------------- | ------------------------------------------- | ---- | ------------- |
| `nextcloud-app`       | `docker.io/library/nextcloud:33-fpm-alpine` | 9000 | pinned in git |
| `nextcloud-nginx`     | `docker.io/library/nginx:alpine`            | 80   | auto          |
| `nextcloud-database`  | `docker.io/library/postgres:17-alpine`      | 5432 | auto          |
| `nextcloud-cache`     | `docker.io/library/redis:alpine`            | 6379 | auto          |
| `nextcloud-collabora` | `docker.io/collabora/code:latest`           | 9980 | auto          |
| `nextcloud-backup`    | `docker.io/kopia/kopia:0.23.1`              | —    | generated     |

`nextcloud.pod` owns the network namespace all six share, so `POSTGRES_HOST` and
`REDIS_HOST` are `127.0.0.1` rather than compose service names, and nginx's
`upstream php-handler` is `127.0.0.1:9000` — the one line changed in
`config/nginx.conf`.

The app is pinned and everything around it is not: a Nextcloud major carries a
database migration and the `occ` steps below, so it is a line in git;
postgres, redis, nginx and Collabora ride `AutoUpdate=registry` with the service
user's `podman-auto-update.timer`, which rolls back an image that fails its
health check.

`nextcloud-backup` is **not** in this folder. The role generates it from
`backup:` in `service.yml` and joins it to this pod.

## Two routes, one pod

```yaml
routes:
  - { domain: nextcloud, port: 8280 }
  - { domain: collabora, port: 9980 }
```

This is the service the `routes` list exists for. The browser loads Nextcloud
from one hostname and, when it opens a document, loads the editor from the other
— they are two origins to it, not one service with a path prefix. So the pod
publishes two loopback ports and the role writes two Traefik files,
`nextcloud-nextcloud.yml` and `nextcloud-collabora.yml`, each with its own router
and service.

`8280` rather than `80`: `PublishPort=127.0.0.1:8280:80` maps the route's
loopback port onto the port nginx really listens on. Only the pod's publish line
bridges the two; nothing inside the pod knows about 8280. Collabora's two numbers
are the same, so its line is `9980:9980`.

The old compose file put `traefik.http.routers.collabora.tls.certresolver` on the
Collabora container. There is no per-router resolver any more: Traefik issues one
wildcard for the host's domain and both routes are served from it, so neither
route says anything about the certificate the **client** sees. The two do differ
in how Traefik reaches the **backend** — nginx over plain HTTP, Collabora over
the TLS it serves itself — and that is what the per-entry `scheme` and
`insecure_skip_verify` are for; see "Collabora keeps its own TLS".

### `AddHost=kopia.<domain>:host-gateway`

On the pod, for the backup sidecar: it connects to the Kopia server the way every
other client does, through Traefik on the host (`https://kopia.<domain>`), and
inside the pod there is no DNS that answers for that name. Harmless on a host
where DNS does answer.

## `config/`

Three files, copied to `/etc/storagebaby/nextcloud/` by the role and mounted
read-only where the compose stack mounted them:

| File          | Mounted at                              | In    |
| ------------- | --------------------------------------- | ----- |
| `nginx.conf`  | `/etc/nginx/nginx.conf`                 | nginx |
| `www.conf`    | `/usr/local/etc/php-fpm.d/www.conf`     | app   |
| `opcache.ini` | `/usr/local/etc/php/conf.d/opcache.ini` | app   |

They are the old stack's files, flattened out of
`config/<service>/<absolute path>/` into one directory, because the role copies
`config/` as it is and the unit says where each file goes. `nginx.conf` differs
from the compose version in exactly one line, the upstream; `www.conf` and
`opcache.ini` are unchanged.

Only `nginx.conf` is replaced in the nginx image — `mime.types` and
`fastcgi_params`, which it includes, stay the image's own.

`nextcloud-nginx` mounts the `html` volume **read-only**: it serves the static
half of the installation straight off the tree php-fpm owns, and has no business
writing into it.

## Health checks

Every container declares one — the platform's contract, and the integration suite
runs `podman healthcheck run` against each of them. Three are worth a note:

- **`nextcloud-app` speaks FastCGI, not HTTP**, so there is no URL to ask. The
  most a probe can establish is that php-fpm is listening on 9000, and on this
  image that is harder than it sounds: `nextcloud:33-fpm-alpine` is Alpine, so
  `/bin/sh` is busybox ash, which has no `/dev/tcp`; there is no `bash`, no
  `curl` and no `cgi-fcgi`. What is always there is the interpreter the container
  exists for, so it does the connect:

  ```
  HealthCmd=php -r 'exit(@fsockopen("127.0.0.1", 9000) ? 0 : 1);'
  ```

  Podman runs a `HealthCmd` that is not a JSON array through the image's
  `/bin/sh -c`, which is what strips the single quotes — the php code reaches
  `php -r` intact.

  That the check is weak is the point of the next one: php-fpm listening is not
  Nextcloud answering, and the entrypoint only `exec`s php-fpm once it has
  finished copying the installation into the volume and running the installer.
  So this probe going green is also what says the first start is over, which is
  what the `after_change` hooks wait for.

- **`nextcloud-nginx` asks `/status.php`**, which is a php file: a 200 there
  means nginx reached php-fpm over the pod's loopback, php ran and Nextcloud
  answered with its status JSON. That is the probe that proves the pair. busybox
  `wget` rather than `curl` — this is Alpine, and `wget` is the one that is
  always there; it exits non-zero on any status but 2xx.

- **`nextcloud-collabora` has nothing to ask with at all.** Since 26.04 the CODE
  image is a Nix-built **distroless** rootfs: `/bin` and `/sbin` are empty, its
  entrypoint is the `coolwsd` binary rather than the shell script it used to be,
  and the whole of `/usr/bin` is

  ```
  c_rehash  coolforkit-caps  coolforkit-ns  coolmount  coolwsd
  debconf*  openssl
  ```

  There is no `/bin/sh`, so podman's default `CMD-SHELL` wrapping cannot run —
  the plan's `curl -sf http://127.0.0.1:9980/hosting/discovery` fails with an
  empty message and exit 1, which is how this was found — and there is no HTTP
  client of any kind. The one usable probe in the image is `openssl`, so the
  check is a JSON array and the question it asks is the TLS handshake:

  ```
  HealthCmd=["CMD", "/usr/bin/openssl", "s_client", "-connect", "127.0.0.1:9980", "-quiet", "-no_ign_eof"]
  ```

  A completed handshake against coolwsd's own certificate proves it is accepting
  and serving. Measured: exit 0 against the live port, exit 1
  (`connect:errno=111`) against a dead one. Paperless's tika probe is the same
  shape for the same reason — an image with nothing in it to ask with.

  `-no_ign_eof` is not optional, and it has to come **after** `-quiet`: `-quiet`
  implies `-ign_eof`, and s_client then sits on its open stdin until podman kills
  it at the 30 s health timeout — a failing check, not a passing one. Spelled
  out, the empty stdin of a `podman exec` ends the connection and openssl exits.

  **This is why Collabora keeps its own TLS** (see below). It is not only that
  `openssl` cannot speak plain HTTP; with TLS off there is no probe in this image
  at all.

`HealthStartPeriod` is long on the app and on nginx (600 s). The first start is
not a start: the entrypoint copies the whole installation into the empty `html`
volume and then installs against an empty database, and `HealthOnFailure=kill`
would otherwise kill it halfway through and make it begin again. Collabora gets
240 s — its image is around 2 GB and it unpacks a template jail before it binds.

## Collabora keeps its own TLS

```yaml
- { domain: collabora, port: 9980, scheme: https, insecure_skip_verify: true }
```

```
Environment="extra_params=--o:ssl.enable=true --o:ssl.termination=false"
```

The old compose stack turned Collabora's TLS **off**
(`--o:ssl.enable=false --o:ssl.termination=true`) because Traefik spoke plain
HTTP to every backend it had. It can re-encrypt now — the kopia server
established the mechanism and the route keys that express it — and here that is
not a preference but the only arrangement in which this container can be probed
at all, for the reason above.

So: the browser reaches Traefik over TLS, Traefik reaches coolwsd over TLS, and
the certificate on that second hop is the self-signed one the image carries.
Nothing can vouch for it and nothing has to: the hop is to `127.0.0.1` inside one
pod. `insecure_skip_verify: true` renders a Traefik `serversTransport` named
after the route; `ssl.termination=false` follows from `ssl.enable=true` — TLS is
not terminated in front of coolwsd any more, coolwsd sees it, and the URLs it
generates are `https` either way.

## `chmod 0755 /var/www/html`, in the app's entrypoint

```
Entrypoint=/bin/sh
Exec=-c 'chmod 0755 /var/www/html; exec /entrypoint.sh php-fpm'
```

`html` is the one volume on this platform that two images share, and they are not
the same user inside it: php-fpm's workers and `occ` are `www-data`, nginx's
workers are `nginx`. The role creates a volume directory 0750; the Nextcloud
entrypoint chowns the tree it rsyncs in to `www-data` but never widens the mount
point itself. nginx could then stat nothing through it:

```
[crit] stat() "/var/www/html/status.php" failed (13: Permission denied)
```

— which arrives at the client as a 404, because `try_files $fastcgi_script_name
=404` is nginx's own check and it fails before the request ever reaches php-fpm.
A compose named volume was 0755 root-owned and the old stack never met this.

So the container that owns the tree makes its own mount point traversable before
handing over to the entrypoint it replaced. `/entrypoint.sh php-fpm` is the
image's own `Entrypoint` and `Cmd`, read off it with `podman image inspect` — a
version bump has to read them again. Only the mount point is touched: everything
under it keeps the modes the image gives it, and `data/` stays 0770.

## `AddCapability=MKNOD` on Collabora

Upstream's own `docker run` line asks for this one capability, and it is the
only thing this pod adds to the default set: `coolwsd` builds a chroot jail per
document and populates its `/dev` with `mknod`. Rootless, it is a capability
inside the service user's own user namespace and nothing at all on the host —
`svc-nextcloud` gains no privilege it did not have.

## Secrets

| Secret               | Where from                                                      |
| -------------------- | --------------------------------------------------------------- |
| `database_password`  | `secrets.sops.yaml` in this folder                              |
| `collabora_username` | `secrets.sops.yaml` in this folder                              |
| `collabora_password` | `secrets.sops.yaml` in this folder                              |
| `admin_user`         | `secrets.sops.yaml` in this folder                              |
| `admin_password`     | `secrets.sops.yaml` in this folder                              |
| `kopia_password`     | `hosts/<host>/secrets/kopia-clients.sops.yaml`, key `nextcloud` |

> **All five of this folder's secrets are `REPLACE_ME` and must be filled before
> the first deploy on storagebaby**, with
>
> ```sh
> make sops FILE=hosts/storagebaby/services/nextcloud/secrets.sops.yaml
> ```
>
> - `database_password` has to be **the password of the database that is migrated
>   in**. The postgres image only applies `POSTGRES_PASSWORD` when it initialises
>   an empty data directory; moved-in data keeps the old stack's password, and a
>   different value here means the app cannot log in to its own database.
> - `collabora_username` and `collabora_password` are Collabora's admin console
>   login, the old stack's `collabora_username.secret` and
>   `collabora_password.secret`. Collabora reads both from the environment and
>   supports no `_FILE` variable, so they arrive as **env-type** podman secrets:
>   podman injects them at start rather than writing them into the container's
>   config, so `podman inspect` does not show them.
> - `admin_user` and `admin_password` are only read **when the image installs**,
>   which on storagebaby it never does — the installation is migrated in and the
>   entrypoint leaves it alone. They are declared anyway because they are the only
>   thing that makes a **test host** usable: there, the volume starts empty, and
>   with the two variables present the image installs Nextcloud unattended
>   instead of leaving the web installer waiting for a human, which is what gives
>   the `occ` hooks something to run against. Test hosts generate all six values
>   fresh per run.

`kopia_password` is the client half of the shared value the Kopia server knows as
`client_nextcloud`; `hosts/storagebaby/services/kopia/README.md` has the whole
mechanism.

## Behind Traefik

```
Environment=NEXTCLOUD_TRUSTED_DOMAINS={{ routes[0].fqdn }}
Environment=OVERWRITEPROTOCOL=https
Environment=OVERWRITEHOST={{ routes[0].fqdn }}
```

Traefik terminates TLS and reaches nginx over plain HTTP on loopback, so
everything Nextcloud derives from the request it actually sees would be `http://`
on an address nobody can reach. `OVERWRITEHOST` and `OVERWRITEPROTOCOL` are what
make a redirect, a WebDAV URL and a share link come out as the public ones. They
are read at **runtime**, by the image's `reverse-proxy.config.php`;
`NEXTCLOUD_TRUSTED_DOMAINS` is different — the entrypoint applies it with `occ`
on install and on upgrade, so a changed route domain reaches an existing
installation only on the next image bump (or one `occ config:system:set` by
hand).

### Trusted proxies

`TRUSTED_PROXIES` is **not set**, on purpose, and that is a measurement rather
than an omission. The old compose stack passed Traefik's container address
(`TRAEFIK_CONTAINER_IP`); the equivalent here would be the address the pod sees a
proxied request come from. Measured in nginx's own access log, a request through
Traefik for `nextcloud.test.local` arrives as

```
192.168.122.247 - - [23/Sep/2026:22:30:28 +0200] "GET /status.php HTTP/1.1" … "127.0.0.1"
```

— `$remote_addr` is `192.168.122.247`, the **VM's own address**, while the
`X-Forwarded-For` Traefik set is the real client. Traefik runs with
`Network=host` and connects to `127.0.0.1:8280`, and pasta
rewrites that loopback source to the pod's address, which under rootless Podman
is the host's. So the value would have to be a different literal IP per host, for
a setting whose only job is to decide whether to believe `X-Forwarded-For`.

Nothing here depends on believing it. `OVERWRITEHOST`/`OVERWRITEPROTOCOL` fix the
generated URLs without it, and the remaining consumer is the client address in
the log and in brute-force protection — where trusting a header that a client can
set itself is the worse failure. Paperless and Immich found the same thing and
made the same call.

So the unit renders the line only when a host sets
`service_config.nextcloud.trusted_proxies` — for a host that really does sit
behind a second proxy. It is quoted in the unit (`Environment="TRUSTED_PROXIES=…"`)
because the value is a space-separated **list** to Nextcloud, and systemd would
otherwise keep the first address and drop the rest.

## The two timers

```
nextcloud-cron.timer    every five minutes   podman exec -u www-data nextcloud-app php -f /var/www/html/cron.php
nextcloud-dump.timer    daily at 02:30       podman exec nextcloud-database pg_dump -Fc … > /backups/nextcloud.dump
```

Plain systemd user units in `~svc-nextcloud/.config/systemd/user/`, not Quadlet
ones. Both declare `After=` **and** `Requisite=` on the container they exec into:
a `podman exec` into a container that is not running fails, and `Requisite=`
refuses to run the job rather than starting the pod behind the timer's back.

`nextcloud-cron` is what the compose stack had ofelia do. Nextcloud's own
recommendation is real cron over the AJAX fallback, and this is it: the
background jobs run whether or not anybody has the web UI open.

`nextcloud-dump` at 02:30 is half an hour before the snapshot at 03:00, so every
snapshot carries a dump from the same night. The dump is written to
`<name>.dump.tmp` and renamed, so `/backups` never holds a half-written file for
the sidecar to pick up — `mv` within one volume is atomic.

**A database is backed up as a dump, never as its data directory.** `database` is
therefore not in `backup.paths`; `backups` is.

## The after-change hooks

```yaml
hooks:
  after_change:
    - {
        container: nextcloud-app,
        user: www-data,
        command: 'php occ maintenance:mode --off'
      }
    - {
        container: nextcloud-app,
        user: www-data,
        command: 'php occ db:add-missing-columns'
      }
    - {
        container: nextcloud-app,
        user: www-data,
        command: 'php occ db:add-missing-indices'
      }
    - {
        container: nextcloud-app,
        user: www-data,
        command: 'php occ db:add-missing-primary-keys'
      }
    - {
        container: nextcloud-app,
        user: www-data,
        command: 'php occ maintenance:repair --include-expensive'
      }
```

These are the old `nextcloud/Makefile`'s `upgrade` target, minus the parts the
platform already does. `make upgrade` was: pull, recreate, `chown -R www-data`,
`occ upgrade`, then these five, then start. The pull is
`AutoUpdate=registry` or a tag bump in git; the recreate is the role restarting a
changed unit; the `chown` and `occ upgrade` are the image's entrypoint, which
runs them itself on a version change. What was left over is the five commands
here — the "and now finish the migration" half that nobody should have to
remember.

`when` is left at its default `unit_changed`, which is what makes them a deploy
step and not a nightly one: a version bump in `nextcloud-app.container.j2` runs
them, the nightly no-op does not. The role waits for `nextcloud-app`'s health
check before each one, and on this image that wait is real — see above.

`maintenance:mode --off` first, because `occ upgrade` leaves the instance in
maintenance mode and the four commands after it refuse to run while it is on.

## Backups

```yaml
backup:
  paths: [html, backups]
  schedule: '03:00'
  retention: { latest: 3, daily: 7, weekly: 4, monthly: 12, annual: 3 }
```

The role generates `nextcloud-backup.container` from this, mounts the two volumes
read-only at `/data/<volume>`, connects to the Kopia server as
`nextcloud@<host>` and leaves a scheduler running, so the snapshots happen at
03:00 without a timer of their own. `ansible/roles/service/README.md` has the
sidecar's mechanics.

`html` is the installation and all of the user data under it; `backups` carries
the nightly dump. The server lists

```
nextcloud@test-a:/data/html
nextcloud@test-a:/data/backups
```

## Dropped from the compose stack

- **Traefik labels.** Podman containers are invisible to Traefik's Docker
  provider; the role writes one file-provider route per `routes` entry instead.
- **`certresolver: letsencrypt` on Collabora.** Superseded by the wildcard
  certificate Traefik issues for the whole domain.
- **Watchtower labels.** `AutoUpdate=registry` and the service user's
  `podman-auto-update.timer` replaced them.
- **The ofelia labels.** `nextcloud-cron.timer` replaced them, and it is a
  systemd timer rather than a container that watches other containers.
- **`depends_on`.** `After=`/`Wants=` on the generated unit names, which is the
  same claim made to the thing that actually starts them.
- **`TRAEFIK_CONTAINER_IP`.** A `docker inspect` in a Makefile is not something a
  deploy can depend on; see "Trusted proxies".

## Migrating the data

`nextcloud/docker-compose.yml`'s two named volumes map onto this folder's three:

| Old (rootful compose)                   | New                                                               |
| --------------------------------------- | ----------------------------------------------------------------- |
| `nextcloud_nextcloud` (`/var/www/html`) | `/pool/apps/nextcloud/html`                                       |
| `nextcloud_database`                    | `/var/lib/storagebaby/fast/nextcloud/database`                    |
| —                                       | `/var/lib/storagebaby/fast/nextcloud/backups` (new, for the dump) |

The repo's root README has the recipe and the ownership rules. The nextcloud
image runs the app as its own in-container user (`www-data`, uid 33), so the
moved trees have to be chowned under `podman unshare` as `svc-nextcloud`, not on
the host directly — and the database directory has to end up owned by postgres's
in-container uid (70 in `postgres:17-alpine`), which is a subuid of
`svc-nextcloud` on the host.

Two things to check after the first converge on storagebaby, because they are
what an installation carries rather than what a unit declares:

```sh
make ps SERVICE=nextcloud
podman exec -u www-data nextcloud-app php occ status
podman exec -u www-data nextcloud-app php occ config:system:get trusted_domains
```

`trusted_domains` comes from the migrated `config.php`, not from
`NEXTCLOUD_TRUSTED_DOMAINS` — the entrypoint only applies that on install and
upgrade — so if the domain changed it is one `occ config:system:set` by hand.
