# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

The **host repository** of StorageBaby, a home NAS/media server running Arch Linux: what the machine is made of (disks, parity, the mergerfs pool) and which services run on it, as rootless Podman containers. The engine that turns this into a running host is **Cradle** — `github.com/janlucaklees/cradle`, checked out at `/home/jlk/Projects/cradle` on the workstation. Cradle holds the playbook, the roles, the tests and the documentation of the contract this repository's folders answer to: `ansible/roles/service/README.md` is the full reference, `docs/ownership.md` is who decides what, `docs/architecture.md` the map, `docs/quickstart.md` and `docs/external-service.md` the two how-tos, and the `adding-a-service` skill lives there too. A host is set up once with Cradle's `bootstrap.sh` and converges itself from Cradle plus this repository after that; nothing is changed on a host by hand. Design: `docs/superpowers/specs/2026-09-21-gitops-podman-platform-design.md` — §2, §4 and §9 of it are the pre-Phase-4 design and say so; the storage half is `docs/superpowers/specs/2026-09-25-phase-4-storage-and-ftp-design.md`.

Migrated: on storagebaby yuzukam, stirling-pdf, jellyfin and the four multi-container stacks as pods — paperless, openarchiver, immich and nextcloud. The migration is complete: **nothing on a host is hand-stowed any more**, and the repo root holds no service, unit or script directory at all — `samba/` and `snapraid/`, the last two, are retired. The mounts, the mergerfs pool, the snapraid array and the nightly maintenance run are declared in `host.yml` and executed by Cradle's `storage` role; Samba was dropped entirely rather than migrated, and its share trees stay on disk with nothing serving them. `paperless-upload` is retired too: the scanner logs in to the `paperless-ftp` part of the paperless pod and writes into its `consume` volume, so the API hop and the `/pool/shared/scans` share are gone. A new service goes under `hosts/`.

## Managing services

Placement is the folder:

- `hosts/<host>/host.yml` — everything host-specific (domain, ACME, certificate zones, storage roots, `storage`, `backup`, volume overrides, deploy timer, `gpu`, `packages`, `service_config`, and optionally `tcp_bind_address`, `registries` and `ingress`). `backup` is required and is either the string `none` or `{ repository, username, domain, replica }` — the whole of what this host says about the kopia built-in; `ingress: { domain }` renames the Traefik built-in's dashboard and defaults to `traefik`. `storage` declares the filesystems the host is made of — `disks`, `parity` and the mergerfs `pool` — and the `storage` role mounts exactly those, before `host_base`, and aborts the converge when a declared device is missing or a declared path is not a mount point, so an unmounted pool cannot be silently recreated as empty directories on the root filesystem by the nightly deploy timer. It never partitions, formats or wipes: Cradle's `ansible/roles/storage/README.md`. (It replaced the `mountpoints` list, which is gone.)
- `hosts/<host>/services/<name>/` — a service that runs on that host only.
- **There is no `hosts/shared/`** — a host repository that still has one is refused. The two services every host runs are **Cradle's built-ins**: Traefik (the ingress, placed on every host) and the kopia server (placed where `backup` is a block). Cradle ships both folders in `ansible/builtins/` and places them itself, so nothing here carries a folder for either; the host's side of them is `ingress`/`acme`/`cert_zones` and `backup` in `host.yml`, and their secrets are the host's own `secrets/ingress.sops.yaml` and `secrets/backup.sops.yaml`. Cradle's `ansible/builtins/README.md` is the statement of what a built-in is.
- `hosts/<host>/services/<name>/source.yml` — a pointer: the service's definition lives in its own repository (`repo`, `ref`, `path`, all required). The deploy script fetches it into `/var/lib/cradle/sources/<name>` and the playbook resolves the pointer to `<path>/service.yml` there. The folder holds nothing else but an optional README; the git token is the host's, `<name>: { token }` in `hosts/<host>/secrets/sources.sops.yaml`. Cradle's `docs/external-service.md` is the other side, and its `tests/fixtures/host-repo` and `tests/fixtures/sources/ext-echo` are the model host repository and the model pointed repository.
- `hosts/<host>/secrets/registries.sops.yaml` — the host's registry logins, `<registry>: { user, token }`, one per registry its `host.yml` offers under `registries`. A service names a registry it needs; the host offers it or the converge refuses.
- `hosts/<host>/secrets/<service>.sops.yaml` — optional per-host override of a service's secrets.
- `hosts/<host>/secrets/backup.sops.yaml` — the kopia built-in's four: `repository_password` (the one credential that opens the repository, with no reset and no escrow — back it up outside this repository), `server_password` (the web UI login for `backup.username`, freely rotatable), `b2_key_id` and `b2_application_key` (the replica push's Backblaze credentials). `hosts/<host>/secrets/ingress.sops.yaml` — the Traefik built-in's three: `porkbun_api_key`, `porkbun_secret_api_key`, `dashboard_htpasswd`.
- `hosts/<host>/secrets/<set>.sops.yaml` — a set of values two services on that host have to agree on, referenced from both sides as `host_secrets`. No service declares one, and the kopia client passwords, which were its one use, are **generated on the host** since stage 2 of the split: one `openssl rand -base64 32` per backed-up service under `/var/lib/cradle/backup-clients/<service>`, created on the first converge that places it and never rewritten, synced into two podman secrets — the consumer's `kopia_password` and the server's `client_<service>`. Neither side declares the name, and a service that lists `kopia_password` in its own `secrets` is refused. `mail.sops.yaml` in the same directory is not a set but the storage role's relay password.

**A service folder is a plugin.** The platform is the receiver: it offers everything a plugin needs in order to run, and the plugin ships every fact about itself — so placing one never requires touching the receiver. The question to ask of any change is therefore not "is this platform or service?" but **"would adding the next service have to edit this?"** If yes, it is in the wrong place. That applies to Cradle's test suite too, not just its roles: `probe_path` (below) exists because the alternative was a list of per-service exceptions inside the integration verifier. Cradle's `docs/ownership.md` § 1.1 is the full statement, and its § 4 draws the other half of the line — a service may not ship a fact that belongs to the _host_, because that is someone else's decision.

**No test in this repository checks a placement.** Cradle's static suite renders Cradle's own fixture host repository and never this one, and its integration scenario converges a fixture host in a VM. A mistake here therefore surfaces at converge on the real machine — loudly, and again five minutes later — or beforehand in `mise run render` inside Cradle, which renders this host repository against that engine. Where this file or a service README says "a static test refuses X", read it as the rule it names: the test is Cradle's and does not see this repository.

A service folder is a contract, not a script:

```
service.yml        name, port (loopback) + domain or routes, tcp_ports, volumes, binds, devices,
                   groups, config, secrets, host_secrets, hooks, backup policy
quadlet/*.j2       Podman Quadlet units (.pod, .container, .build), rendered per host
                   (vars: volumes.<name>, binds, config_dir, port, routes, fqdn, hostname, tz)
quadlet/*.timer.j2 plain systemd user units, with their *.service.j2 half — not Quadlet
config/            copied to /etc/cradle/<name>/, read-only for the service user
secrets.sops.yaml  sops+age encrypted key/value pairs
```

`service.yml` beyond the basics — Cradle's `ansible/roles/service/README.md` is the full reference for what a template may use:

- **`volumes`** — `<name>: { class: pool | fast, owner: <uid> }`, resolved against the host's `storage_roots` (or a `volume_overrides` entry — storagebaby's is empty: its one entry pinned the kopia repository and is `backup.repository` now, which the playbook applies as that volume's override and which the static suite refuses as a `volume_overrides` key) to a directory the role creates **only when it is absent**, 0750 and owned by what `owner` names. `owner` is the uid the process has _inside_ the container, which the role maps onto `subuid_start + owner - 1` here; container uid 0 is the service user itself, which is also what leaving `owner` out means. An existing directory's mode is never re-permissioned: images chown their data tree on every start, so enforcing a mode would report changed forever and take the running service's access away. Its _owner_ is adopted, once — one `chown -R`, and only when `owner` is declared and the directory's own owner differs from it. That is what replaced the operator's hand chown when data is migrated in.
- **`binds`** — `<name>: { host, container, mode, group, owner }`: a pre-existing host tree mounted into the container. The role guarantees the group exists and that `svc-<name>` is in it, and creates the host directory as `<owner or root>:<group> 2775` if missing — an existing one's mode is again never re-permissioned, and its owner only when `owner` is declared, by uid alone so the group that carries everyone else's access stays.
- **`devices`** — a list of device paths, rendered as `AddDevice=` **only** on a host whose `host.yml` says `gpu: true` (the template reads `devices_enabled`). Cradle's fixture hosts have no GPU and get no device line.
- **`groups`** — extra host supplementary groups for the service user. Any `groups` or bind group sets `keep_groups`, which is what a template turns into `GroupAdd=keep-groups`. It carries the groups into the container's credentials, and it does **not** survive an image that switches user through s6 — see `hosts/StorageBaby/services/jellyfin/README.md`.
- **`config`** — free-form, readable in templates as `service.config.*`, and overridable per host through `service_config: { <service>: { ... } }` in `host.yml` (host wins, deep-merged in the playbook). Storagebaby's is empty — no service here overrides a value of its own folder — and a built-in has no `service_config` entry at all: the kopia server's replica is `backup.replica` in `host.yml` (`none` on a host without a Backblaze account), and `service_config.kopia` is refused rather than silently overridden.
- **`port`** is optional, and required only when there is a `domain`. Cradle's `build-echo` fixture has neither.
- **`routes`** — `[{domain, port}, ...]` when one service answers on several names (nextcloud: `nextcloud.klees.io` and `collabora.klees.io`); `domain:` + `port:` is the one-route shorthand. A `domain` is **either** a bare label the host's own `domain` completes (`jellyfin` → `jellyfin.home.klees.io`) **or** a full hostname used as it stands (`immich.klees.io`) — the dot tells them apart, because a DNS label cannot contain one. The host has to declare the zone in `cert_zones` or the name gets no certificate, and Cradle's `tests/static/test_hosts.py` refuses the combination in its own fixtures. One Traefik file and one router per entry, `<name>-<domain>`, and every `<name>-*.yml` a converge did not write is removed, so a renamed route stops answering instead of leaving a live router behind. `route:` is a block of backend options for all of them, and a `routes[]` entry may override it: `scheme: https` + `insecure_skip_verify: true` is how Traefik reaches a backend that keeps its own TLS (kopia, because gRPC needs HTTP/2; collabora, because its distroless image has no plain-HTTP probe). `basic_auth: <secret>` puts a `basicAuth` middleware in front of the router, `usersFile` being that podman secret of **traefik** (it opens the file; a rootless user cannot see another user's secrets) — traefik's own dashboard, `dashboard_htpasswd`, is the one consumer. `probe_path:` (default `/`) is the URL the integration verifier requests to prove the route arrives; it is consumed by neither Traefik nor the role, and belongs to the service because `/` is a legitimate 404 on a backend whose root is not a route — the `mcp` hub registers `/:name` and nothing above it, so it declares `/livez`. A 404 is also exactly what Traefik answers when it matches no router, and the two cannot be told apart from a status code.
- **`tcp_ports`** — `[{port: 21, target: 2121}, {range: [21100, 21109]}]`: plain-TCP ports Traefik listens on and forwards to `127.0.0.1:<target>` (a `range` forwards each port to itself, which is what an FTP passive range needs). Paperless is the only service that declares any. Plain TCP carries no hostname, so a port belongs to exactly one service per host — route ports, entrypoints and targets are one namespace, which Cradle's `tests/static/test_ports.py` checks for its fixtures — and the pod must publish every target on loopback. See "Networking" below.
- **`registries`** — `[ghcr.io]`: the registries the service's images are private on. The service user is logged into each before any pull, from the host's `registries.sops.yaml`; a registry the host's `registries` list does not offer is refused on the host by the role.
- **`host_secrets`** — `<podman secret name>: <set>.<key>`, resolved against `hosts/<host>/secrets/<set>.sops.yaml`, for one string two services must agree on. Supported and tested, declared by nobody right now: the kopia client passwords were its one use and are generated on the host since stage 2 of the split — a service folder carries no backup password at all, and declaring `kopia_password` is refused because a name cannot have two sources.
- **`hooks.after_change`** — `podman exec` commands the role runs, in order, after it restarted the service's units, each waiting for its container's health check first (60 × 10 s). `when: unit_changed` (the default) makes a hook a deploy step and not a nightly one — a version bump runs nextcloud's five `occ` commands, the nightly no-op does not.
- **`backup`** — consumed by the role, not by a template: it generates `<name>-backup.container` into the service's pod, mounts each named volume read-only at `/data/<volume>`, connects to `https://<backup.domain>.<host domain>` as `<name>@<host>` with the generated password, and keeps a scheduler running. Hence `backup` requires a `<name>.pod.j2`. A database is dumped into a `backups` volume by a timer, never snapshotted as a data directory — all four of paperless, openarchiver, nextcloud and immich have a `<name>-dump.timer` at 02:30, dumping the same way, and **no service's backup depends on the application being configured for it**. The dump is plain SQL and uncompressed (`pg_dump --clean --if-exists --no-owner`) because the sidecar snapshots it into a deduplicating repository and a compressed dump dedups against nothing; restore with `psql`, not `pg_restore`. Every `backups` volume is class `pool` — a write-once nightly file has no business on a 120G SSD. `exclude: { <volume>: [rule] }` is the one optional key: kopia ignore rules relative to that volume's snapshot root, for an application that writes **its own** backups into a tree this platform already snapshots. Immich is the only user (`upload: ['/backups/']`, its admin-UI backup job). It is not a knob for regenerable churn — previews and search indexes are still carried. Cradle's `ansible/roles/service/README.md`, "`backup.exclude`" and "The dump form".

There are no test hosts in this repository. Cradle carries the fixture host repository its own suites render and converge — two hosts, the services they place, and the three fixtures that measure the `tcp_ports` path, the host-build path and the pointer path — so a placement here is only ever this machine's. Sharing one service folder between several hosts of one host repository is still a symlink and never a copy; this repository describes one host and has none.

A multi-container service is one Podman pod. The conventions a template must meet, all of them enforced by Cradle's `tests/static/` against its fixtures:

- exactly one `<name>.pod.j2`, and every `*.container.j2` of that service carries `Pod=<name>.pod` and `ContainerName=<name>-<part>` (`-app`, `-database`, `-cache`, `-backup`, …);
- every `PublishPort=` lives on the `.pod`, publishes a route port and starts `127.0.0.1:` — inside the pod the parts reach each other on `127.0.0.1:<upstream port>`;
- no template writes `AddHost=<fqdn>:host-gateway`. The role does, into a Quadlet drop-in, for every route name placed on the host: rootless, pasta gives the container the host's own address, so a service reaching another one through Traefik needs the gateway address instead. Cradle's `ansible/roles/service/README.md`, "Reaching another service through Traefik".

Cradle's generic `service` role (`ansible/roles/service/` there) turns all of that into a running service: system user `svc-<name>` with subids and linger, volume and bind directories, group memberships, `podman secret`s synced from sops (own and host sets), quadlets and timer units rendered, the host-gateway drop-ins, the generated backup sidecar, one Traefik route file per route, then restarts only what changed and runs the after-change hooks. Restarting a pod is the only restart its containers need, so they are dropped from the restart list when it is in it.

Work on this repository through `mise.toml` — it is the single entry point, there is no Makefile, and it carries formatting and secrets and nothing else: everything that converges, renders or tests a host is a task in Cradle. mise itself comes from the distribution (pacman: `mise`), and the two secret tasks use the distribution's own `sops` and `age` because the key that opens the files lives on this machine.

```bash
mise tasks         # every task, with its arguments
mise run devtools  # build the prettier image (once)
mise run format    # prettier over the whole repo
mise run fmt-check # check only, no writes
mise run sops hosts/StorageBaby/secrets/ingress.sops.yaml
mise run updatekeys # re-encrypt every secret file for the recipients .sops.yaml names
```

Arguments are positional, not `NAME=value`: a `make X=Y` override of a variable is `X=Y mise run ...` instead. A task that takes an argument documents it in its `description`, which `mise tasks` prints.

On a host, service units belong to the service user's systemd manager (run as root):

```bash
cradle-svc ps traefik      # systemctl --user -M svc-traefik@ status traefik.service
cradle-svc restart traefik # also: start, stop
cradle-svc logs traefik    # journalctl _SYSTEMD_USER_UNIT=traefik.service -f
```

It is pod-aware: the service name resolves to `<name>-pod.service` when the service
user's manager knows that unit and to `<name>.service` otherwise, so
`cradle-svc logs nextcloud` follows the pod. A single container of a pod is addressed
by its own unit name (`nextcloud-collabora.service`).

`cradle-svc` is a program on the host, installed to `/usr/local/sbin` by the
`host_base` role of Cradle (`ansible/roles/host_base/files/cradle-svc` there) — not a mise task. mise
is a development tool and is not installed on a host, and a host is not a checkout anybody
runs tasks from. The raw forms it wraps:

```bash
systemctl --user -M svc-traefik@ status traefik.service
systemctl --user -M svc-traefik@ restart traefik.service
journalctl _SYSTEMD_USER_UNIT=traefik.service -f # journalctl has no --user -M form
```

## Formatting

Prettier (+ `prettier-plugin-sh` for shell scripts) runs inside a small Docker image built from `devtools/` — nothing formatter-related is installed on the host besides `lefthook` itself.

```bash
mise run devtools      # build/rebuild that image
mise run format        # reformat the whole repo
mise run fmt-check     # check only, no writes
mise run install-hooks # one-time per clone: wires lefthook's pre-commit hook
```

Once hooks are installed, staged files are auto-formatted and re-staged on every commit (`lefthook.yml`, `stage_fixed: true`). Config: `.prettierrc` / `.prettierignore` at repo root.

## Storage architecture

Declared in `hosts/<host>/host.yml` under `storage`, realised by Cradle's `ansible/roles/storage` — which runs **before `host_base`** and is skipped on a host that declares no `storage`. That role's README there is the reference; this is the shape.

```
Physical disks                     one rendered .mount unit per entry, What= a stable
  /mnt/data/data1, data2, data3    /dev/disk/by-partuuid (parity: by-id) path
  /mnt/parity/parity1

mergerfs pool
  /pool                            union over the data disks only (parity is not a branch)
  /pool/apps/<service>/<volume>    pool-class service volumes (storage_roots.pool) —
                                    live app state, EXCLUDED from the array
  /pool/backups/devices/StorageBaby/kopia   the kopia repository (backup.repository) —
                                    outside /apps, so in the array
  /pool/shared/media               the media library Jellyfin reads
  /pool/shared/scans, /pool/jlk/backups, …   former Samba trees, now data nothing serves

Snapraid                           /etc/snapraid.conf, rendered from the same block
  parity file on /mnt/parity/parity1/snapraid.parity
  content files on each data disk + /etc/snapraid.content
```

- **`/pool/apps` is out of the array**, as of the 2026-10-14 design's stage 2 (committed 2026-10-15). Snapraid cannot parity a file rewritten while it reads it — that was the nightly `exit:warning` on four soft errors — so live application state is excluded and the kopia repository, on the pool and outside `/apps`, is what protects that tree instead. The cost: a dead disk means restoring application state from the last snapshot rather than repairing it in place, and anything under `/apps` with no `backup:` block (jellyfin's 32 GB of metadata above all) is protected by nothing. `hosts/StorageBaby/host.yml`'s `excludes` comment carries the reasoning; the cutover runbook's "Stage 2" section is the record of how it got there.
- **The role never partitions, formats or wipes.** No `mkfs`, `parted`, `wipefs`, `sgdisk`, ever. A declared device that is not there fails the converge naming the entry, and after mounting it asserts every declared path really is a mount point — the pair that replaced `host_base`'s `mountpoints` list, and what keeps an unmounted `/pool` from being recreated as empty directories on the root filesystem by the nightly deploy timer.
- **The create policy is `pfrd`** (proportional free random distribution: a branch chosen at random, weighted by free space) — **not** `eplfs`, which this file claimed for a long time and the deployed `pool.mount` never said. The option string is `storage.pool.options` in `host.yml` and is copied into the unit verbatim; that file is the source of truth for it.
- **A changed `pool.options`, branch or `fstype` remounts the union**, and every container holding a bind mount under it comes back reading an empty directory until it is restarted. The role warns before it does so; `--check --diff` first, then stop the pool-class services.
- **Packages:** mergerfs from Chaotic-AUR, snapraid and `mergerfs-tools-git` built from the AUR by the role, `state: present` throughout. The two pins differ in kind: snapraid declares `pkgver-pkgrel` and the guard is a `vercmp` against the installed version (older rebuilds, newer stays and is reported), while `mergerfs-tools-git` is a VCS recipe that versions itself, so its pin is the AUR commit held in `/var/lib/aurbuild/stamps/<package>`. The role never runs `pacman -Syu` and never reboots; a first converge does **two** AUR builds, not one.

## Storage maintenance pipeline

`storage-maintenance.timer` (`OnCalendar` from `storage.snapraid.maintenance.on_calendar`, 02:00, `Persistent=true`) runs `storage-maintenance.service`, a oneshot that `Requires=` the pool and execs `/opt/cradle/maintenance/storage-maintenance-unattended.sh`. The role installs that whole directory as root: six scripts at 0755 — the wrapper, the orchestrator, `sync.sh`, `scrub.sh`, `report_failed_units.sh` and `storage-maintenance-failed.sh`, the notifier `storage-maintenance-failed.service` execs — plus the `plugins/` tree and `maintenance.env`, which is 0644 because it carries no secret by construction.

The wrapper logs to `/var/log/storage-maintenance/` and mails the log with `mutt`, which is told to send through `msmtp` explicitly — the host runs no MTA. `/etc/msmtprc` (0600 root) is rendered from `storage.mail` plus `smtp_password` out of `hosts/<host>/secrets/mail.sops.yaml`; msmtp logs to the journal, so `journalctl -t msmtp` is where a refused relay shows up. If the pool does not come up, the unit's start job fails before the wrapper runs at all, which is what `storage-maintenance-failed.service` on `OnFailure=` exists to mail.

The orchestrator runs these steps in order:

1. `on-start` hook
2. Snapraid status print
3. `on-before-sync` hook → `sync.sh` (snapraid touch + sync)
4. `on-after-sync` hook
5. `scrub.sh` (new blocks, then `scrub_percent` of anything older than `scrub_older_days`)
6. `on-after-scrub` hook
7. Final status + SMART report
8. `report_failed_units.sh` (every `svc-*` service manager's failed units, for the mail)
9. `on-finish` hook

**There is no balance step and no `on-before-balance` hook** — both were removed on 2026-10-15. The pool's create policy is `category.create=pfrd`, which already spreads new files over the branches by free space, so the packing the nightly `mergerfs.balance` existed to undo does not happen. The tool is still installed and is the operator's to run by hand.

If any step or hook fails, the orchestrator runs the `on-failure` hook before aborting; that one is best-effort, so one broken recovery script does not stop the others. It also fires on SIGINT/SIGTERM (a systemd stop or a timeout mid-run), so a service stopped by `on-before-sync` is never left down.

**The hook contract is still `plugins/<name>/<hook>.sh`, but nothing hand-writes one any more.** `storage.snapraid.maintenance.stop_services` is the list, **empty on storagebaby** since /apps left the array — the run touches nothing a service writes, so nothing is stopped for it, and only Cradle's test hosts keep the mechanism exercised; the role creates one directory per name and drops the same three scripts in — `on-before-sync.sh` (stop), `on-after-scrub.sh` and `on-failure.sh` (start) — which read the service name off their own directory, so they are byte-identical on every host, and resolve `<name>-pod.service` against `<name>.service` the way `cradle-svc` does. A directory for a name no longer in the list is removed. The old `samba` and `snapshot-*` plugins are gone with the tree that held them: Samba is retired and Kopia is the only backup path.

**No parameter is rendered into a script.** They all come from `/opt/cradle/maintenance/maintenance.env`, which every script sources, so a threshold changes in git and a hand-run script behaves exactly like the timer's. To run maintenance manually, on the host:

```bash
doas systemctl start storage-maintenance.service    # the real nightly run, blocking
bash /opt/cradle/maintenance/storage-maintenance.sh # the same, without wrapper or mail
bash /opt/cradle/maintenance/sync.sh                # sync only
bash /opt/cradle/maintenance/scrub.sh               # scrub only
mergerfs.balance -p 5 /pool                         # by hand; never nightly
```

## Networking

Traefik runs rootless as `svc-traefik` with `Network=host` and is the only process on a routable port — 80 and 443 always, plus any declared TCP port (the unprivileged-port sysctl is lowered to the lowest of them: 80 normally, 21 on storagebaby). Every other service binds `127.0.0.1:<port>` — the port declared in its `service.yml`, and unique per host.

Traefik's only provider is the file provider: the `service` role renders one route file per route into `/etc/cradle/traefik/dynamic.d/<name>-<domain>.yml` — one per `routes:` entry, so a two-route service gets two — pointing at `http://127.0.0.1:<port>`. No labels, no Docker socket, no shared container network — rootless containers of different users cannot reach each other's loopback, so cross-service traffic goes through Traefik and the public FQDN.

Resolving that FQDN from inside a container does not help: DNS answers with the host's own address, which pasta has copied onto the container's interface, so the connection terminates in the container, where nothing listens on 443. The role therefore renders `AddHost=<fqdn>:host-gateway` for **every** route name placed on the host into a Quadlet drop-in — `<name>.pod.d/` for a pod service, `<stem>.container.d/` otherwise — and no service template of this repository writes one. Details in Cradle's `ansible/roles/service/README.md`, "Reaching another service through Traefik".

Plain TCP goes through the same door: a service declares `tcp_ports: [{port: 21, target: 2121}, {range: [21100, 21109]}]`, the playbook collects every placed port into `placed_tcp_ports`, traefik's unit renders one static entrypoint `tcp-<port>` on `<tcp_bind_address>:<port>` per port (so placing one restarts Traefik once), and the `service` role renders one TCP router and service per port into `dynamic.d/<name>-tcp.yml`, forwarding to `127.0.0.1:<target>`. The entrypoint binds the host's own address, not the wildcard: `target` equals `port` wherever the protocol advertises the port it listens on (an FTP passive range), and a wildcard listener cannot coexist with the service's `127.0.0.1:<port>` publish. `tcp_bind_address` defaults to `ansible_default_ipv4.address` and is a top-level `host.yml` key. Plain TCP carries no hostname, so a port belongs to exactly one service per host — route ports, entrypoints and targets are one namespace that Cradle checks for its fixtures — and a port below 80 pulls the unprivileged-port sysctl down with it. Details in Cradle's `ansible/roles/service/README.md`, "TCP ports".

TLS: with `acme: true` in `host.yml`, Traefik itself issues one wildcard cert per entry of `cert_zones` (default `[domain]`; storagebaby declares `home.klees.io` and `klees.io`) via the Porkbun DNS challenge, state in the `letsencrypt` volume. Only the dashboard's router carries the resolver and the domains, which is what triggers issuance; every other router inherits what it obtained. `cert_zones` is also the allowlist — a route under a zone the host does not declare gets a certificate that does not cover it (Traefik's own default certificate on this host), so the browser errors; nothing refuses it. `acme: false` (Cradle's fixture hosts) serves Traefik's default certificate. Details in Cradle's `ansible/builtins/traefik/README.md`.

Updates are Podman's: floating tag plus `AutoUpdate=registry` and the user's `podman-auto-update.timer`, or a pinned tag bumped in git.

## Adding a new disk

Cradle's `ansible/roles/storage/README.md`, "Adding a disk", has the procedure: partition and `mkfs.ext4` the disk by hand on the host (the role refuses to do either), read its `PARTUUID` off `lsblk`, add the entry to `storage.disks` in `hosts/<host>/host.yml`, push. The role mounts it, folds it into the pool and into `snapraid.conf`, and the next nightly `snapraid sync` writes its content file. Adding a branch changes `pool.mount`, so that converge remounts the pool — do it with the pool-class services stopped.

## Deployment

Bootstrap is Cradle's, run once as root on a fresh host — where the script sits does not matter, nothing reads it afterwards:

```bash
doas bash bootstrap.sh \
	--cradle-repo https://github.com/janlucaklees/cradle \
	--host-repo git@github.com:janlucaklees/StorageBaby.git \
	--host-path hosts/StorageBaby
```

`--cradle-ref` and `--host-ref` default to `stable` and are how a rollout is tested; `--local` installs everything but leaves the deploy timer disabled, `--no-upgrade` skips its one `pacman -Syu` — the only full upgrade either repository ever runs, because **keeping a host's packages current is the operator's job** and no role upgrades or reboots one. It generates three keys — `deploy_key` for this repository, `cradle_key` for Cradle, `age.key` for the secrets — prints all three public halves, writes `/etc/cradle/deploy.conf` (`CRADLE_REPO`, `CRADLE_REF`, `HOST_REPO`, `HOST_REF`, `HOST_PATH`, and an `EXTRA_ARGS` left empty), then clones Cradle into `/var/lib/cradle/cradle` and installs `cradle-deploy`, its service and its timer **from that checkout** rather than from a second copy embedded in the script. The operator registers the two deploy keys as read-only deploy keys on the two repositories, adds the age recipient to `.sops.yaml` under that host's own rule — once, and there is no shared rule any more — runs `mise run updatekeys` (`mise run sops ...` edits one file), commits and pushes. Until that recipient is in `.sops.yaml` the first converge fails at secret decryption, in the `storage` role's mail task, because `storage` runs first and `mail.sops.yaml` is the first secret the play reads.

From then on the host deploys itself. The timer runs `cradle-deploy` 2 minutes after boot and every 5 minutes after that, and one tick fetches three kinds of checkout: Cradle at `CRADLE_REF` into `/var/lib/cradle/cradle`, this repository at `HOST_REF` into `/var/lib/cradle/host` (`HOST_PATH` names the folder inside it, `hosts/StorageBaby`), and every pointed service repository at the ref its `source.yml` names into `/var/lib/cradle/sources/<name>`. It converges only when one of the three moved, comparing the three SHAs against `/var/lib/cradle/deploy.state` — the state of the last converge that **succeeded**, so a failed converge retries on the next tick instead of waiting for the next commit. `doas cradle-deploy --check` is the pre-flight: fetch everything, `--check --diff`, write no state.

`stable` is moved by CI, never by hand, on both sides: this repository's `.github/workflows/ci.yml` checks formatting and fast-forwards `stable` on a green master push, and Cradle's runs its own static and Molecule suites before moving its own. **The two can race** — a commit here that uses a contract key Cradle's `stable` does not have yet promotes on formatting alone, errors at converge, and retries every five minutes until Cradle's `stable` catches up.

Secrets are sops+age at rest and `podman secret`s at runtime. A host can only decrypt what it runs, and `hosts/<h>/**` is the whole of it: encrypted to the operator's key plus that host's key. The built-ins' secrets are under the same tree, so there is nothing a host holds a key to that it does not run. CI never holds a key.

## Start here

- Cradle's `docs/architecture.md` — the shape of the engine: the deploy loop, the layers and their measured size, what the `service` role does with a folder end to end, where the complexity sits. Read it before the sections above; they are the reference, it is the map.
- Cradle's `docs/ownership.md` — who decides what, between the engine, a service and the administrator. **The target state, not the current one**, and its § 7 is the list of places the engine disagrees with it. Read it before proposing any change to a `service.yml` key.
- Cradle's `ansible/roles/service/README.md` is the contract reference, its `ansible/roles/storage/README.md` the storage half, and its `adding-a-service` skill the thing to invoke when writing or reviewing a service folder.
- `README.md` here is the host: its services, its storage, its networking, the record of the first deploy, and a **"Known drift"** list of what is wrong or unprotected on this machine.
