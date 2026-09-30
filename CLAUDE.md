# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Git-driven configuration for a home NAS/media server ("StorageBaby") running Arch Linux. It covers the full stack: physical disk management, parity/redundancy, file sharing, and media services as rootless Podman containers. A host is set up once with `bootstrap.sh` and converges itself from this repository after that; nothing is changed on a host by hand. Design: `docs/superpowers/specs/2026-09-21-gitops-podman-platform-design.md`.

Migrated: traefik (shared), and on storagebaby yuzukam, stirling-pdf, jellyfin, kopia and the four multi-container stacks as pods — paperless, openarchiver, immich and nextcloud. The migration is complete: **nothing on a host is hand-stowed any more**, and the repo root holds no service, unit or script directory at all — `samba/` and `snapraid/`, the last two, are retired. The mounts, the mergerfs pool, the snapraid array and the nightly maintenance run are the `storage` role; Samba was dropped entirely rather than migrated, and its share trees stay on disk with nothing serving them. `paperless-upload` is retired too: the scanner logs in to the `paperless-ftp` part of the paperless pod and writes into its `consume` volume, so the API hop and the `/pool/shared/scans` share are gone. A new service goes under `hosts/`.

## Managing services

Placement is the folder:

- `hosts/<host>/host.yml` — everything host-specific (domain, ACME, storage roots, `storage`, volume overrides, deploy timer, `gpu`, `packages`, `service_config`, and optionally `tcp_bind_address`). `storage` declares the filesystems the host is made of — `disks`, `parity` and the mergerfs `pool` — and the `storage` role mounts exactly those, before `host_base`, and aborts the converge when a declared device is missing or a declared path is not a mount point, so an unmounted pool cannot be silently recreated as empty directories on the root filesystem by the nightly deploy timer. It never partitions, formats or wipes: `ansible/roles/storage/README.md`. (It replaced the `mountpoints` list, which is gone and is rejected by a test.)
- `hosts/<host>/services/<name>/` — a service that runs on that host only.
- `hosts/shared/services/<name>/` — a service that runs on every host (traefik).
- `hosts/<host>/secrets/<service>.sops.yaml` — optional per-host override of a service's secrets.
- `hosts/<host>/secrets/<set>.sops.yaml` — a set of values two services on that host have to agree on, referenced from both sides as `host_secrets`. `kopia-clients` is the one that exists: one password per backup client, read by the Kopia server and by that service's sidecar.

A service folder is a contract, not a script:

```
service.yml        name, port (loopback) + domain or routes, tcp_ports, volumes, binds, devices,
                   groups, config, secrets, host_secrets, hooks, backup policy
quadlet/*.j2       Podman Quadlet units (.pod, .container, .build), rendered per host
                   (vars: volumes.<name>, binds, config_dir, port, routes, fqdn, hostname, tz)
quadlet/*.timer.j2 plain systemd user units, with their *.service.j2 half — not Quadlet
config/            copied to /etc/storagebaby/<name>/, read-only for the service user
secrets.sops.yaml  sops+age encrypted key/value pairs
```

`service.yml` beyond the basics — `ansible/roles/service/README.md` is the full reference for what a template may use:

- **`volumes`** — `<name>: { class: pool | fast }`, resolved against the host's `storage_roots` (or a `volume_overrides` entry) to a directory the role creates **only when it is absent**, 0750 and service-owned. An existing one is never re-permissioned: images chown their data tree on every start, so enforcing a mode would report changed forever and take the running service's access away.
- **`binds`** — `<name>: { host, container, mode, group }`: a pre-existing host tree mounted into the container. The role guarantees the group exists and that `svc-<name>` is in it, and creates the host directory as `root:<group> 2775` if missing — again never re-permissioning an existing one.
- **`devices`** — a list of device paths, rendered as `AddDevice=` **only** on a host whose `host.yml` says `gpu: true` (the template reads `devices_enabled`). Test hosts have no GPU and get no device line.
- **`groups`** — extra host supplementary groups for the service user. Any `groups` or bind group sets `keep_groups`, which is what a template turns into `GroupAdd=keep-groups`. It carries the groups into the container's credentials, and it does **not** survive an image that switches user through s6 — see `hosts/storagebaby/services/jellyfin/README.md`.
- **`config`** — free-form, readable in templates as `service.config.*`, and overridable per host through `service_config: { <service>: { ... } }` in `host.yml` (host wins, deep-merged in the playbook). That is how the test host gives kopia a filesystem repository while storagebaby uses S3.
- **`port`** is optional, and required only when there is a `domain`. The `build-echo` fixture has neither.
- **`routes`** — `[{domain, port}, ...]` when one service answers on several names (nextcloud: `nextcloud` and `collabora`); `domain:` + `port:` is the one-route shorthand. One Traefik file and one router per entry, `<name>-<domain>`. `route:` is a block of backend options for all of them, and a `routes[]` entry may override it: `scheme: https` + `insecure_skip_verify: true` is how Traefik reaches a backend that keeps its own TLS (kopia, because gRPC needs HTTP/2; collabora, because its distroless image has no plain-HTTP probe).
- **`tcp_ports`** — `[{port: 21, target: 2121}, {range: [21100, 21109]}]`: plain-TCP ports Traefik listens on and forwards to `127.0.0.1:<target>` (a `range` forwards each port to itself, which is what an FTP passive range needs). Paperless is the only service that declares any. Plain TCP carries no hostname, so a port belongs to exactly one service per host — route ports, entrypoints and targets are one namespace, checked by `tests/static/test_ports.py` — and the pod must publish every target on loopback. See "Networking" below.
- **`host_secrets`** — `<podman secret name>: <set>.<key>`, resolved against `hosts/<host>/secrets/<set>.sops.yaml`. One string, two services: the kopia server declares `client_paperless: kopia-clients.paperless`, paperless declares `kopia_password: kopia-clients.paperless`.
- **`hooks.after_change`** — `podman exec` commands the role runs, in order, after it restarted the service's units, each waiting for its container's health check first (60 × 10 s). `when: unit_changed` (the default) makes a hook a deploy step and not a nightly one — a version bump runs nextcloud's five `occ` commands, the nightly no-op does not.
- **`backup`** — consumed by the role, not by a template: it generates `<name>-backup.container` into the service's pod, mounts each named volume read-only at `/data/<volume>`, connects to `https://kopia.<domain>` as `<name>@<host>` and keeps a scheduler running. Hence `backup` requires a `<name>.pod.j2`. A database is dumped into a `backups` volume by a timer, never snapshotted as a data directory.

Placing a storagebaby service on a test host is a symlink, never a copy — one folder, several hosts: `hosts/test-a/services/<name> -> ../../storagebaby/services/<name>`. The exceptions are the **test-only fixtures** — `tcp-echo`, which measures the `tcp_ports` path, and `build-echo`, which keeps the `.build` (host-built image) path covered. Neither has any business on the NAS, so neither has anything to link to: each lives as a real folder under both test hosts, and the copies must stay byte-identical (`test_a_service_placed_on_several_hosts_is_one_folder_or_identical_copies` holds them so). There are two test hosts: `test-a` places everything and runs on the workstation, `test-ci` a subset that fits a GitHub runner. `MOLECULE_HOST` picks which one the integration scenario converges (default `test-a`).

A multi-container service is one Podman pod. The conventions a template must meet, all of them enforced by `tests/static/`:

- exactly one `<name>.pod.j2`, and every `*.container.j2` of that service carries `Pod=<name>.pod` and `ContainerName=<name>-<part>` (`-app`, `-database`, `-cache`, `-backup`, …);
- every `PublishPort=` lives on the `.pod`, publishes a route port and starts `127.0.0.1:` — inside the pod the parts reach each other on `127.0.0.1:<upstream port>`;
- no template writes `AddHost=<fqdn>:host-gateway`. The role does, into a Quadlet drop-in, for every route name placed on the host: rootless, pasta gives the container the host's own address, so a service reaching another one through Traefik needs the gateway address instead. `ansible/roles/service/README.md`, "Reaching another service through Traefik".

The generic `service` role in `ansible/roles/service/` turns all of that into a running service: system user `svc-<name>` with subids and linger, volume and bind directories, group memberships, `podman secret`s synced from sops (own and host sets), quadlets and timer units rendered, the host-gateway drop-ins, the generated backup sidecar, one Traefik route file per route, then restarts only what changed and runs the after-change hooks. Restarting a pod is the only restart its containers need, so they are dropped from the restart list when it is in it.

Work on the repo through the Makefile — everything runs in the `devtools` image, nothing is installed on the workstation:

```bash
make devtools                               # build the tooling image (once)
make test-static                            # contract, secrets, render checks
make test-integration                       # Molecule scenario test-ci in a KVM VM (needs libvirt + KVM)
MOLECULE_HOST=test-ci make test-integration # the same scenario on the smaller CI placement
make molecule CMD=converge                  # a single Molecule step in that scenario
make molecule-login                         # SSH into the running test VM
make molecule-exec CMD='podman ps -a'       # one command on it, no TTY needed
make sops FILE=hosts/shared/services/traefik/secrets.sops.yaml
```

`make molecule-exec` is how to look at a running test VM from a script or without a terminal — `molecule login` needs a real TTY. It runs the command through Ansible against the inventory Molecule already wrote, so there is no second source of truth for the VM's address and key.

On a host, service units belong to the service user's systemd manager (run as root):

```bash
make ps SERVICE=traefik      # systemctl --user -M svc-traefik@ status traefik.service
make restart SERVICE=traefik # also: start, stop
make logs SERVICE=traefik    # journalctl _SYSTEMD_USER_UNIT=traefik.service -f
```

They are pod-aware: `SERVICE=<name>` resolves to `<name>-pod.service` when the service
user's manager knows that unit and to `<name>.service` otherwise, so
`make logs SERVICE=nextcloud` follows the pod. A single container of a pod is addressed
by its own unit name (`nextcloud-collabora.service`).

These five targets are the only ones meant to run on a host rather than in the
devtools image. The raw forms they wrap:

```bash
systemctl --user -M svc-traefik@ status traefik.service
systemctl --user -M svc-traefik@ restart traefik.service
journalctl _SYSTEMD_USER_UNIT=traefik.service -f # journalctl has no --user -M form
```

## Formatting

Prettier (+ `prettier-plugin-sh` for shell scripts) runs inside a small Docker image built from `devtools/` — nothing formatter-related is installed on the host besides `lefthook` itself.

```bash
make devtools      # build/rebuild that image
make format        # reformat the whole repo
make fmt-check     # check only, no writes
make install-hooks # one-time per clone: wires lefthook's pre-commit hook
```

Once hooks are installed, staged files are auto-formatted and re-staged on every commit (`lefthook.yml`, `stage_fixed: true`). Config: `.prettierrc` / `.prettierignore` at repo root.

## Storage architecture

Declared in `hosts/<host>/host.yml` under `storage`, realised by `ansible/roles/storage` — which runs **before `host_base`** and is skipped on a host that declares no `storage`. `ansible/roles/storage/README.md` is the reference; this is the shape.

```
Physical disks                     one rendered .mount unit per entry, What= a stable
  /mnt/data/data1, data2, data3    /dev/disk/by-partuuid (parity: by-id) path
  /mnt/parity/parity1

mergerfs pool
  /pool                            union over the data disks only (parity is not a branch)
  /pool/apps/<service>/<volume>    pool-class service volumes (storage_roots.pool)
  /pool/shared/media               the media library Jellyfin reads
  /pool/shared/scans, /pool/jlk/backups, …   former Samba trees, now data nothing serves

Snapraid                           /etc/snapraid.conf, rendered from the same block
  parity file on /mnt/parity/parity1/snapraid.parity
  content files on each data disk + /etc/snapraid.content
```

- **The role never partitions, formats or wipes.** No `mkfs`, `parted`, `wipefs`, `sgdisk`, ever. A declared device that is not there fails the converge naming the entry, and after mounting it asserts every declared path really is a mount point — the pair that replaced `host_base`'s `mountpoints` list, and what keeps an unmounted `/pool` from being recreated as empty directories on the root filesystem by the nightly deploy timer.
- **The create policy is `pfrd`** (proportional free random distribution: a branch chosen at random, weighted by free space) — **not** `eplfs`, which this file claimed for a long time and the deployed `pool.mount` never said. The option string is `storage.pool.options` in `host.yml` and is copied into the unit verbatim; that file is the source of truth for it.
- **A changed `pool.options`, branch or `fstype` remounts the union**, and every container holding a bind mount under it comes back reading an empty directory until it is restarted. The role warns before it does so; `--check --diff` first, then stop the pool-class services.
- **Packages:** mergerfs from Chaotic-AUR, snapraid and `mergerfs-tools` built from the AUR by the role, each pinned by AUR commit and `state: present`. The role never runs `pacman -Syu` and never reboots.

## Storage maintenance pipeline

`storage-maintenance.timer` (`OnCalendar` from `storage.snapraid.maintenance.on_calendar`, 02:00, `Persistent=true`) runs `storage-maintenance.service`, a oneshot that `Requires=` the pool and execs `/opt/storagebaby/maintenance/storage-maintenance-unattended.sh`. The role installs that whole directory, 0755 root: the wrapper, the orchestrator, `sync.sh`, `scrub.sh`, `balance_disks.sh`, the `plugins/` tree and `maintenance.env`.

The wrapper logs to `/var/log/storage-maintenance/` and mails the log with `mutt`, which is told to send through `msmtp` explicitly — the host runs no MTA. `/etc/msmtprc` (0600 root) is rendered from `storage.mail` plus `smtp_password` out of `hosts/<host>/secrets/mail.sops.yaml`; msmtp logs to the journal, so `journalctl -t msmtp` is where a refused relay shows up. If the pool does not come up, the unit's start job fails before the wrapper runs at all, which is what `storage-maintenance-failed.service` on `OnFailure=` exists to mail.

The orchestrator runs these steps in order, unchanged from the hand-stowed original:

1. `on-start` hook
2. Snapraid status print
3. `on-before-balance` hook → `balance_disks.sh` (mergerfs.balance, `balance_threshold`)
4. `on-before-sync` hook → `sync.sh` (snapraid touch + sync)
5. `on-after-sync` hook
6. `scrub.sh` (new blocks, then `scrub_percent` of anything older than `scrub_older_days`)
7. `on-after-scrub` hook
8. Final status + SMART report + `on-finish` hook

If any step or hook fails, the orchestrator runs the `on-failure` hook before aborting; that one is best-effort, so one broken recovery script does not stop the others. It also fires on SIGINT/SIGTERM (a systemd stop or a timeout mid-run), so a service stopped by `on-before-balance` is never left down.

**The hook contract is still `plugins/<name>/<hook>.sh`, but nothing hand-writes one any more.** `storage.snapraid.maintenance.stop_services` is the list (storagebaby: `[jellyfin]`); the role creates one directory per name and drops the same three scripts in — `on-before-balance.sh` (stop), `on-after-scrub.sh` and `on-failure.sh` (start) — which read the service name off their own directory, so they are byte-identical on every host, and resolve `<name>-pod.service` against `<name>.service` the way `make stop SERVICE=` does. A directory for a name no longer in the list is removed. The old `samba` and `snapshot-*` plugins are gone with the tree that held them: Samba is retired and Kopia is the only backup path.

**No parameter is rendered into a script.** They all come from `/opt/storagebaby/maintenance/maintenance.env`, which every script sources, so a threshold changes in git and a hand-run script behaves exactly like the timer's. To run maintenance manually, on the host:

```bash
doas systemctl start storage-maintenance.service           # the real nightly run, blocking
bash /opt/storagebaby/maintenance/storage-maintenance.sh   # the same, without wrapper or mail
bash /opt/storagebaby/maintenance/sync.sh                  # sync only
bash /opt/storagebaby/maintenance/scrub.sh                 # scrub only
bash /opt/storagebaby/maintenance/balance_disks.sh [0-100] # balance only
```

## Networking

Traefik runs rootless as `svc-traefik` with `Network=host` and is the only process on 80/443 (unprivileged-port sysctl lowered to 80). Every other service binds `127.0.0.1:<port>` — the port declared in its `service.yml`, unique per host and checked by a test.

Traefik's only provider is the file provider: the `service` role renders one route file per route into `/etc/storagebaby/traefik/dynamic.d/<name>-<domain>.yml` — one per `routes:` entry, so a two-route service gets two — pointing at `http://127.0.0.1:<port>`. No labels, no Docker socket, no shared container network — rootless containers of different users cannot reach each other's loopback, so cross-service traffic goes through Traefik and the public FQDN.

Resolving that FQDN from inside a container does not help: DNS answers with the host's own address, which pasta has copied onto the container's interface, so the connection terminates in the container, where nothing listens on 443. The role therefore renders `AddHost=<fqdn>:host-gateway` for **every** route name placed on the host into a Quadlet drop-in — `<name>.pod.d/` for a pod service, `<stem>.container.d/` otherwise — and a static test keeps those lines out of the service templates. Details in `ansible/roles/service/README.md`, "Reaching another service through Traefik".

Plain TCP goes through the same door: a service declares `tcp_ports: [{port: 21, target: 2121}, {range: [21100, 21109]}]`, the playbook collects every placed port into `placed_tcp_ports`, traefik's unit renders one static entrypoint `tcp-<port>` on `<tcp_bind_address>:<port>` per port (so placing one restarts Traefik once), and the `service` role renders one TCP router and service per port into `dynamic.d/<name>-tcp.yml`, forwarding to `127.0.0.1:<target>`. The entrypoint binds the host's own address, not the wildcard: `target` equals `port` wherever the protocol advertises the port it listens on (an FTP passive range), and a wildcard listener cannot coexist with the service's `127.0.0.1:<port>` publish. `tcp_bind_address` defaults to `ansible_default_ipv4.address` and is a top-level `host.yml` key. Plain TCP carries no hostname, so a port belongs to exactly one service per host — route ports, entrypoints and targets are one namespace, checked by `tests/static/test_ports.py` — and a port below 80 pulls the unprivileged-port sysctl down with it. Details in `ansible/roles/service/README.md`, "TCP ports".

TLS: with `acme: true` in `host.yml`, Traefik itself issues the wildcard cert for the host's domain via the Porkbun DNS challenge, state in the `letsencrypt` volume. `acme: false` (test hosts) serves Traefik's default certificate. Details in `hosts/shared/services/traefik/README.md`.

Updates are Podman's: floating tag plus `AutoUpdate=registry` and the user's `podman-auto-update.timer`, or a pinned tag bumped in git.

## Adding a new disk

`ansible/roles/storage/README.md`, "Adding a disk", has the procedure: partition and `mkfs.ext4` the disk by hand on the host (the role refuses to do either), read its `PARTUUID` off `lsblk`, add the entry to `storage.disks` in `hosts/<host>/host.yml`, push. The role mounts it, folds it into the pool and into `snapraid.conf`, and the next nightly `snapraid sync` writes its content file. Adding a branch changes `pool.mount`, so that converge remounts the pool — do it with the pool-class services stopped.

## Deployment

`bootstrap.sh` runs once as root on a fresh host: adds the Chaotic-AUR repository (same key, same package URLs and same marker lines as the `storage` role, held in agreement by `tests/static/test_bootstrap.py`) **before** its one `pacman -Syu`, so the upgrade already knows it and the first converge finds its own markers unchanged; installs git/ansible/sops/age/podman, generates `/etc/storagebaby/age.key` and an ed25519 deploy key, prints both public keys, installs `storagebaby-deploy.service` and `.timer`. That `-Syu` is the only full upgrade this repository ever runs: **keeping a host's packages current is the operator's job**, and no role upgrades or reboots one. The operator adds the deploy key to the repository as a read-only deploy key, adds the age recipient to `.sops.yaml` twice — under that host's own rule and under the `hosts/shared/**` rule, because every host runs the shared services — runs `sops updatekeys` over every affected `*.sops.yaml` (`make sops FILE=...` for editing), and pushes.

From then on the host deploys itself: the timer runs `ansible-pull` as root every 5 minutes (2 min after boot), checking out the `stable` branch into `/var/lib/storagebaby/repo` and running `ansible/playbook.yml --limit <hostname>`, only when the checkout changed.

`stable` is moved by CI, never by hand: `.github/workflows/ci.yml` runs the static checks and the `test-ci` Molecule scenario, and fast-forwards `stable` to the tested commit on a green master push. Until a host's age recipient is in `.sops.yaml`, its first converge fails at secret decryption — expected, and now in the `storage` role's mail task rather than in the first service, because `storage` runs first and `mail.sops.yaml` is the first secret the play reads.

Secrets are sops+age at rest and `podman secret`s at runtime. A host can only decrypt what it runs: `hosts/<h>/**` is encrypted to the operator's key plus that host's key, `hosts/shared/**` to the operator's key plus every host key. CI never holds a key; it only verifies each file's recipient list against `.sops.yaml`.
