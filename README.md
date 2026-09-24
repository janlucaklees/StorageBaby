# StorageBaby

Git-driven configuration for my self-hosted services and the Arch hosts they
run on. Design: `docs/superpowers/specs/2026-09-21-gitops-podman-platform-design.md`.

## Layout

- `hosts/<host>/host.yml` — everything specific to one host (domain, storage
  roots, later disks/snapraid/samba).
- `hosts/<host>/services/<name>/` — a service placed on that host.
- `hosts/shared/services/<name>/` — a service that runs on every host.
- `ansible/` — the site playbook and roles that turn the above into a running host.
- `tests/` — static checks and the Molecule integration scenario.

Each service folder holds `service.yml` (routes, volumes, binds, secrets, shared
host secrets, after-change hooks, backup policy), `quadlet/*.j2` (Podman Quadlet
units, plus plain systemd `*.timer`/`*.service` units for scheduled commands),
optional `config/`, and `secrets.sops.yaml`. Values that two services on one host
have to agree on — a Kopia client password, say — live in
`hosts/<host>/secrets/<set>.sops.yaml` instead. `ansible/roles/service/README.md` is
the full reference.

## Services

Domains are relative to the host's own domain — `jellyfin.home.klees.io` on
storagebaby. "auto" means `AutoUpdate=registry` plus the service user's
`podman-auto-update.timer`, which rolls back an image that fails its health check.
A **pod** is one Podman pod per service: every part keeps its own image, container
and generated unit, all of them share one network namespace and reach each other on
`127.0.0.1`, and the pod is the only thing that publishes a port — on loopback, for
Traefik.

| Service                              | Route → loopback port                      | Images and updates                                                                                                      | Backup                                                    | Phase |
| ------------------------------------ | ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- | ----- |
| traefik (`hosts/shared`, every host) | `traefik.*` → 8080 (dashboard)             | `docker.io/library/traefik:v3` — auto                                                                                   | none                                                      | 1     |
| yuzukam                              | `yuzukam.*` → 3000                         | `ghcr.io/janlucaklees/yuzukam:latest` — auto                                                                            | none (stateless)                                          | 2     |
| stirling-pdf                         | `stirling.*` → 8180                        | `docker.stirlingpdf.com/stirlingtools/stirling-pdf:latest` — auto                                                       | none                                                      | 2     |
| jellyfin                             | `jellyfin.*` → 8096                        | `lscr.io/linuxserver/jellyfin:latest` — auto                                                                            | none                                                      | 2     |
| kopia                                | `kopia.*` → 51515                          | `docker.io/kopia/kopia:0.23.1` — pinned, bumped in git                                                                  | the repository server itself, plus one account per client | 2     |
| paperless-upload                     | none                                       | host build (`.build` unit from the service's own `config/build/`)                                                       | none                                                      | 2     |
| paperless (pod)                      | `paperless.*` → 8000                       | app `paperless-ngx:2.20.15` pinned; postgres, redis, gotenberg and tika auto                                            | `data`, `media`, `backups` (nightly dump)                 | 3     |
| openarchiver (pod)                   | `openarchiver.*` → 3001                    | app `v0.6.0`, `meilisearch:v1.38` and `tika:3.2.2.0-full` pinned; postgres and valkey auto                              | `data`, `backups` (nightly dump)                          | 3     |
| immich (pod)                         | `immich.*` → 2283                          | server and machine learning `v2.7.5` pinned together, the vectorchord postgres pinned by digest beside them; redis auto | `upload` — Immich writes its own database dumps into it   | 3     |
| nextcloud (pod)                      | `nextcloud.*` → 8280, `collabora.*` → 9980 | app `33-fpm-alpine` pinned; nginx, postgres, redis and Collabora auto                                                   | `html`, `backups` (nightly dump)                          | 3     |

Everything but traefik is placed on storagebaby; `test-a` places all nine folders by
symlink, `test-ci` the smaller subset a GitHub runner can carry.

Kopia is pinned on purpose — a kopia upgrade can carry a repository format upgrade,
which is not a decision for a nightly timer. The pods pin on the same rule: whatever
carries a migration when it moves (an application schema, a search index format, a
database extension set) is a line in git, and the interchangeable parts float.

`openarchiver`'s route port is 3001 while the app itself listens on 3000 — yuzukam
already has 3000 on the host, and only the pod's `PublishPort=` line sees both
numbers.

A service with a `backup:` block gets a Kopia client container **generated into its
pod** by the `service` role; it is not in the service folder. It connects to
`https://kopia.<domain>` as `<service>@<host>`, applies the retention policy from
`service.yml` and takes its snapshots on its own schedule. Databases are backed up as
dumps, never as data directories: a `<name>-dump.timer` writes `pg_dump -Fc` into a
`backups` volume half an hour before the snapshot, and that volume is what the sidecar
carries.

What is left of the old repository is Phase 4: `samba/` and `snapraid/` at the root,
still hand-stowed units and scripts. Nothing else at the root is a service any more —
a new one goes under `hosts/`.

## Operator steps before and right after the first storagebaby deploy

Everything in this section is a secret, a piece of data or a permission — the three
things that cannot live in git or in a role. All of it but the last step has to be
done **before** storagebaby converges the first time; the last one can only be done
**after**, because it needs groups the converge creates.

There is no grace period to do it in afterwards. CI fast-forwards `stable` on a green
push and the deploy timer pulls it within five minutes, unattended, as root — so the
ordering is: fill the secrets and move the data, **then** let the commit that places
the service land on `master`.

### 1. Fill the placeholders

`REPLACE_ME` is the literal value in git wherever the real one was unknown when the
service was migrated. Each file is opened with `make sops FILE=<path>`.

| File                                                            | Keys                                               | What goes in                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| --------------------------------------------------------------- | -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `hosts/shared/services/traefik/secrets.sops.yaml`               | `porkbun_api_key`, `porkbun_secret_api_key`        | **Nothing.** Real already — carried over from the old `.secret` files in Phase 1.                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `hosts/storagebaby/services/kopia/secrets.sops.yaml`            | `b2_key_id`, `b2_application_key`                  | `REPLACE_ME`. The Backblaze application key id and key; there was no credential in the old stack to carry over. Confirm `s3_endpoint` and `s3_bucket` in `service.yml` against the account while you are there — they were written from the old README, not read off it.                                                                                                                                                                                                                                       |
|                                                                 | `repository_password`                              | Generated for this migration. Correct **only if the bucket is fresh**; see step 2.                                                                                                                                                                                                                                                                                                                                                                                                                             |
|                                                                 | `server_password`                                  | Generated. The web UI login for `server_username: jlk`, free to choose and free to rotate.                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `hosts/storagebaby/services/paperless/secrets.sops.yaml`        | `database_password`, `secret_key`                  | Both `REPLACE_ME`, and both have to be the **existing** values: the password of the database being migrated in, and the live `paperless_secret_key.secret` (a new one logs every user out and invalidates every API token).                                                                                                                                                                                                                                                                                    |
| `hosts/storagebaby/services/openarchiver/secrets.sops.yaml`     | all six                                            | **Nothing.** Real already — the old stack's six `openarchiver_*.secret` files. Do not regenerate any of them: `encryption_key` and `storage_encryption_key` decrypt the archive, `jwt_secret` signs the sessions.                                                                                                                                                                                                                                                                                              |
| `hosts/storagebaby/services/immich/secrets.sops.yaml`           | `database_password`                                | `REPLACE_ME`. The password of the database being migrated in. `database_name` is `postgres`, not `immich`, for the same reason — that is what the old stack called it.                                                                                                                                                                                                                                                                                                                                         |
| `hosts/storagebaby/services/nextcloud/secrets.sops.yaml`        | `database_password`                                | `REPLACE_ME`. The password of the database being migrated in.                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
|                                                                 | `collabora_username`, `collabora_password`         | `REPLACE_ME`. Collabora's admin console login, the old stack's `collabora_*.secret`.                                                                                                                                                                                                                                                                                                                                                                                                                           |
|                                                                 | `admin_user`, `admin_password`                     | `REPLACE_ME`. Read only when the image **installs**, which on storagebaby it must never do — read step 5 before this one.                                                                                                                                                                                                                                                                                                                                                                                      |
| `hosts/storagebaby/services/paperless-upload/secrets.sops.yaml` | `paperless_token`                                  | `REPLACE_ME_paperless_api_token`. The file it was to be carried over from was empty; issue a new API token in Paperless once it is up.                                                                                                                                                                                                                                                                                                                                                                         |
| `hosts/storagebaby/secrets/kopia-clients.sops.yaml`             | `paperless`, `openarchiver`, `immich`, `nextcloud` | All `REPLACE_ME`, and all a **free choice**: each is one string that the Kopia server and that service's backup sidecar both read, so it only has to be the same on both sides. It would otherwise be the one placeholder that fails **silently** — both sides match, the account works, and the repository endpoint is public at `https://kopia.<domain>` — so `config/start.sh` refuses to register a client whose password is still `REPLACE_ME`, and the kopia server restart-loops until they are filled. |

A postgres password is not a free choice because the image only applies
`POSTGRES_PASSWORD` when it initialises an **empty** data directory. Moved-in data
keeps the old stack's password, and a different value here means the application
cannot log in to its own database. Each service's README spells its own values out:
`hosts/storagebaby/services/<name>/README.md`, section "Secrets".

### 2. Check whether the B2 bucket already holds a repository

Kopia's `start.sh` connects if it can and creates if it cannot, so a fresh bucket
needs nothing more. A bucket that already holds the old stack's repository needs that
repository's **existing** password in `repository_password`, in place of the
generated one — otherwise both the connect and the create fail and the unit
restart-loops. `hosts/storagebaby/services/kopia/README.md` spells the two cases out.

### 3. Back up kopia's repository password outside this repository

If the bucket is fresh, the password in `secrets.sops.yaml` is what the repository
will be encrypted with, and it lives nowhere else. Kopia derives the repository's
encryption keys from it: no reset, no escrow, no way into the snapshots without it.

### 4. Restart kopia after changing the client set

Client accounts are not clicked together: `config/start.sh` registers one
`<service>@<host>` user per `client_*` secret, on every start, before the server
binds. So a change to `hosts/storagebaby/secrets/kopia-clients.sops.yaml` reaches the
server only when `kopia.service` restarts. A converge does that by itself — the
secret sync reports changed and restarts the unit — but after any edit that did not
go through a deploy:

```bash
make restart SERVICE=kopia
```

before expecting a sidecar to connect. A sidecar whose account does not exist yet
fails its connect and retries; a running server also rereads its user list within
5–10 minutes.

### 5. Move the data before the placing commit reaches `stable`

For all four pods, and for nextcloud it is the step that cannot be got wrong.
"Migrating existing service data" below is the recipe; the ordering is the point
here. What a converge does when it finds empty volumes is not a failure and not
visible: postgres initialises a fresh cluster from `POSTGRES_PASSWORD_FILE`, the
Nextcloud entrypoint sees an empty `html` and runs **the installer** with
`admin_user` / `admin_password`, and the `occ` hooks then run against that new empty
instance and succeed. With the placeholders still in place that is a brand-new
Nextcloud with an admin account `REPLACE_ME` / `REPLACE_ME` published on the public
name, and nothing anywhere reports a problem.

Filling the secrets first does not avoid it — it only changes the password of the
instance that should not exist. Moving the data is the half that does.
`hosts/storagebaby/services/nextcloud/README.md`, "The one ordering step that cannot
be got wrong", has it in full.

### 6. Expect OpenArchiver's live stack to be broken already

The old `openarchiver/docker-compose.yml` never set `JWT_EXPIRES_IN`, which
`createServer` validates alongside `JWT_SECRET` — so the backend throws on startup
while the frontend binds anyway and answers 500 from its own proxy. The pod looks up,
nothing works, and it is close to invisible from outside. The new unit sets it
(`config.jwt_expires_in: 7d`), so this is fixed by the migration rather than caused
by it — but check the live stack before migrating, because a "working" archive there
may not have been one. `hosts/storagebaby/services/openarchiver/README.md` has the
symptom and the log lines.

### 7. Converge once, then open the shared trees to the services that read them

This one is deliberately after the first converge: the `media` and `scans` groups do
not exist on the host until the `service` role creates them, so a `chgrp` run before
it has nothing to chgrp to. Expect jellyfin to come up with an empty library and
paperless-upload to restart-loop (it cannot create `processed/` in a tree it may not
write) until these run:

```bash
doas chgrp -R media /pool/shared/media
doas chmod -R o+rX /pool/shared/media
doas chgrp -R scans /pool/shared/scans
doas chmod -R g+rwX /pool/shared/scans
doas find /pool/shared/scans -type d -exec chmod g+s {} +
```

The setgid bit is per directory and is not inherited by anything that already
exists, so a plain `chmod g+s /pool/shared/scans` would fix the share root and
leave every subdirectory already under it — `processed/`, and whatever the
scanner made — without it. Hence the `find`.

The role creates a bind directory only when it is missing and never touches
the permissions of one that exists, so both trees stay the operator's — which
is also why these are not one-shot: anything dropped into them later by
another writer inherits whatever that writer gives it.

For the media tree the `o+rX` is what Jellyfin actually reads through: the
linuxserver image drops its supplementary groups when s6 switches to its own
user, so group access never reaches the app process —
`hosts/storagebaby/services/jellyfin/README.md` has the measurement. The
`chgrp` still matters anyway, because the `media` group is what Samba and the
rest of the host use, and it is what the role itself would set on a tree it
creates. The uploader keeps its groups, so for the scans tree the group is the
whole mechanism — and the `g+s` is what makes the existing tree match the
`2775` the role would have given a fresh one, so files the scanner and the
uploader drop there stay group-`scans` instead of falling back to the writer's
own group.

Two more things are not steps but expectations about that first converge:

- **Existing volume directories keep their owner and mode.** A converge creates
  the ones that are missing and leaves the rest alone, so anything already on the
  pool stays exactly as it is.
- **GPU transcoding is unverified until real hardware runs it.** Jellyfin reaches
  `/dev/dri` through a udev rule `host_base` installs on a `gpu: true` host. The
  test VM has no GPU, so nothing in this repo proves it works — check it after
  cutover.
- **A deploy that ends in "After-change hooks were skipped for: …" converged
  everything else.** A service's after-change hooks wait ten minutes for their
  container to report healthy; when it never does, that one service's hooks are
  skipped and every other service still converges. The failure is raised as the very
  last task of the play, naming the services and the containers — so the message is the
  list of what to look at, not the point where the converge stopped. Read those
  services' journals from the top (`make logs SERVICE=<name>`): on the first converge
  this is what a `database_password` that does not match the migrated cluster looks
  like. The next converge runs the skipped hooks, once the container is healthy.

## Migrating existing service data

**Nothing in this repository moves the old stack's data.** The role creates a
volume directory when it is missing, leaves an existing one alone, and never
repairs ownership afterwards — it is create-only, by design, because images chown
their own data tree and an enforced mode would fight them on every converge. So
carrying the data over is the operator's job, done once, by hand, per service.
**A service whose data is not moved simply starts empty** — new library, new
settings — and moving it later means stopping the service and redoing the two
steps below. For nextcloud, "starts empty" is worse than it sounds: see operator
step 5.

### The two layouts

|                          | Path                                                   |
| ------------------------ | ------------------------------------------------------ |
| old, bind mount          | `/pool/apps/<svc>/volumes/<name>`                      |
| old, Docker named volume | `/var/lib/docker/volumes/<stack>_<volume>/_data`       |
| new (this repo)          | `<storage root for the volume's class>/<svc>/<volume>` |

The old compose files used both: the big pool-resident trees were absolute binds,
everything else was an ordinary named volume, which rootful Docker keeps under its
own data root. `<stack>` is the compose project name, which is the directory the
`docker-compose.yml` sat in — `paperless_data`, `nextcloud_database` and so on.
`docker volume inspect <name> --format '{{.Mountpoint}}'` says it for certain.

`storage_roots` in `hosts/storagebaby/host.yml` resolves the classes: `pool` →
`/pool/apps`, `fast` → `/var/lib/storagebaby/fast`. Which volume is which class is
in each service's `service.yml`. Concretely:

| Service      | old                                                                | new                                                        |
| ------------ | ------------------------------------------------------------------ | ---------------------------------------------------------- |
| jellyfin     | `/pool/apps/jellyfin/volumes/jellyfin_config`                      | `/pool/apps/jellyfin/config`                               |
| kopia        | `/pool/apps/kopia/volumes/config`                                  | `/pool/apps/kopia/config`                                  |
| kopia        | `/pool/apps/kopia/volumes/cache`                                   | `/var/lib/storagebaby/fast/kopia/cache`                    |
| kopia        | `/pool/apps/kopia/volumes/logs`                                    | `/var/lib/storagebaby/fast/kopia/logs`                     |
| stirling-pdf | `/pool/apps/stirling-pdf/volumes/{configs,logs,pipeline,tessdata}` | `/pool/apps/stirling-pdf/{configs,logs,pipeline,tessdata}` |
| paperless    | `paperless_data` (named)                                           | `/pool/apps/paperless/data`                                |
| paperless    | `paperless_media` (named)                                          | `/pool/apps/paperless/media`                               |
| paperless    | `paperless_database` (named)                                       | `/var/lib/storagebaby/fast/paperless/database`             |
| paperless    | `paperless_broker` (named)                                         | — (a Redis queue; start empty)                             |
| openarchiver | `/pool/apps/openarchiver/volumes/data`                             | `/pool/apps/openarchiver/data`                             |
| openarchiver | `openarchiver_database` (named)                                    | `/var/lib/storagebaby/fast/openarchiver/database`          |
| openarchiver | `openarchiver_cache` (named)                                       | `/var/lib/storagebaby/fast/openarchiver/cache`             |
| openarchiver | `openarchiver_meilisearch` (named)                                 | `/var/lib/storagebaby/fast/openarchiver/meilisearch`       |
| immich       | `/pool/apps/immich/volumes/immich_upload`                          | `/pool/apps/immich/upload`                                 |
| immich       | `immich_database` (named)                                          | `/var/lib/storagebaby/fast/immich/database`                |
| immich       | `immich_model-cache` (named)                                       | `/var/lib/storagebaby/fast/immich/model-cache`             |
| nextcloud    | `nextcloud_nextcloud` (named, `/var/www/html`)                     | `/pool/apps/nextcloud/html`                                |
| nextcloud    | `nextcloud_database` (named)                                       | `/var/lib/storagebaby/fast/nextcloud/database`             |

`stirling-pdf`'s old folder is not a compose stack — it is the untracked Quadlet
attempt that preceded this repo, with the same four names under `volumes/`.
Kopia's fourth volume, `repo`, is not in the table: it holds a repository only
under `repository: filesystem`, which is the test hosts' backend, so on
storagebaby it stays empty and there is nothing to migrate into it. The `backups`
volume of paperless, openarchiver and nextcloud is new and starts empty — the dump
timer fills it on its first run. `immich`'s `model-cache` is downloadable weights and
can be left behind as easily as moved; its `upload` tree is where Immich puts its own
database dumps, once they are switched on (its README has the switch).
`yuzukam` and `paperless-upload` declare no volumes at all: yuzukam is stateless,
and paperless-upload's only state is the `scans` bind, which stays where it is and
is handled by the shared-trees operator step above.

### The recipe, per service

1. Stop the old stack (`cd <old dir> && docker compose down`, or the old Quadlet
   unit for stirling-pdf) and stop the new unit if it already ran:
   `make stop SERVICE=<svc>`.
2. Move each old tree onto its new path. Where both sides are on the same
   filesystem — pool to pool, or a Docker named volume under `/var/lib` to the
   `fast` root under `/var/lib` — `mv` is a rename, costs nothing and leaves no
   second copy to forget about. The one crossing is nextcloud's `html`, from
   Docker's data root on the system disk to the pool, which is a real copy:
   `rsync -aHAX --info=progress2 <old>/ <new>/` and remove the old volume
   afterwards.
3. Fix the ownership for the rootless mapping. This is the step that is easy to
   skip and impossible to skip, and the owner it has to be fixed _from_ differs per
   service: jellyfin's old tree is owned by host **uid 1000**, because rootful
   Docker ran the linuxserver image under `PUID=1000` — and under rootless Podman
   that uid is the operator's login user, not the service. Kopia's old tree is
   **root-owned**, because that image runs as root and Docker ran it rootful.
   Neither is what the new mapping needs.

**Which chown depends on what uid the image runs as _inside_ the container**, because
that is what the user namespace maps. `podman unshare` is what translates a
container-side uid into the host uid it actually lands on, and it has to run as the
service user — with that user's runtime directory, the same way every other rootless
command in this repo is invoked:

- **jellyfin** — the linuxserver image drops to `PUID=1000`, so the tree has to end
  up owned by container uid 1000, which is a subuid of `svc-jellyfin` on the host:

  ```bash
  uid=$(id -u svc-jellyfin)
  cd /tmp # rootless podman cannot chdir back into root's 0700 home
  doas runuser -u svc-jellyfin -- env XDG_RUNTIME_DIR=/run/user/$uid \
  	podman unshare chown -R 1000:1000 /pool/apps/jellyfin/config
  ```

- **stirling-pdf** — its in-container uid is **not** established anywhere here;
  nothing in this repo measured it. Two ways out, both fine: read it once after the
  first start with `podman exec stirling-pdf id -u` and put that number into the
  command above, or chown the migrated trees to `0:0` under `podman unshare` —
  container root, which _is_ `svc-stirling-pdf` on the host — and let the image's own
  start-time chown finish the job.

- **kopia** — runs as root inside, so container uid 0 maps straight onto the service
  user itself and a plain chown says it:

  ```bash
  doas chown -R svc-kopia:svc-kopia /pool/apps/kopia/config
  doas chown -R svc-kopia:svc-kopia /var/lib/storagebaby/fast/kopia/cache
  ```

  (`runuser -u svc-kopia -- env XDG_RUNTIME_DIR=/run/user/$(id -u svc-kopia) podman unshare chown -R 0:0 <path>`
  is the same thing said the other way round.)

- **the four pods** — two in-container uids each, so two chowns each: the
  application's tree to the uid its own image runs as, and the database tree to
  postgres's uid inside the database image. Every one of them runs under the service
  user of its own pod, so the pattern is one command with two names substituted:

  ```bash
  svc=svc-paperless # or svc-openarchiver, svc-immich, svc-nextcloud
  uid=$(id -u "$svc")
  cd /tmp
  doas runuser -u "$svc" -- env XDG_RUNTIME_DIR=/run/user/$uid \
  	podman unshare chown -R 70:70 /var/lib/storagebaby/fast/paperless/database
  ```

  `70` is postgres's uid in `postgres:17-alpine`, which is what paperless,
  openarchiver and nextcloud run. Immich's database is not stock postgres but its own
  vectorchord build, so **read that one rather than assume it**:
  `podman exec immich-database id -u postgres`. The application trees are the same
  story — `podman exec paperless-app id -u`, `openarchiver-app`, `immich-server`, and
  for nextcloud `www-data`, uid 33. Reading the number off the running container once
  is always cheaper than guessing it.

- **paperless-upload** — nothing to do. It declares no volumes, and its unit runs
  `UserNS=keep-id:uid=1000,gid=1000`, so container uid 1000 **is**
  `svc-paperless-upload`'s own host uid: no subuid, nothing for `podman unshare` to
  translate. Its only state is the `/pool/shared/scans` bind, and that one is opened
  by group and setgid in the shared-trees operator step — never by a chown.

Then `make start SERVICE=<svc>` and check `make ps SERVICE=<svc>`. If the ownership
is wrong the container comes up and fails — nothing on the host will quietly fix it
on the next converge.

### Databases: the directory or a dump, not both

Each of the four pods has a postgres container, and there are two ways to give it the
old stack's data. They are alternatives, and the choice changes what the secret has to
be:

- **Move the data directory** (the rows above). The cluster comes over as it is, which
  means it keeps the old stack's password: `database_password` in the service's
  `secrets.sops.yaml` has to be that existing password, because the image only applies
  `POSTGRES_PASSWORD` to an **empty** data directory and never to one it finds.
- **Restore a dump into a fresh cluster.** Leave the `database` volume empty, let the
  image initialise it from whatever `database_password` says, and load a dump
  afterwards. Take the dump from the old stack before it is stopped
  (`docker compose exec database pg_dump -Fc -U <user> <db> > <file>`), then restore
  it into the running new one as the service user:

  ```bash
  /usr/local/sbin/podman-as svc-paperless podman exec -i paperless-database \
  	pg_restore -U paperless -d paperless --no-owner < /path/to/paperless.dump
  ```

  `-U` and `-d` are `config.database_user` and `config.database_name` from the
  service's `service.yml`. `--no-owner` because the roles in the dump are the old
  stack's. `pg_restore` does not empty what is already there, so restore into a
  cluster that has just been initialised, or drop and recreate the database first.
  `hosts/storagebaby/services/nextcloud/README.md`, "Restoring one", has the same
  command for nextcloud with its own user and database name.

Immich is the exception in one respect: it writes its own `pg_dump` output into
`upload/backups/` rather than through a platform dump timer, so a restore there reads
a file from inside the `upload` tree. The dumps are **off by default** — switch them
on under Administration → Settings → Backup after the first deploy, or the snapshots
hold the photos and nothing that can rebuild the index.

Two service-specific notes:

- **kopia's `cache` belongs to whichever repository `config` points at.** Move both
  or neither; a cache from a different repository fails at startup with
  `cipher: message authentication failed`, which reads like a wrong password and is
  not one. `cache` is rebuildable, so leaving it behind is always safe.
- **A migrated kopia `config` carries the old `repository.config`**, and `start.sh`
  skips the connect whenever that file is present — which is the point, the
  connection is already made. To force a fresh connect from `service.yml` and the
  secrets instead, delete `repository.config` before starting.

## New host

Copy the script to the fresh Arch install and run it as root:

    scp bootstrap.sh root@<host>:/root/ && ssh root@<host> 'bash /root/bootstrap.sh --repo git@github.com:janlucaklees/StorageBaby.git'

`stable` is created by CI on the first green push to master, so push and let CI
run before bootstrapping the first host.

Protect `stable` on GitHub: no direct pushes, no force pushes, no deletion — it
is moved by CI alone. GitHub Actions must still be allowed to push to it: the
`promote` job fast-forwards `stable` with the workflow token, so if "restrict
who can push" is enabled, add the Actions actor to the allow list or promotion
stops there.

The host's hostname must equal its folder name under `hosts/` — the deploy unit
converges `--limit <hostname>` and fails loudly if no such folder exists.

Add the printed deploy key to the repository, then add the printed age recipient
to `.sops.yaml` in two places: the host's own rule, and the `hosts/shared/**`
rule — every host runs the shared services and has to decrypt their secrets.
Then run `sops updatekeys` on every affected `*.sops.yaml` —
`make sops FILE=...` opens one for editing — then commit and push. The host
pulls `stable` every 5 minutes.

Until the host's age recipient is in `.sops.yaml` and `sops updatekeys` has run
on the secret files it needs, its first converge fails at secret decryption.
That is expected: the host cannot read anything it was not encrypted to.

## Operating a service

Every service runs as its own lingering user `svc-<name>`, so its units belong
to that user's systemd manager, not the system one. The Makefile wraps that —
run these on the host as root (they are the only targets that do not go through
the devtools image):

    make ps SERVICE=traefik
    make start SERVICE=traefik
    make stop SERVICE=traefik
    make restart SERVICE=traefik
    make logs SERVICE=traefik

For a pod service (`paperless`, `openarchiver`, `immich`, `nextcloud`) they resolve
to `<name>-pod.service` and for a single-container one to `<name>.service`; which of
the two a service is cannot be read off its name, so the target asks the service
user's manager. Restarting the pod is the only restart its containers need — Quadlet
binds them to it.

They are thin wrappers, so the raw forms still work:

    systemctl --user -M svc-traefik@ status traefik.service
    systemctl --user -M svc-traefik@ restart traefik.service

`journalctl` has no `--user -M` equivalent, so read logs by unit name instead:

    journalctl _SYSTEMD_USER_UNIT=traefik.service -f

Nothing is ever changed on a host by hand — this is for looking, and for the
occasional restart. The fix belongs in git.

## Working on the repo

    make devtools              # build the tooling image (once)
    make format                # prettier over the whole repo
    make fmt-check             # check only, no writes
    make test-static           # contract, secrets, render checks
    make test-integration      # Molecule scenario test-ci in a KVM VM, converging hosts/test-a
    MOLECULE_HOST=test-ci make test-integration   # same scenario, the smaller CI placement
    make molecule CMD=converge # a single Molecule step in that scenario
    make molecule-login        # SSH into the running test VM
    make molecule-exec CMD='podman ps -a'   # one command on it, no TTY needed
    make test-clean            # destroy the VM and drop the Molecule cache
    make sops FILE=hosts/shared/services/traefik/secrets.sops.yaml
    make install-hooks         # once per clone: lefthook's formatting hook

Docker and lefthook are all the workstation needs for everything but the
integration tests. Those drive real KVM machines through the host's libvirt, so
they additionally need `qemu-base libvirt dnsmasq iptables-nft`, `libvirtd`
enabled, and libvirt's `default` network active. On a machine that also runs
Docker, set `firewall_backend = "iptables"` in `/etc/libvirt/network.conf` —
with the nftables backend Docker's rules drop the VM network's traffic.
