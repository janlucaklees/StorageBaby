# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Configuration and scripts for a home NAS/media server ("StorageBaby") running Arch Linux. It covers the full stack: physical disk management, parity/redundancy, file sharing, and Docker-based media services.

## Managing Docker services

Each service lives in its own directory with a `docker-compose.yml`. Two ways to operate them:

**Via `manage.sh`** (preferred for one-off commands — auto-loads `.env`):

```bash
./manage.sh <service> <docker-compose-subcommand>
# e.g.: ./manage.sh jellyfin up -d
#        ./manage.sh traefik logs -f
```

**Via Makefile** (each service dir has one with common targets: `start`, `stop`, `ps`, `logs`, `remove`, `clean`):

```bash
cd traefik && make start
```

The global `.env` (copy from `.env.example`) provides `TZ`. Some services also accept a service-level `.env.sh` for dynamic env vars.

## Formatting

Prettier (+ `prettier-plugin-sh` for shell scripts) runs inside a small Docker image built from `devtools/` — nothing formatter-related is installed on the host besides `lefthook` itself.

```bash
make fmt           # reformat the whole repo
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
  /pool/apps/<service>/volumes/   ← persistent Docker volumes
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

## Docker networking

All web-facing services join the external `traefik_network` Docker network. Traefik is the sole entry point — services expose themselves via labels (`traefik.enable: "true"`, router/service labels). Watchtower auto-updates containers that have `com.centurylinklabs.watchtower.enable: "true"`.

TLS is handled by Traefik reading certs from the shared `letsencrypt` volume. Certbot (in the traefik stack) issues a wildcard cert for `*.home.klees.io` via DNS challenge:

```bash
cd traefik && make cert
```

## Adding a new disk

See `snapraid/README.md` for the full procedure: partition → ext4 → systemd mount unit → snapraid.conf update → mergerfs pool.mount update.

## Deployment

Config files are deployed using GNU Stow (`stow -vv -t / <dir>`) or direct copies with `doas`/`sudo`. See each service's `install.sh` for the exact steps. Packages are managed with `yay` (Arch AUR).
