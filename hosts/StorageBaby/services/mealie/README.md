# Mealie

Recipe manager, meal planner and shopping list. <https://mealie.klees.io>

Public, not `home.klees.io`: the shopping list is read in a supermarket, which is the one
place the VPN is not already on.

## Stack

One pod, two containers and a timer:

- `mealie-app` — the whole application, frontend and API in one image, on port 9000.
- `mealie-database` — Postgres 17, the only copy of the data that matters.
- `mealie-dump.timer` → `mealie-dump.service` — `pg_dump` into the `backups` volume at
  02:30, half an hour before the backup sidecar runs.

The parts reach each other on `127.0.0.1` inside the pod; `POSTGRES_SERVER=127.0.0.1` and
not a container name for that reason.

## The admin account is an operator step, and it is time-critical

Mealie seeds a default administrator on first start and offers no environment variable to
set its password — the backend-config page documents none, so there is nothing this folder
can declare. The first deploy therefore brings up a public service whose administrator
credentials are the upstream defaults.

**Sequence the first deploy so the window does not exist:** deploy, log in immediately, change
the administrator's e-mail and password, and only then create the `mealie.klees.io` A record.
`ALLOW_SIGNUP=false` is set, so nobody can register an account of their own either way, but
that is a second lock on the door and not this one.

## Volumes

| volume     | class | owner | what it holds                                   |
| ---------- | ----- | ----- | ----------------------------------------------- |
| `data`     | pool  | 911   | recipe images, user files, the generated secret |
| `database` | fast  | 70    | the Postgres data directory                     |
| `backups`  | pool  | —     | the nightly dump, written by the timer          |

`911` is the image's own `useradd -u 911 … abc`, and `PUID`/`PGID` both default to it; this
service does not set them. The role chowns `data` to the host uid that maps to container uid
911 once, before the units start, so a tree copied in beforehand comes up readable. Confirm
rather than trust it:

```sh
doas /usr/local/sbin/podman-as svc-mealie podman exec mealie-app id -u # expect 911
```

`backups` declares no `owner` because the dump timer writes it through `podman exec` with no
`-u` — container root, which is the service user itself.

## Secrets

- `database_password` — Postgres. Reaches both containers as a file:
  `POSTGRES_PASSWORD_FILE` on the database and, since Mealie 2.4.2, the same `_FILE` form on
  the application. Preferred over `type=env` wherever an image offers it, because the value
  then never appears in a container's environment at all.
- `kopia_password` — the backup client's own password, the same string as `client_mealie` in
  kopia's `secrets.sops.yaml`. Two files on purpose: a service folder carries its own
  password rather than depending on a shared set.

## Backup and restore

`data` and `backups` are snapshotted at 03:00; `database` never is. A restore is the dump,
not the data directory:

```sh
doas /usr/local/sbin/podman-as svc-mealie podman exec -i mealie-database \
	psql --single-transaction --set ON_ERROR_STOP=on -U mealie -d mealie < mealie.sql
```

`--clean --if-exists --no-owner` are already in the dump, which is why this is `psql` and not
`pg_restore`.

## The MCP adapter

Mealie is reached by `mealie-mcp`, a stdio child of the `mcp` service, over
`https://mealie.klees.io` with an API token. The token is created here — Settings → API
Tokens — and stored as `mealie_api_token` in `mcp`'s secrets, never in this folder: the
adapter is the consumer, so the credential belongs with it.

## Updating

The tag is pinned and bumped in git. Postgres floats on `17-alpine` with
`AutoUpdate=registry`, which keeps patch releases current without changing the major version
the data directory was initialised with.
