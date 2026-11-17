# Mailflow

Self-hosted unified webmail — several IMAP/SMTP accounts in one inbox.
<https://mailflow.klees.io>. Upstream: <https://github.com/maathimself/mailflow>.

**This host runs JLK's fork**, <https://github.com/janlucaklees/mailflow>: upstream plus the
archive structure (Settings → Accounts → Folder mappings → Archive structure: Single folder,
By year, By year and month), which is meant to go upstream as a pull request. The images are
`ghcr.io/janlucaklees/mailflow-{backend,frontend}`, private, hence `registries: [ghcr.io]`.
Once upstream ships the feature, this goes back to `ghcr.io/maathimself/` and the
`registries` line goes.

Moved off the VPS (`CloudBaby/mailflow`). It comes up **empty**: this folder is the service,
not the data. The migration is a separate step, and the one thing it must carry is
`encryption_key` (below).

## Stack

One pod, three containers and a timer:

- `mailflow-frontend` — Caddy plus the built SPA, container port 80. The only part the route
  reaches; it proxies every API path to the backend.
- `mailflow-backend` — the Node API on 3000, pod-internal.
- `mailflow-database` — Postgres 17. The only copy of anything that matters.
- `mailflow-dump.timer` — `pg_dump` into `backups` at 02:30.

The frontend and the backend are two halves of one release and carry the same tag, **4.4.1-archive.2**
(upstream 4.4.0 plus its unreleased commits up to `c2e56144`, plus the archive structure);
they are bumped together, never one at a time.

## Three settings that come from reading the image, not the compose file

Upstream's `docker-compose.yml` is written for Docker, and three of its assumptions are wrong
here. All three have supported escape hatches, so none of this needs a patched config:

- **`BACKEND_HOST=127.0.0.1`, `BACKEND_PORT=3000`.** The frontend's start script
  (`frontend/caddy/start.sh` upstream, `/usr/local/bin/mailflow-start` in the image) points
  Caddy's proxy at these, defaulting to the Compose service name `backend:3000`, which does
  not resolve in a pod. Its own comment names the case: "for Kubernetes, Podman and split
  hosts". So no `AddHost=backend:127.0.0.1` alias is needed here, unlike immich.
- **`TLS_MODE=off`.** Caddy serves HTTP on port 80 only, with no 443 listener and no
  self-signed certificate. Hence a plain-HTTP route with no `scheme: https` and no
  `insecure_skip_verify` — kopia and collabora need those because their protocols do, and
  this does not. (`NGINX_TLS=off`, the name before 4.4, still means the same.)
- **`TRUST_PROXY_HOPS=2`.** Traefik, then this pod's frontend. Upstream permits 2 only "if
  clients cannot reach MailFlow's ports except through it", because otherwise a client
  connecting directly chooses the address it is rate-limited and logged under. On this
  platform that condition is structural: the pod publishes on `127.0.0.1` alone — the
  port being the one Cradle derives for the route — and
  Cradle's `tests/static/test_quadlet_conventions.py` refuses any other bind.

Neither image ships `curl`. Both health checks use busybox `wget`, which is in `caddy:2-alpine`
and `node:22-alpine` alike; the frontend's probes Caddy's own `127.0.0.1:2080/healthz`, as
upstream's compose does.

Verified against a local build of the 4.4.1-archive.2 source: with these three variables the
start script logs `HTTP on port 80 only (TLS_MODE=off)`, `/healthz` and `/` answer, and 443
refuses. The end-to-end check is the API path, not `/` — the SPA answers `/` whether or not
the proxy reaches the backend:

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
| `image_cache` | fast  | 1000  | remote images the image proxy has fitted |
| `backups`     | pool  | —     | the nightly dump, written by the timer   |

### No redis since 4.4

Sessions and short-lived keys moved into Postgres (migration 0076), so the `mailflow-broker`
container and its `sessions` volume are gone. The upgrade signs everyone out once; after that
a restart no longer does, because the sessions are in `database`. The old `sessions`
directory stays on disk, as every volume a service stops declaring does, and holds nothing
anyone reads.

The rendered `mailflow-broker.container` stays too: Cradle leaves a unit file a service stops
carrying where it is. It is not inert, though — it still says `Pod=mailflow.pod` and the
generated pod unit `Wants=` it, so a redis keeps running in the pod that nothing talks to.
Harmless, and gone with `/etc/containers/systemd/users/<uid>/mailflow-broker.container`.

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
and needed both. 4.1.0 → 4.4 carried 0072–0081 and asked for the opposite: a container
taken away (redis) and a setting renamed (`NGINX_TLS` → `TLS_MODE`). Read the release notes'
"Upgrading" sections across the whole gap, not just the newest.

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
