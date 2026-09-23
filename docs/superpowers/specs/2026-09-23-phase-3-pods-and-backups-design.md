# Phase 3: pods, backup clients and timers

Status: reviewed, approved (2026-09-23)
Date: 2026-09-23
Extends: `2026-09-21-gitops-podman-platform-design.md` and `2026-09-22-phase-2-single-services-design.md`.

## 1. Scope

Migrate the four multi-container stacks onto `hosts/storagebaby/services/` as Podman pods: paperless, openarchiver, immich, nextcloud. Deliver the backup machinery the spec promised: a role-generated Kopia client sidecar per service with a `backup` block, client registration on the Kopia server, database dump timers. Add the two contract pieces the stacks need: several routes per service and post-change hooks. Retire `nextcloud/`, `paperless/`, `immich/`, `openarchiver/`.

JLK's rules for this phase: tests never use real data or secrets (generated values on test hosts; placeholders for storagebaby where the value is unknown, real values only where they already sit in the repo checkout: OpenArchiver's six); every deploy step runs on deploy, nothing by hand, with per-command control of when it runs.

## 2. Contract additions

```yaml
name: nextcloud
routes: # replaces domain+port when a service has more than one hostname
  - { domain: nextcloud, port: 8280 }
  - { domain: collabora, port: 9980 }
host_secrets: # secrets shared between services on this host
  kopia_password: kopia-clients.nextcloud # <set>.<key> from hosts/<host>/secrets/<set>.sops.yaml
hooks:
  after_change: # run after this service's units were restarted by a change
    - {
        container: nextcloud-app,
        user: www-data,
        command: 'php occ db:add-missing-indices',
        when: unit_changed
      }
backup:
  paths: [html, backups] # volume names, mounted read-only into the sidecar at /data/<name>
  schedule: '03:00'
  retention: { latest: 3, daily: 7, weekly: 4, monthly: 12, annual: 3 }
```

- **`routes`** is a list of `{domain, port}`; `domain` + `port` at the top level stay valid as the one-route shorthand. The role renders one Traefik route file per entry (`<name>-<domain>.yml`, router `<name>-<domain>`), the pod publishes every route port on `127.0.0.1`, and `test_ports` counts all of them.
- **`host_secrets`** maps a podman secret name for this service to a key in a per-host shared secrets file `hosts/<host>/secrets/<set>.sops.yaml` (encrypted under that host's rule, so only that host can read it). Used for Kopia client passwords: each client references `kopia-clients.<service>`, the kopia server references every `kopia-clients.<service>` as `client_<service>`. Static tests check that referenced keys exist (key names are plaintext) and that no plain `secrets` entry has the same name. Test hosts get generated set files from `prepare.yml`.
- **`hooks.after_change`** is a list of `podman exec` commands the role runs, in order, after it restarted the service's units because a unit, config or secret changed (`when: unit_changed`, the default) or after every converge (`when: always`). Each hook waits for its container's health check first. A version bump in a `.container.j2` therefore runs the Nextcloud post-upgrade commands on deploy, and a service can opt out per command.
- **`backup`** is now consumed by the role: it generates `<name>-backup.container` in the pod from a role template, mounting each listed volume read-only at `/data/<volume>`, connecting to `https://kopia.<domain>` as `<name>@<host>` with the `kopia_password` secret, applying the policy, and keeping a scheduler running. A service with `backup` must define a `.pod` (the sidecar joins it). Backups of databases are dumps, not data directories: a service declares a dump timer (below) writing into a `backups` volume that is listed in `paths`.
- **Timers**: `quadlet/*.timer.j2` and `*.service.j2` are plain systemd user units, rendered into `~svc-<name>/.config/systemd/user/`, enabled and started by the role, restarted on change like the Quadlet units. Used for database dumps (`<name>-dump`) and Nextcloud's cron (`nextcloud-cron`, every 5 minutes, `podman exec -u www-data nextcloud-app php -f /var/www/html/cron.php`).

`host.yml` additions: none. `hosts/<host>/secrets/<set>.sops.yaml` is the only new file kind.

## 3. Pods

One `<name>.pod` per service. Containers `Pod=<name>.pod`, `ContainerName=<name>-<part>` (`-app`, `-database`, `-cache`, `-broker`, `-tika`, `-gotenberg`, `-meilisearch`, `-ml`, `-nginx`, `-collabora`, `-backup`). Inside a pod everything is `127.0.0.1`; each part keeps its upstream port, and the pod's `PublishPort=127.0.0.1:<route port>:<container port>` lines are the only host-visible ports. Dependency order with `After=`/`Wants=` on the generated unit names (`<name>-database.service` before `<name>-app.service`); apps that retry connections tolerate a slow database, but the order avoids restart noise. `AddHost=kopia.<domain>:host-gateway` on the pod so the backup sidecar reaches Traefik on the host without DNS (harmless where DNS exists).

Secrets: every part that supports `_FILE` variables gets `Secret=<name>`; parts that only take environment get `Secret=<name>,type=env,target=<VAR>`. Composite values (OpenArchiver's `DATABASE_URL`) are assembled by a small `Entrypoint=` shell wrapper from the secret file, so a password is stored once.

| Service      | Parts                                                                                                                     | Route ports                                              | Volumes (class)                                                      | Update policy       | Backup                                        |
| ------------ | ------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- | -------------------------------------------------------------------- | ------------------- | --------------------------------------------- |
| paperless    | app (pinned), database postgres:17-alpine (auto), broker redis (auto), gotenberg:8 (auto), tika (auto), backup            | 8000                                                     | data pool, media pool, database fast, broker fast, backups fast      | app pinned 2.20.15  | data, media, backups (pg_dump daily)          |
| openarchiver | app (pinned v0.6.0), database (auto), cache valkey (auto), meilisearch v1.38 (pinned), tika 3.2.2.0-full (pinned), backup | 3000                                                     | data pool, database fast, cache fast, meilisearch fast, backups fast | as listed           | data, backups                                 |
| immich       | server (pinned), ml (pinned), database (pinned digest), cache redis (auto), backup                                        | 2283                                                     | upload pool, database fast, model-cache fast                         | server/ml/db pinned | upload (Immich writes its own DB dumps there) |
| nextcloud    | app fpm (pinned 33-fpm-alpine), nginx (auto), database (auto), cache redis (auto), collabora (auto), backup               | 80 (nginx) → route `nextcloud`; 9980 → route `collabora` | html pool, database fast, backups fast                               | as listed           | html, backups                                 |

Nextcloud: `config/` carries the nginx and php-fpm files as today; `TRUSTED_PROXIES` is the pod's gateway address as seen from pasta (determined on the test VM and set through `service.config`); `NEXTCLOUD_TRUSTED_DOMAINS` from the route; cron via timer; `hooks.after_change` runs, as `www-data` in `nextcloud-app` after it is healthy: `php occ maintenance:mode --off`, `php occ db:add-missing-columns`, `db:add-missing-indices`, `db:add-missing-primary-keys`, `maintenance:repair --include-expensive`. The image runs `occ upgrade` itself on a version change. Collabora reads its credentials from env-type secrets.

Paperless: consume and export mounts dropped (the uploader posts via the API), `USERMAP_GID` dropped, `PAPERLESS_TRUSTED_PROXIES` as for Nextcloud.

## 4. Kopia

Server: the kopia service gains `host_secrets` entries `client_<service>: kopia-clients.<service>` for every service with a backup, and the start script registers or updates each `<service>@<host>` user from `/run/secrets/client_*` before starting the server (repository-level command, no server needed). Clients: the role-generated sidecar connects to `https://kopia.<domain>` (LE certificate on storagebaby; on test hosts, `acme: false`, it pins the server certificate fingerprint fetched at start), sets the policy from `backup`, and runs `kopia server start --no-ui --insecure --address http://127.0.0.1:51516` so the scheduler executes the snapshots. Verification on the test VM: trigger a snapshot from a sidecar and list it on the server.

Database dumps: `<name>-dump.timer` daily before the snapshot time, `<name>-dump.service` runs `podman exec <name>-database pg_dump -U <user> -Fc <db> > /backups/<db>.dump` through the database container with the `backups` volume mounted at `/backups`. Verification: start the dump service once, assert the dump exists.

## 5. Tests

- Static: `routes` shape and unique ports across routes; `host_secrets` references resolve to keys in an existing set file and do not collide with `secrets`; `hooks` shape; every `.pod.j2` publishes exactly the route ports on `127.0.0.1`; every container template in a pod has `Pod=`; `backup` requires a pod and lists only declared volumes; timers come in `.timer`/`.service` pairs.
- Integration (`test_service.py` extended): `<name>-pod.service` active; every part active and healthy; routes answer; timers enabled; dump service produces a file; backup sidecar connected and a triggered snapshot visible on the server; hooks ran (Nextcloud: `occ status` reports installed and no maintenance mode). `test_deploy` gains a hook case: a change to a container unit of a service with hooks leaves a marker the hook writes.
- Placement: `test-a` places everything, VM 12 GiB; `hosts/test-ci/` places traefik, yuzukam, kopia, paperless-upload and the paperless pod for the GitHub runner, selected by `MOLECULE_HOST` (default `test-a`); `ci.yml` sets `test-ci` and 5 GiB.
- Test hosts generate all `secrets` and `host_secrets` values in `prepare.yml`; nothing real is used.

## 6. Migration order

1. Contracts and role: `routes`, `host_secrets`, `hooks`, timers, pod-aware unit handling and `make` wrappers, role-generated backup sidecar, static tests.
2. Harness: `hosts/test-ci/`, `MOLECULE_HOST`, VM size, `test_service` pod/timer/backup/hook checks, prepare generating host secret sets, kopia server client registration.
3. paperless (first pod, first dump timer, first client). 4. openarchiver (env-type secrets, entrypoint wrapper). 5. immich. 6. nextcloud (routes, hooks, cron, collabora).
4. Docs, retire old directories, operator steps (secrets to fill, data migration rows for the four stacks).

## 7. Operator items this phase creates

Unknown secret values become placeholders in the storagebaby secrets files: paperless database password and secret key, nextcloud database password, collabora credentials, immich database password, and the `kopia-clients` set. Databases must be migrated with their existing passwords or recreated. Snapraid's remote snapshot plugins are untouched (they talk to another host).
