# StorageBaby

Git-driven configuration for my self-hosted services and the Arch hosts they
run on. Design: `docs/superpowers/specs/2026-09-21-gitops-podman-platform-design.md`.

## Layout

- `hosts/<host>/host.yml` — everything specific to one host: domain, storage
  roots, and the `storage` block that declares the filesystems the host is made
  of (disks, parity, the mergerfs pool, the snapraid array and its nightly run).
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
| paperless (pod)                      | `paperless.*` → 8000, FTP 21 + 21100–21109 | app `paperless-ngx:2.20.15` and `pure-ftpd:trixie-1.0.50` pinned; postgres, redis, gotenberg and tika auto              | `data`, `media`, `backups` (nightly dump)                 | 3, 4  |
| openarchiver (pod)                   | `openarchiver.*` → 3001                    | app `v0.6.0`, `meilisearch:v1.38` and `tika:3.2.2.0-full` pinned; postgres and valkey auto                              | `data`, `backups` (nightly dump)                          | 3     |
| immich (pod)                         | `immich.*` → 2283                          | server and machine learning `v2.7.5` pinned together, the vectorchord postgres pinned by digest beside them; redis auto | `upload` — Immich writes its own database dumps into it   | 3     |
| nextcloud (pod)                      | `nextcloud.*` → 8280, `collabora.*` → 9980 | app `33-fpm-alpine` pinned; nginx, postgres, redis and Collabora auto                                                   | `html`, `backups` (nightly dump)                          | 3     |

Everything but traefik is placed on storagebaby; `test-a` places all eight folders by
symlink, `test-ci` the smaller subset a GitHub runner can carry. Both test hosts also place
two test-only fixtures, in no table here because they are not NAS services: `tcp-echo`,
which echoes a payload back so the `tcp_ports` path can be measured end to end, and
`build-echo`, which runs the image its own `.build` unit produces so the host-build path
stays covered. They are the only placements that are a real folder per host rather than a
symlink — there is nothing to link to, and neither belongs on the NAS — and a static test
keeps each one's two copies byte-identical.

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

Nothing is left of the old repository. `samba/` and `snapraid/` were the last two
directories at the root, and both are retired: the mounts, the pool, the array and the
nightly maintenance run are the `storage` role below, and Samba is dropped entirely
rather than migrated. A new service goes under `hosts/`.

## Storage

The filesystems a host is made of are declared, not stowed: `storage` in
`hosts/<host>/host.yml` names the data disks, the parity disk and the mergerfs pool over
them, and the `storage` role mounts exactly those.

```yaml
storage:
  disks:
    - {
        name: d1,
        device: /dev/disk/by-partuuid/…,
        mount: /mnt/data/data1,
        fstype: ext4
      }
  parity:
    - {
        name: parity1,
        device: /dev/disk/by-id/…,
        mount: /mnt/parity/parity1,
        fstype: ext4
      }
  pool:
    mount: /pool
    options: defaults,allow_other,use_ino,category.create=pfrd,…
  snapraid: { block_size: 256, excludes: [...], maintenance: { ... } }
  mail: { to: …, from: …, smtp_host: …, smtp_port: 587, smtp_user: … }
```

```
/mnt/data/data1, data2, data3   data disks (ext4, one rendered .mount unit each)
/mnt/parity/parity1             the snapraid parity disk
/pool                           the mergerfs union over the data disks
/pool/apps/<service>/<volume>   pool-class service volumes (storage_roots.pool)
/pool/shared/media              the media library Jellyfin reads
```

- **The role never partitions, formats or wipes.** There is no `mkfs`, `parted` or
  `wipefs` anywhere in it. A declared device that is not there **fails the converge**,
  naming the entry, and after mounting it asserts that every declared path really is a
  mount point. That pair is what keeps a converge with `/pool` unmounted from recreating
  `/pool/apps` — empty, unattended, from the nightly deploy timer — on the root filesystem.
  It runs **before `host_base`**, which is what creates those directories.
- **The pool's create policy is `pfrd`** — proportional free random distribution: a branch
  is picked at random, weighted by how much free space it has. (Everything this repository
  said about `eplfs` was wrong: the deployed `pool.mount` never said that.) The option
  string is one line in `host.yml` and is copied into the unit verbatim.
- **Changing `pool.options`, or adding or moving a branch, remounts the pool**, and every
  container holding a bind mount under it comes back reading an empty directory until it
  is restarted. Run the playbook with `--check --diff` first, stop the pool-class services,
  converge, start them.
- **mergerfs comes from Chaotic-AUR; snapraid and `mergerfs-tools` are built from the AUR**
  by the role itself, each pinned by AUR commit and bumped like an image tag. The role
  never runs `pacman -Syu` and never reboots — upgrading a host is the operator's job.

`ansible/roles/storage/README.md` is the full reference, including **"Adding a disk"**:
partition and `mkfs.ext4` it by hand, read its `PARTUUID` off `lsblk`, add the entry to
`host.yml`, push. The role does the rest, and the next nightly `snapraid sync` folds the
disk into the array.

### The nightly maintenance run

`storage-maintenance.timer` fires at 02:00 (`Persistent=true`, so a host that was off
catches up) and runs the orchestrator in `/opt/storagebaby/maintenance/`: snapraid status,
`mergerfs.balance` while the branches are more than 5 % apart, `snapraid touch` + `sync`,
a scrub of the new blocks and 8 % of anything older than 12 days, then a final status and
a SMART report. Logs land in `/var/log/storage-maintenance/` and `/var/log/snapraid/`, and
the whole run is mailed — `mutt` composing, `msmtp` sending through the relay in
`storage.mail`, with the password in `hosts/<host>/secrets/mail.sops.yaml`.

Services that must not be running while files move between branches are named in
`storage.snapraid.maintenance.stop_services` (storagebaby: `jellyfin`). The role turns each
name into a plugin directory that stops it before the balance and starts it again after the
scrub — **and on failure**, so a run that aborts never leaves a service down. If the pool
does not come up at all, the maintenance unit's start job fails before the wrapper ever
runs and nothing would be sent; `storage-maintenance-failed.service`, hooked on
`OnFailure=`, is the mail that says so.

Every parameter above is `storage.snapraid.maintenance` in `host.yml`, rendered into one
file (`maintenance.env`) that every script sources — so a threshold is changed in git, and
running `bash /opt/storagebaby/maintenance/sync.sh` by hand behaves exactly like the
timer's run.

## Networking

Traefik is the only process on a routable port. It runs rootless as `svc-traefik` with
`Network=host`, owns 80 and 443, and every other service publishes on `127.0.0.1:<port>`
only — rootless containers of different users cannot reach each other's loopback, so
service-to-service traffic goes out through Traefik and the public name.

Plain TCP goes through the same door. A service declares `tcp_ports` (paperless:
`{port: 21, target: 2121}` plus the passive range `21100–21109`), the playbook collects
every placed port, and Traefik gets one entrypoint per port forwarding to that service's
loopback port. Three consequences worth knowing before the first converge:

- **A TCP entrypoint binds one concrete address**, `tcp_bind_address`, which defaults to
  the host's default-route address (`ansible_default_ipv4.address`) and is overridable as
  a top-level key in `host.yml`. A wildcard listener and a pod publishing
  `127.0.0.1:<same port>` cannot coexist, and an FTP passive range needs exactly that
  shape. It is also the address the FTP server advertises in its `PASV` reply, so on a
  host with several addresses it has to be **the one the scanner reaches**, or transfers
  connect, log in and then hang.
- **A declared port below 80 pulls the unprivileged-port sysctl down with it.** Traefik is
  rootless and binds 21 for paperless, so `host_base` sets
  `net.ipv4.ip_unprivileged_port_start` to the minimum of 80 and the host's declared TCP
  ports — 21 on storagebaby. That is host-wide and not Traefik's: **every** unprivileged
  user on the host may then bind 21–79, 25 and 53 included. Single-tenant NAS, and the
  value is derived from the placement, so it returns to 80 the converge after the
  declaration goes.
- **The FTP drop carries no TLS.** Printers speak plain FTP, and explicit FTPS cannot be
  terminated by a TCP proxy, so the scanner's credentials and the scans themselves cross
  the LAN in the clear. That is the exposure, and it is bounded by it being one account
  that can only write into paperless's consume directory.

## Operator steps before and right after the first storagebaby deploy

Everything in this section is a secret, a piece of data, a permission or a check — the
things that cannot live in git or in a role. Steps 1–8 have to be done **before**
storagebaby converges the first time; step 9 **is** that converge, and it is a planned
outage run by hand rather than something to let the timer walk into. Steps 10–12 can only
be done after it: they need groups and units the converge creates, or they check what the
converge cannot.

There is no grace period to do it in afterwards. CI fast-forwards `stable` on a green
push and the deploy timer pulls it within five minutes, unattended, as root — so the
ordering is: fill the secrets and move the data, **then** let the commit that places
the service land on `master`, with the deploy timer stopped until step 9 says otherwise.

### 1. Fill the placeholders

`REPLACE_ME` is the literal value in git wherever the real one was unknown when the
service was migrated. Each file is opened with `make sops FILE=<path>`.

| File                                                        | Keys                                               | What goes in                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ----------------------------------------------------------- | -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `hosts/shared/services/traefik/secrets.sops.yaml`           | `porkbun_api_key`, `porkbun_secret_api_key`        | **Nothing.** Real already — carried over from the old `.secret` files in Phase 1.                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `hosts/storagebaby/services/kopia/secrets.sops.yaml`        | `b2_key_id`, `b2_application_key`                  | `REPLACE_ME`. The Backblaze application key id and key; there was no credential in the old stack to carry over. Confirm `s3_endpoint` and `s3_bucket` in `service.yml` against the account while you are there — they were written from the old README, not read off it.                                                                                                                                                                                                                                       |
|                                                             | `repository_password`                              | Generated for this migration. Correct **only if the bucket is fresh**; see step 2.                                                                                                                                                                                                                                                                                                                                                                                                                             |
|                                                             | `server_password`                                  | Generated. The web UI login for `server_username: jlk`, free to choose and free to rotate.                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `hosts/storagebaby/services/paperless/secrets.sops.yaml`    | `database_password`, `secret_key`                  | Both `REPLACE_ME`, and both have to be the **existing** values: the password of the database being migrated in, and the live `paperless_secret_key.secret` (a new one logs every user out and invalidates every API token).                                                                                                                                                                                                                                                                                    |
|                                                             | `ftp_password`                                     | `REPLACE_ME`, and a **free choice**: it is the password of the one FTP account the scanner delivers with, and the only other place it exists is the printer's own configuration. Set the printer to FTP, `<storagebaby's LAN address>`, port 21, user `scanner`, passive mode.                                                                                                                                                                                                                                 |
| `hosts/storagebaby/services/openarchiver/secrets.sops.yaml` | all six                                            | **Nothing.** Real already — the old stack's six `openarchiver_*.secret` files. Do not regenerate any of them: `encryption_key` and `storage_encryption_key` decrypt the archive, `jwt_secret` signs the sessions.                                                                                                                                                                                                                                                                                              |
| `hosts/storagebaby/services/immich/secrets.sops.yaml`       | `database_password`                                | `REPLACE_ME`. The password of the database being migrated in. `database_name` is `postgres`, not `immich`, for the same reason — that is what the old stack called it.                                                                                                                                                                                                                                                                                                                                         |
| `hosts/storagebaby/services/nextcloud/secrets.sops.yaml`    | `database_password`                                | `REPLACE_ME`. The password of the database being migrated in.                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
|                                                             | `collabora_username`, `collabora_password`         | `REPLACE_ME`. Collabora's admin console login, the old stack's `collabora_*.secret`.                                                                                                                                                                                                                                                                                                                                                                                                                           |
|                                                             | `admin_user`, `admin_password`                     | `REPLACE_ME`. Read only when the image **installs**, which on storagebaby it must never do — read step 5 before this one.                                                                                                                                                                                                                                                                                                                                                                                      |
| `hosts/storagebaby/secrets/mail.sops.yaml`                  | `smtp_password`                                    | `REPLACE_ME`. The password of the relay the nightly maintenance report is sent through. `storage.mail.smtp_host` and `smtp_user` in `hosts/storagebaby/host.yml` are `REPLACE_ME` beside it, and all three have to be filled: the deployed wrapper used whatever MTA the host happened to have, which this repository never captured. Until they are real, the maintenance run succeeds and its mail step fails — nightly, silently.                                                                           |
| `hosts/storagebaby/secrets/kopia-clients.sops.yaml`         | `paperless`, `openarchiver`, `immich`, `nextcloud` | All `REPLACE_ME`, and all a **free choice**: each is one string that the Kopia server and that service's backup sidecar both read, so it only has to be the same on both sides. It would otherwise be the one placeholder that fails **silently** — both sides match, the account works, and the repository endpoint is public at `https://kopia.<domain>` — so `config/start.sh` refuses to register a client whose password is still `REPLACE_ME`, and the kopia server restart-loops until they are filled. |

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

### 7. Check the storage declaration against the running host

`hosts/storagebaby/host.yml`'s `storage` block was written from the deployed mount units
and `/etc/snapraid.conf`, not from a measurement of the live machine — so read it back
before it is converged. Four device paths, four mount points, one pool option string:

```sh
lsblk -o NAME,SIZE,PARTUUID,PARTLABEL,MOUNTPOINTS
ls -l /dev/disk/by-id/ | grep -i wdc
findmnt /pool -o SOURCE,FSTYPE,OPTIONS
```

- The three data disks are `by-partuuid` paths, the parity disk a `by-id` one. All four
  have to resolve on the host, and to the disk the entry names.
- **`pool.options` has to stay byte-identical to the deployed `pool.mount`**,
  `category.create=pfrd` included. It is copied into the rendered unit verbatim; a
  difference here is a different pool.
- **The disk names `d1`, `d2`, `d3` are snapraid's identity for those disks** and must not
  be changed. Renaming one makes snapraid treat the whole disk as new — a full parity
  rewrite on the next sync.

The rendered units still cannot be byte-identical to the stowed ones (`pool.mount`'s
`What=` is the explicit branch list where the stowed one had the glob `/mnt/data/*`, and
each branch unit's `Description=` differs), which is why step 9 exists.

### 8. Fill in the FTP side, and point the scanner at the host

`ftp_password` is in the table above. Two more things, neither of them a secret:

- **`tcp_bind_address`**, a top-level key in `hosts/storagebaby/host.yml`. It defaults to
  the host's default-route address, and that is right on a host with one address. On a host
  with several — a second NIC, a VPN interface, a bridge — set it explicitly to the address
  the scanner reaches, because it is both what Traefik's TCP entrypoints bind and what the
  FTP server advertises in its `PASV` reply. Wrong value, and the printer connects, logs in
  and then hangs on the transfer.
- **The printer's profile**: FTP (not SFTP, not FTPS), host `<that address>`, port 21, user
  `scanner` (`config.ftp_user`), the `ftp_password` from step 1, **passive mode**. Nothing
  else is needed — the consumer picks up whatever lands in the consume volume and deletes
  it once the document is ingested.

### 9. The first converge is a planned outage — run it by hand

The first converge replaces the hand-stowed storage units with rendered ones. None of the
five can be byte-identical to what is there (step 7), so **all four branch units and
`/pool` are remounted, once**. Every container holding a bind mount under `/pool` comes
back reading an empty directory until it is restarted, and `systemctl stop pool.mount` is
a plain `umount`: it fails with `EBUSY` while anything at all still holds `/pool` open —
which `smbd` does, serving `/pool/shared/*`, right up until step 10 retires it.

So this one converge does not belong to the deploy timer. Stop the timer **before** the
placing commit reaches `stable`:

```sh
doas systemctl stop storagebaby-deploy.timer
```

then, with the commit on `stable`, bring the checkout to it and read the whole diff first:

```sh
# The repository is reachable only through the host's deploy key, which lives in the
# deploy unit's GIT_SSH_COMMAND and nowhere in root's ssh config.
export GIT_SSH_COMMAND='ssh -i /etc/storagebaby/deploy_key -o IdentitiesOnly=yes'
doas -E git -C /var/lib/storagebaby/repo fetch origin stable
doas git -C /var/lib/storagebaby/repo checkout --detach FETCH_HEAD
cd /var/lib/storagebaby/repo
doas ansible-playbook -i ansible/inventory/hosts.yml --limit "$(uname -n)" \
	--check --diff ansible/playbook.yml
```

`--check --diff` over the whole playbook is expected to end `failed=0` and to change
nothing — every probe in every role carries `check_mode: false` for exactly this, and the
Molecule harness runs this same command on a fresh VM before its first converge and
asserts the recap.

What it prints on a **first** converge is less than it prints later, and that is not a
fault:

- the `storage` role's whole diff — the five mount units, `/etc/snapraid.conf`, the
  maintenance scripts, the timer and `/etc/msmtprc` — plus a line naming the units that
  are not on the host yet and the filesystems the real run will mount. It cannot enable or
  mount anything it did not write, so it says so instead of failing;
- `host_base`'s diff;
- then **one line per service**, `svc-<name> does not exist on this host yet`, and what the
  first converge will create for it. Nothing of a service can be described before its user
  exists: the uid is the name of its unit directory. Every later pre-flight — a pool option,
  an image tag, anything pushed at a converged host — shows the units, the drop-ins and the
  route files in full.

**If it aborts in `storage/tasks/mail.yml` with a sops error**, that is the one thing this
run is a real test of: the host cannot decrypt what the push carries. Either
`/etc/storagebaby/age.key` is missing (bootstrap writes it) or this host's age recipient is
not in `.sops.yaml` yet — "New host" below has that half. It surfaces in the storage role
rather than in the first service, because storage runs first.

Then the outage itself:

```sh
# 1. every service with a pool-class volume (all of them but yuzukam; traefik's
#    letsencrypt volume is `fast`, and it restarts by itself for the entrypoints)
for s in jellyfin kopia stirling-pdf paperless openarchiver immich nextcloud; do
	doas make stop SERVICE=$s
done
# 2. the other holder of /pool
doas systemctl stop smb nmb
# 3. ask the kernel who still has it open -- this is the EBUSY, before it happens.
#    A login shell sitting in /pool counts, and so does anything not in the list above.
doas fuser -vm /pool
# 4. converge for real, reading the same diff again
doas ansible-playbook -i ansible/inventory/hosts.yml --limit "$(uname -n)" \
	--diff ansible/playbook.yml
# 5. back up
for s in jellyfin kopia stirling-pdf paperless openarchiver immich nextcloud; do
	doas make start SERVICE=$s
done
doas systemctl start storagebaby-deploy.timer
```

Three things about that run, none of them a problem:

- **Traefik restarts once**, because the TCP entrypoints are static configuration on its
  unit's `Exec=` line. Every route is down for that restart and nothing else changes.
- **Packages move.** The role adds the Chaotic-AUR repository (`chaotic-keyring`,
  `chaotic-mirrorlist`, one `pacman -Sy` database refresh — never a `-Syu`, never a
  reboot), and if the host's `snapraid` is not the pinned `14.9-1` it builds that version
  from the AUR and `pacman -U`s it over what is installed. Running `pacman -Q` over the
  three packages beforehand says whether anything will move. The AUR build also installs
  `base-devel` and leaves it installed.
- **The old timer is already replaced, not disabled.** `storage-maintenance.timer` and
  `.service` keep their names; the role overwrote both, so there is no second run to
  disable. Step 10 is what removes the half that is now orphaned.

### 10. After that converge, retire the hand-stowed half and Samba

**There is probably nothing to unstow — check rather than assume.** The five unit paths and
`/etc/snapraid.conf` were `stow` symlinks into the old checkout; Ansible's `template` does
not follow a symlink at its destination, it replaces it with a regular file, but only on a
converge where the content actually differs. All six do differ, so step 9 turned all six
into rendered files — confirm it, because a link that survived would dangle the moment the
checkout is deleted:

```sh
find /etc/systemd/system -maxdepth 1 -type l -name '*.mount' -o -maxdepth 1 -type l -name 'storage-maintenance.*'
ls -l /etc/snapraid.conf
```

Neither may point into the old checkout. Do not run `stow -D` either way; what is left over
is the checkout itself and the wrapper beside it.

Verify first that the unit really is the new one:

```sh
systemctl cat storage-maintenance.service | grep ExecStart
# ExecStart=/opt/storagebaby/maintenance/storage-maintenance-unattended.sh
```

Once it says that, the old wrapper and its checkout are dead weight and can go:

```sh
doas rm -rf /opt/scripts
```

and with them the old `~/StorageBaby` working copy the wrapper pointed at, once nothing
else is being read out of it (`make pull` / `make push` are the only things that ever
wrote to it).

Samba is retired by hand, because the role does not remove a package it never installed:

```sh
doas systemctl disable --now smb nmb
doas pacman -Rns samba
```

The share trees — `/pool/shared/utility`, `/pool/shared/maki`, `/pool/mk`,
`/pool/jlk/backups`, `/pool/shared/scans` — stay exactly where they are, untouched data
with nothing serving them.

### 11. Open the media tree to Jellyfin

This one is deliberately after the first converge: the `media` group does not
exist on the host until the `service` role creates it, so a `chgrp` run before it
has nothing to chgrp to. Expect jellyfin to come up with an empty library until
these run:

```bash
doas chgrp -R media /pool/shared/media
doas chmod -R o+rX /pool/shared/media
```

The role creates a bind directory only when it is missing and never touches
the permissions of one that exists, so the tree stays the operator's — which
is also why this is not one-shot: anything dropped into it later by another
writer inherits whatever that writer gives it.

The `o+rX` is what Jellyfin actually reads through: the linuxserver image drops
its supplementary groups when s6 switches to its own user, so group access never
reaches the app process — `hosts/storagebaby/services/jellyfin/README.md` has the
measurement. The `chgrp` still matters anyway: it is what the role itself would set
on a tree it creates, and what anything else on the host that has to reach the
library goes through.

`/pool/shared/scans` needs nothing any more. It was the Samba drop the retired
`paperless-upload` watched; the scanner now logs in by FTP and writes into
paperless's own `consume` volume, so the tree is data nothing serves and no group
on the host has to reach it. With Samba gone (step 10), the same is true of every
other share tree.

### 12. After the first converge, check the storage, the certificate and the backups

These are the things that fail **silently** — the host looks converged, every unit is
active, and the problem does not show up until it is needed.

**Is everything mounted, and is the array running?**

```sh
findmnt /mnt/data/data1 /mnt/data/data2 /mnt/data/data3 /mnt/parity/parity1
findmnt /pool -o SOURCE,FSTYPE,OPTIONS # fuse.mergerfs, the declared option string
systemctl is-enabled storage-maintenance.timer && systemctl list-timers storage-maintenance.timer
```

Then run the nightly job once, by hand, rather than waiting for 02:00 — it is the only
thing that proves the array, the balance, the scrub **and** the mail at the same time.
It takes as long as a sync takes, and it stops jellyfin for the duration:

```sh
doas systemctl start storage-maintenance.service # blocking: it is a oneshot
journalctl -u storage-maintenance.service -b
```

A mail has to arrive, subject `[SUCCESS] SnapRAID Sync Report`. If none does, the run
itself was still fine — `journalctl -t msmtp` is where a refused relay or a rejected
sender says why, and `storage.mail` is what to correct. Check afterwards that
`snapraid.content` exists on all three data disks and `snapraid.parity` on the parity
disk, and that jellyfin came back up (`make ps SERVICE=jellyfin`).

**Does a scan reach Paperless?** Send one page from the printer. It should appear as a
document within a minute or two — the consumer polls rather than watching inotify,
because an FTP write is not inotify-friendly — and the consume volume should be empty
again afterwards, because the consumer deletes what it has ingested. If the transfer
hangs after the login instead, re-read step 8: that is `tcp_bind_address`.

**Is the wildcard certificate issued?** Traefik's ACME run is router-driven and
happens after the converge has finished, so nothing in the play reports on it. Every
backup sidecar validates Traefik's certificate out of the kopia image's CA bundle, so
until this is right, no pod can back anything up:

```sh
echo | openssl s_client -connect 127.0.0.1:443 -servername kopia.home.klees.io 2> /dev/null \
	| openssl x509 -noout -issuer -dates
```

The issuer has to be Let's Encrypt, not `CN=TRAEFIK DEFAULT CERT`. If it is the
default, read `make logs SERVICE=traefik` for the Porkbun DNS challenge.

**Did each sidecar connect, and did a snapshot land?** Per pod
(`paperless`, `openarchiver`, `immich`, `nextcloud`):

```sh
doas /usr/local/sbin/podman-as svc-paperless podman exec paperless-backup kopia repository status
```

The same for `openarchiver`, `immich` and `nextcloud`: the user is `svc-<name>`, the
container `<name>-backup`.

It has to succeed. A sidecar that cannot connect stays in a 15 s retry loop while its
pod is up and healthy — nothing else reports it, and **no backup is ever taken**. Then,
after the first 03:00, the server has to know the snapshots:

```sh
doas /usr/local/sbin/podman-as svc-kopia podman exec kopia kopia snapshot list --all
```

One `<name>@storagebaby:/data/<volume>` line per volume in that service's
`backup.paths`. Nothing before that first schedule proves anything: connecting and
snapshotting are two different things.

Two more things are not steps but expectations about that first converge:

- **Existing volume directories keep their owner and mode.** A converge creates
  the ones that are missing and leaves the rest alone, so anything already on the
  pool stays exactly as it is.
- **The pods converged before kopia log failed connects, and heal themselves.**
  Services converge in sorted order, so immich's sidecar starts while
  `kopia.<domain>` has no route yet: it retries every 15 s and, after its start
  period, is killed and restarted by `HealthOnFailure=kill`. It connects on its own
  once kopia is up — noise in the first converge's journal, not a step.
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
`yuzukam` declares no volumes at all — it is stateless. Paperless's `consume` is
new and starts empty by definition: it is the FTP spool the consumer drains, and a
file in it has not been ingested yet.

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
