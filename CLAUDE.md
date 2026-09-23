# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Git-driven configuration for a home NAS/media server ("StorageBaby") running Arch Linux. It covers the full stack: physical disk management, parity/redundancy, file sharing, and media services as rootless Podman containers. A host is set up once with `bootstrap.sh` and converges itself from this repository after that; nothing is changed on a host by hand. Design: `docs/superpowers/specs/2026-09-21-gitops-podman-platform-design.md`.

Migrated so far: traefik (shared), and on storagebaby yuzukam, stirling-pdf, jellyfin, paperless-upload and kopia. The `docker-compose.yml` directories still at the repo root are the not-yet-migrated remainder: `nextcloud/` and `paperless/` are tracked Phase 3 inputs, `immich/` and `openarchiver/` are untracked working copies of the same phase, and `samba/` and `snapraid/` are Phase 4. A new service goes under `hosts/`, never into one of them.

## Managing services

Placement is the folder:

- `hosts/<host>/host.yml` — everything host-specific (domain, ACME, storage roots, volume overrides, deploy timer, `gpu`, `packages`, `service_config`).
- `hosts/<host>/services/<name>/` — a service that runs on that host only.
- `hosts/shared/services/<name>/` — a service that runs on every host (traefik).
- `hosts/<host>/secrets/<service>.sops.yaml` — optional per-host override of a service's secrets.

A service folder is a contract, not a script:

```
service.yml        name, port (loopback), domain, volumes, binds, devices, groups, config, secrets, backup policy
quadlet/*.j2       Podman Quadlet units, rendered per host (vars: volumes.<name>, binds, config_dir, port, fqdn, tz)
config/            copied to /etc/storagebaby/<name>/, read-only for the service user
secrets.sops.yaml  sops+age encrypted key/value pairs
```

`service.yml` beyond the basics — `ansible/roles/service/README.md` is the full reference for what a template may use:

- **`volumes`** — `<name>: { class: pool | fast }`, resolved against the host's `storage_roots` (or a `volume_overrides` entry) to a directory the role creates **only when it is absent**, 0750 and service-owned. An existing one is never re-permissioned: images chown their data tree on every start, so enforcing a mode would report changed forever and take the running service's access away.
- **`binds`** — `<name>: { host, container, mode, group }`: a pre-existing host tree mounted into the container. The role guarantees the group exists and that `svc-<name>` is in it, and creates the host directory as `root:<group> 2775` if missing — again never re-permissioning an existing one.
- **`devices`** — a list of device paths, rendered as `AddDevice=` **only** on a host whose `host.yml` says `gpu: true` (the template reads `devices_enabled`). Test hosts have no GPU and get no device line.
- **`groups`** — extra host supplementary groups for the service user. Any `groups` or bind group sets `keep_groups`, which is what a template turns into `GroupAdd=keep-groups`. It carries the groups into the container's credentials, and it does **not** survive an image that switches user through s6 — see `hosts/storagebaby/services/jellyfin/README.md`.
- **`config`** — free-form, readable in templates as `service.config.*`, and overridable per host through `service_config: { <service>: { ... } }` in `host.yml` (host wins, deep-merged in the playbook). That is how the test host gives kopia a filesystem repository while storagebaby uses S3.
- **`port`** is optional, and required only when there is a `domain`. paperless-upload has neither.

Placing a storagebaby service on the test host is a symlink, never a copy — one folder, two hosts: `hosts/test-a/services/<name> -> ../../storagebaby/services/<name>`.

The generic `service` role in `ansible/roles/service/` turns that into a running service: system user `svc-<name>` with subids and linger, volume and bind directories, group memberships, `podman secret`s synced from sops, quadlets rendered into `/etc/containers/systemd/users/<uid>/`, a Traefik route file, then restarts only what changed.

Work on the repo through the Makefile — everything runs in the `devtools` image, nothing is installed on the workstation:

```bash
make devtools                         # build the tooling image (once)
make test-static                      # contract, secrets, render checks
make test-integration                 # Molecule scenario test-ci in a KVM VM (needs libvirt + KVM)
make molecule CMD=converge            # a single Molecule step in that scenario
make molecule-login                   # SSH into the running test VM
make molecule-exec CMD='podman ps -a' # one command on it, no TTY needed
make sops FILE=hosts/shared/services/traefik/secrets.sops.yaml
```

`make molecule-exec` is how to look at a running test VM from a script or without a terminal — `molecule login` needs a real TTY. It runs the command through Ansible against the inventory Molecule already wrote, so there is no second source of truth for the VM's address and key.

On a host, service units belong to the service user's systemd manager (run as root):

```bash
make ps SERVICE=traefik      # systemctl --user -M svc-traefik@ status traefik.service
make restart SERVICE=traefik # also: start, stop
make logs SERVICE=traefik    # journalctl _SYSTEMD_USER_UNIT=traefik.service -f
```

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

```
Physical disks
  /mnt/data/data1, data2, data3   ← data disks (ext4, systemd .mount units)
  /mnt/parity/parity1             ← snapraid parity disk

mergerfs pool
  /pool                           ← union FS over /mnt/data/* (create policy: eplfs)
  /pool/apps/<service>/<volume>   ← service volumes (pool class), resolved by the service role
  /pool/shared/media              ← media library (Jellyfin + Samba)
  /pool/shared/scans, /pool/jlk/backups, etc.

Snapraid
  parity file on /mnt/parity/parity1/snapraid.parity
  content files mirrored on each disk + /etc/snapraid.content
```

mergerfs uses `eplfs` (existing path, least free space) as the create policy, meaning files in an existing directory stay together on the same disk, and new directories go to the disk with least free space (to balance usage).

## Storage maintenance pipeline

A systemd timer runs daily at 02:00 via `storage-maintenance.service` → `/opt/scripts/storage-maintenance-unattended.sh`. That wrapper handles logging to `/var/log/storage-maintenance/` and emails results via `mutt`.

The core orchestrator is `snapraid/storage-maintenance/storage-maintenance.sh`, which runs these steps in order:

1. `on-start` hook
2. Snapraid status print
3. `on-before-balance` hook → `balance_disks.sh` (mergerfs.balance, default ≤5% imbalance)
4. `on-before-sync` hook → `sync.sh` (snapraid touch + sync)
5. `on-after-sync` hook
6. `scrub.sh` (scrub new files, then scrub 8% of old files older than 12 days)
7. `on-after-scrub` hook
8. Final status + SMART report + `on-finish` hook

If any step or hook script fails, the orchestrator runs the `on-failure` hook before aborting. Unlike other hooks, `on-failure` is best-effort: a failing plugin script there doesn't stop the remaining plugins' `on-failure` scripts from running, so e.g. samba still gets restarted even if jellyfin's restart script breaks. `on-failure` also fires if the script is killed by SIGINT/SIGTERM (e.g. a systemd stop or timeout mid-run), so services stopped by `on-before-balance` don't get left down.

**Plugin system:** Drop a script at `snapraid/storage-maintenance/plugins/<plugin-name>/<hook>.sh` to participate in any hook. Existing plugins:

- `jellyfin/` — stops Jellyfin before balance, starts it again after scrub or on failure
- `samba/` — stops smb/nmb before balance, starts again after scrub or on failure
- `snapshot-nextcloud/` — SSHes to `cloud.janlucaklees.de`, takes a DB snapshot, rsyncs it locally
- `snapshot-immich/`, `snapshot-paperless/` — similar remote snapshot/rsync patterns

To run maintenance manually:

```bash
bash snapraid/storage-maintenance/storage-maintenance.sh   # full run
bash snapraid/storage-maintenance/sync.sh                  # sync only
bash snapraid/storage-maintenance/scrub.sh                 # scrub only
bash snapraid/storage-maintenance/balance_disks.sh [0-100] # balance only
```

## Networking

Traefik runs rootless as `svc-traefik` with `Network=host` and is the only process on 80/443 (unprivileged-port sysctl lowered to 80). Every other service binds `127.0.0.1:<port>` — the port declared in its `service.yml`, unique per host and checked by a test.

Traefik's only provider is the file provider: the `service` role renders one route file per placed service into `/etc/storagebaby/traefik/dynamic.d/<name>.yml`, pointing at `http://127.0.0.1:<port>`. No labels, no Docker socket, no shared container network — rootless containers of different users cannot reach each other's loopback, so cross-service traffic goes through Traefik and the public FQDN.

TLS: with `acme: true` in `host.yml`, Traefik itself issues the wildcard cert for the host's domain via the Porkbun DNS challenge, state in the `letsencrypt` volume. `acme: false` (test hosts) serves Traefik's default certificate. Details in `hosts/shared/services/traefik/README.md`.

Updates are Podman's: floating tag plus `AutoUpdate=registry` and the user's `podman-auto-update.timer`, or a pinned tag bumped in git.

## Adding a new disk

See `snapraid/README.md` for the full procedure: partition → ext4 → systemd mount unit → snapraid.conf update → mergerfs pool.mount update.

The root `install.sh` that used to `yay -S` the storage packages and `stow` those units is removed — the container half of it is what the platform replaced. Until Phase 4 ports mounts, mergerfs, snapraid and samba into Ansible roles, that half stays a manual `stow -vv -t / <dir>` per the procedure above.

## Deployment

`bootstrap.sh` runs once as root on a fresh host: installs git/ansible/sops/age/podman, generates `/etc/storagebaby/age.key` and an ed25519 deploy key, prints both public keys, installs `storagebaby-deploy.service` and `.timer`. The operator adds the deploy key to the repository as a read-only deploy key, adds the age recipient to `.sops.yaml` twice — under that host's own rule and under the `hosts/shared/**` rule, because every host runs the shared services — runs `sops updatekeys` over every affected `*.sops.yaml` (`make sops FILE=...` for editing), and pushes.

From then on the host deploys itself: the timer runs `ansible-pull` as root every 5 minutes (2 min after boot), checking out the `stable` branch into `/var/lib/storagebaby/repo` and running `ansible/playbook.yml --limit <hostname>`, only when the checkout changed.

`stable` is moved by CI, never by hand: `.github/workflows/ci.yml` runs the static checks and the `test-ci` Molecule scenario, and fast-forwards `stable` to the tested commit on a green master push. Until a host's age recipient is in `.sops.yaml`, its first converge fails at secret decryption — expected.

Secrets are sops+age at rest and `podman secret`s at runtime. A host can only decrypt what it runs: `hosts/<h>/**` is encrypted to the operator's key plus that host's key, `hosts/shared/**` to the operator's key plus every host key. CI never holds a key; it only verifies each file's recipient list against `.sops.yaml`.
