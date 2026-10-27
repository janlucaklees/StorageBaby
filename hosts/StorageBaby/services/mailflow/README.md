# Mailflow

Self-hosted unified webmail — several IMAP/SMTP accounts in one inbox.
<https://mailflow.klees.io>. Upstream: <https://github.com/maathimself/mailflow>.

Moved off the VPS (`CloudBaby/mailflow`). It comes up **empty**: this folder is the service,
not the data. The migration is a separate step, and the one thing it must carry is
`encryption_key` (below).

## Stack

One pod, four containers and a timer:

- `mailflow-frontend` — nginx plus the built SPA, container port 80. The only part the route
  reaches; it proxies every API path to the backend.
- `mailflow-backend` — the Node API on 3000, pod-internal.
- `mailflow-database` — Postgres 17. The only copy of anything that matters.
- `mailflow-broker` — redis, sessions and cache. The sessions are the reason it has a volume
  at all, unlike every other redis on this host; see Volumes.
- `mailflow-dump.timer` — `pg_dump` into `backups` at 02:30.

The frontend and the backend are two halves of one release and carry the same tag, **4.0.5**;
they are bumped together, never one at a time.

## Three settings that come from reading the image, not the compose file

Upstream's `docker-compose.yml` is written for Docker, and three of its assumptions are wrong
here. All three have supported escape hatches, so none of this needs a patched config:

- **`BACKEND_HOST=127.0.0.1`, `BACKEND_PORT=3000`.** The shipped `nginx.conf` hard-codes
  `proxy_pass http://mailflow_api` with the Compose service name `backend:3000`, which does
  not resolve in a pod. The entrypoint script `17-set-backend.sh` rewrites that one marked
  line from these variables, and its own comment names the case: the Compose default "is
  wrong everywhere the backend is named something else — Kubernetes services, **Podman
  pods**, split hosts". So no `AddHost=backend:127.0.0.1` alias is needed here, unlike immich.
- **`NGINX_TLS=off`.** The image ships two server blocks: 443 with a self-signed certificate
  it generates on start, and 80 "intended for deployments behind a reverse proxy that
  terminates TLS upstream". `18-set-tls.sh` comments the HTTPS half out. Hence a plain-HTTP
  route with no `scheme: https` and no `insecure_skip_verify` — kopia and collabora need
  those because their protocols do, and this does not.
- **`TRUST_PROXY_HOPS=2`.** Traefik, then this pod's nginx. Upstream permits 2 only "if
  clients cannot reach MailFlow's ports except through it", because otherwise a client
  connecting directly chooses the address it is rate-limited and logged under. On this
  platform that condition is structural: the pod publishes on `127.0.0.1` alone — the
  port being the one Cradle derives for the route — and
  Cradle's `tests/static/test_quadlet_conventions.py` refuses any other bind.

Neither image ships `curl`, so upstream's own compose healthchecks cannot be working. Both
health checks here use busybox `wget`, which is in `nginx:alpine` and `node:22-alpine` alike.

All three were verified on test-a rather than reasoned about, and re-verified against the
4.0.5 images: `set-backend.sh`, `set-tls.sh` and the two marker lines in `nginx.conf` are
unchanged, and the frontend still has `wget` and no `curl`. What a correctly configured
frontend looks like from inside the container:

```sh
podman exec mailflow-frontend grep -A8 'upstream mailflow_api' /etc/nginx/conf.d/default.conf
#   server 127.0.0.1:3000 resolve; # mailflow-backend-managed   <- BACKEND_HOST/PORT applied
#   resolver 169.254.1.1 …         # mailflow-managed           <- detect-resolver.sh applied
# and the HTTPS server block's lines each carry a `#tls-off#` prefix                <- NGINX_TLS=off
```

The end-to-end check is the API path, not `/` — the SPA answers `/` whether or not nginx
proxies anything:

```sh
curl -sk -H 'Host: mailflow.klees.io' https://127.0.0.1/api/health # {"status":"ok"}
```

## `encryption_key` is not rotatable

It encrypts the stored IMAP/SMTP passwords and OAuth tokens at rest. Upstream's warning,
verbatim: "If this key is lost or changed, all stored credentials become unreadable and users
will need to re-enter passwords / re-authenticate OAuth accounts."

When the VPS data is migrated, the value from `CloudBaby/mailflow/mailflow_encryption_key.secret`
must be the value in this folder's `secrets.sops.yaml`. Generating a fresh one and restoring
the database gives a working instance with every stored account unreadable.

## Volumes

| volume        | class | owner | what it holds                            |
| ------------- | ----- | ----- | ---------------------------------------- |
| `database`    | fast  | 70    | the Postgres data directory              |
| `sessions`    | fast  | 999   | redis's snapshot: who is signed in       |
| `image_cache` | fast  | 1000  | remote images the image proxy has fitted |
| `backups`     | pool  | —     | the nightly dump, written by the timer   |

### Redis keeps its data here, unlike everywhere else on this platform

MailFlow's sessions are neither cookies carrying their own state nor rows in Postgres:
express-session stores them through `connect-redis`, so redis's dataset _is_ the list of who
is signed in. Until 2026-10-27 this service followed paperless, nextcloud and openarchiver
and gave its redis no volume — and so every restart, whether a deploy, a redis auto-update or
a reboot, signed everyone out. That is what the `sessions` volume fixes, with upstream's own
`--save 60 1`: one snapshot sixty seconds after a change, so a hard kill loses at most the
last minute's sign-ins and a graceful stop loses none.

Two consequences worth knowing. Nothing snapshots this volume — a session is not state a
restore wants back, and losing it costs one sign-in. And persistence buys the failure mode
nextcloud's unit avoids on purpose: a redis that cannot write its snapshot stops accepting
writes, which here means nobody can sign in. The dataset is kilobytes and it sits on `fast`,
so that needs the SSD to be full before it bites.

## Upgrading is a tag bump and nothing else

Both images move together, and the backend does the rest by itself: it applies every pending
schema migration before it starts listening (`runMigrations()` ahead of `httpServer.listen()`
in upstream's `backend/src/index.js`), under a Postgres session advisory lock, each migration
in its own transaction. So there is no `hooks.after_change` here and nothing to run on the
host — which is why `HealthStartPeriod` on the backend is 300 s rather than the usual 60: the
health endpoint is dark for as long as the migrations take, and `HealthOnFailure=kill` would
otherwise restart the container in the middle of an upgrade.

Two things a bump can still ask for, both of which have to be read out of the release notes
before it goes out: a new volume (4.0.0 added one, for the image proxy's cache) and a new
setting (4.0.5 added the Microsoft app switch). 3.8.3 → 4.0.5 carried migrations 0063–0070
and needed both.

## Secrets

| secret              | what it is                                                       |
| ------------------- | ---------------------------------------------------------------- |
| `database_password` | Postgres. File form for the database, `type=env` for the backend |
| `session_secret`    | session signing key, `openssl rand -hex 32`                      |
| `encryption_key`    | the one above — carry it over, never regenerate                  |
| `kopia_password`    | the backup client, `client_mailflow` in kopia's own secrets      |

## Backup and restore

Only `backups` is snapshotted: there is no upload tree and no index, so the dump is the whole
of it. `database` is never snapshotted as a data directory.

```sh
doas /usr/local/sbin/podman-as svc-mailflow podman exec -i mailflow-database \
	psql --single-transaction --set ON_ERROR_STOP=on -U mailflow -d mailflow < mailflow.sql
```

## Not configured

Web push (`VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY`) and Google OAuth for Gmail accounts. Both
are upstream features this folder leaves off; adding either is a `config:` key plus a secret.

MailFlow's own Microsoft app is switched off (`ms_builtin_app: false`), so adding a personal
Outlook.com or Hotmail account would need an app registered in Microsoft Entra, exactly as it
did before 4.0.5. Turning it back on is a `config:` flip — or the same switch in the UI under
Settings > Email providers, which needs no deploy.
