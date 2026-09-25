# Phase 4: storage roles, Samba retirement and FTP through Traefik

Status: reviewed, approved (2026-09-25)
Date: 2026-09-25
Extends: `2026-09-21-gitops-podman-platform-design.md`, `2026-09-22-phase-2-single-services-design.md`, `2026-09-23-phase-3-pods-and-backups-design.md`.

## 1. Scope

Port the storage half of the host into git: disk and parity mounts, the mergerfs pool, snapraid, the nightly maintenance run and its mail. Retire Samba, the remote snapshot plugins and the paperless uploader. Give the platform TCP routing through Traefik so a printer can deliver scans by FTP straight into Paperless's consume directory. After this phase nothing on a host is hand-stowed; `samba/` and `snapraid/` leave the repo root.

Decisions JLK made during brainstorming:

- **The role never formats a disk.** Partitioning and `mkfs` for a new disk stay a documented one-time hand step; the role mounts what `host.yml` declares and fails if a declared device is absent.
- **Samba is dropped entirely.** No shares, no SMB users, no `samba` maintenance plugin. The old share trees (`/pool/shared/utility`, `/pool/shared/maki`, `/pool/mk`, `/pool/jlk/backups`) stay on disk as untouched data with nothing serving them.
- **The remote snapshot plugins go.** The services they snapshotted move home and Kopia is the only backup path.
- **Scans arrive by FTP**, through Traefik, into a volume shared with Paperless's consumer. `paperless-upload` and the `/pool/shared/scans` bind are retired.
- **Maintenance mail stays**, sent by `msmtp` configured from `host.yml` with the SMTP password in the host's sops secrets.
- **mergerfs keeps `category.create=pfrd`** as deployed today; the docs that said `eplfs` are corrected.
- The maintenance scripts move as they are; paths are rendered, logic is not rewritten.

## 2. Contract: `storage` in `host.yml`

```yaml
storage:
  disks:
    - {
        name: data1,
        device: /dev/disk/by-partuuid/a36d7f0a-…,
        mount: /mnt/data/data1,
        fstype: ext4
      }
    - {
        name: data2,
        device: /dev/disk/by-partuuid/…,
        mount: /mnt/data/data2,
        fstype: ext4
      }
    - {
        name: data3,
        device: /dev/disk/by-partuuid/…,
        mount: /mnt/data/data3,
        fstype: ext4
      }
  parity:
    - {
        name: parity1,
        device: /dev/disk/by-partuuid/…,
        mount: /mnt/parity/parity1,
        fstype: ext4
      }
  pool:
    mount: /pool
    options: defaults,allow_other,use_ino,category.create=pfrd,moveonenospc=true,minfreespace=20G,fsname=mergerfsPool,cache.writeback=false,cache.symlinks=false,cache.readdir=false,async_read=true,threads=4
  snapraid:
    block_size: 256
    excludes:
      - '*.bak'
      - '/lost+found/'
      - '/apps/nextcloud/html/apps/'
      - …
    maintenance:
      on_calendar: '02:00'
      balance_threshold: 5
      scrub_percent: 8
      scrub_older_days: 12
  mail:
    to: jlk@example.org
    from: storagebaby@example.org
    smtp_host: smtp.example.org
    smtp_port: 587
    smtp_user: storagebaby@example.org
```

- `disks` and `parity` are lists of devices the role mounts. `device` is a stable path (`by-partuuid` as today); `mount` is absolute; `fstype` is passed to the unit. The role creates the mount point directory and the `.mount` unit, nothing else on that device.
- `pool.options` is the verbatim mergerfs option string. `pool.mount` is the union's mount point; the branches are every `disks[].mount`.
- `snapraid.excludes` are snapraid `exclude` lines relative to the pool, carried over from the current file with the Nextcloud paths updated to the platform's volume layout (`/apps/nextcloud/html/…`). Content files are rendered for `/etc/snapraid.content` and one per data disk; the parity file is `<parity mount>/snapraid.parity`. Several parity entries render several parity levels in list order.
- `snapraid.maintenance` parametrises the orchestrator: timer schedule, balance threshold, scrub percentage and age, all defaulting to today's values.
- `mail` configures `msmtp`; the password is `hosts/<host>/secrets/mail.sops.yaml`, key `smtp_password`. A test host points `smtp_host` at a local sink (section 6).
- `mountpoints` in `host.yml` is retired: the role asserts every declared disk, parity and pool mount itself before any service runs, which is what the list did by hand.
- `storage_roots.pool` is expected under `pool.mount` (`/pool/apps`), and the role's assertion is what protects it.

## 3. `storage` role

`ansible/roles/storage`, included by the playbook before `host_base`, skipped when `host.yml` has no `storage` block.

1. Packages: `mergerfs`, `snapraid`, `smartmontools`, `msmtp`, `mutt` (the wrapper still composes with it).
2. For every disk and parity entry: `mkdir` the mount point; render `<escaped-path>.mount` (`What=` device, `Where=`, `Type=`, `Options=defaults`, `RequiredBy=local-fs.target`); enable and start it. A device that does not exist fails the converge with the entry's `name`.
3. `pool.mount`: `What=` the disks' mount points joined by `:`, `Type=fuse.mergerfs`, `Options=` from `pool.options`, `Requires=` every disk and parity unit. Enabled and started.
4. Assert every mount is active (`mountpoint -q`) after starting. This replaces `host_base`'s `mountpoints` check.
5. `/etc/snapraid.conf` from the block. `/opt/storagebaby/maintenance/` gets `storage-maintenance.sh`, `balance_disks.sh`, `sync.sh`, `scrub.sh`, `plugins/jellyfin/*.sh` and the unattended wrapper, all mode 0755, root-owned; the wrapper's log directory `/var/log/storage-maintenance/`. `storage-maintenance.service` and `.timer` (`OnCalendar` from the block, `Persistent=true`), timer enabled.
6. `/etc/msmtprc` (0600, root) with account `default` from `mail` and the decrypted password; the wrapper mails through it.
7. Change handling: a changed `.mount` unit reloads systemd and restarts that unit only. A changed `pool.mount` restarts the pool, which every pool-class volume sees as a short outage; the role prints a warning task naming the change before it does so, and the README tells the operator to run the playbook with `--check --diff` before pushing a pool option change. Script and config changes need no restart. The role never runs `mkfs`, `wipefs`, `parted` or `snapraid fix`.

`host_base` loses the `mountpoints` assertion and the storage packages it does not need.

## 4. Retirements

- `samba/` and `snapraid/` at the repo root, `hosts/storagebaby/services/paperless-upload/` and its symlinks, the `snapshot-*` plugins, the `samba` plugin.
- Paperless loses the `scans` bind and its group; Jellyfin's `media` note stays.
- `README.md`, `CLAUDE.md`, `snapraid/README.md`'s "adding a new disk" procedure (rewritten as: partition, `mkfs`, add the entry to `host.yml`, push) and the Phase 2 open item on shared-tree permissions (closed: only Jellyfin's `media` tree remains, `o+rX` as documented).

## 5. TCP through Traefik and the FTP part

### Contract

```yaml
tcp_ports:
  - { port: 21, target: 2121 } # Traefik listens on 21, forwards to 127.0.0.1:2121
  - { range: [21100, 21109] } # each port forwarded to the same port on loopback
```

- The playbook collects `placed_tcp_ports` across placed services, like `placed_fqdns`. The traefik container template renders one static entrypoint per port (`tcp-<port>`, `:<port>`), so adding a port restarts traefik once. The service role renders `<name>-tcp.yml` into `dynamic.d/` with one TCP router (`HostSNI('*')`, the port's entrypoint) and one TCP service (`127.0.0.1:<target>`) per port. Removal cleans the file as route files are cleaned.
- Plain TCP has no hostname, so a port belongs to one service per host. `test_ports` treats `tcp_ports` and route ports as one namespace and rejects duplicates and ranges that overlap.
- Traefik stays the only process on non-loopback ports; the pod publishes `127.0.0.1:2121` and `127.0.0.1:21100-21109`.

### Paperless

- New part `paperless-ftp`: a pinned FTP server image (vsftpd or pure-ftpd, decided in the plan by which image runs unprivileged in a pod and supports a fixed passive range), one user `config.ftp_user` with `secrets: [ftp_password]`, chrooted to the consume directory, `pasv_address` from `service.config.ftp_public_address` (set per host in `service_config`: storagebaby's LAN address, the test host's VM address), passive range equal to the declared `tcp_ports` range, listening on 2121 inside the pod.
- New volume `consume` (fast class), mounted read-write in `paperless-ftp` and in `paperless-app` at `/usr/src/paperless/consume`; `PAPERLESS_CONSUMPTION_DIR` set, `PAPERLESS_CONSUMER_POLLING` on (FTP writes are not inotify-friendly across containers), `PAPERLESS_CONSUMER_RECURSIVE` off. The consumer deletes files it ingests, as today.
- No TLS: printers speak plain FTP on the LAN, and explicit FTPS cannot be terminated by Traefik. The README says so and names the exposure (credentials in clear on the LAN).
- `backup.paths` unchanged; `consume` is transient.

## 6. Tests

### Harness

- `create.yml` attaches four 1 GB qcow2 disks to the VM; `destroy.yml` removes them.
- `prepare.yml` partitions each with one partition and `mkfs.ext4`, then reads the four partuuids and writes them into `hosts/test-a/host.yml`'s and `hosts/test-ci/host.yml`'s `storage` block at converge time through a generated inventory variable, so the tracked `host.yml` files reference the disks by a stable name (`test-data1`) and the harness supplies the device path. The role receives `device` from either source; static tests render with placeholders.
- Test hosts' `storage_roots.pool` becomes `/pool/apps`, so every existing volume test runs on mergerfs. `mail.smtp_host` points at a local sink: `prepare.yml` installs a tiny SMTP receiver that writes each message to `/var/spool/test-mail/`.
- VM memory unchanged; disk adds 4 GB.

### Static

- Every `.mount` and `pool.mount` rendered for every host passes `systemd-analyze verify`.
- `snapraid.conf` renders for every host; `snapraid -c <rendered> status` against a stub tree of empty disk directories reports every declared disk and parity (proves the file parses).
- `msmtprc` renders without the password (`REPLACE_ME` placeholder in the static run) and contains `to`, `from`, `host`, `port`, `user`.
- `tcp_ports` shape (port or range, targets loopback), no overlap with route ports across placed services; every declared port has an entrypoint in the rendered traefik unit and a router in the rendered route file.
- The retired directories and the `mountpoints` key are gone (`test_hosts` rejects `mountpoints`).

### Integration

- All declared mounts active; `/pool` mounted `fuse.mergerfs` with the declared options; a file written to the pool lands on exactly one data disk.
- `storage-maintenance.timer` enabled; `systemctl start storage-maintenance.service` exits clean; the journal shows sync and scrub; `snapraid.content` exists on every data disk and `snapraid.parity` on the parity disk; the mail sink holds one message whose body contains the status block.
- A converge with one data disk unmounted (the test unmounts it, runs the deploy service, remounts) fails naming the disk and restarts no service unit.
- A changed pool option (test edits `host.yml` in the seeded remote) restarts `pool.mount` once and leaves the pool mounted.
- Traefik listens on 21 and the passive range, nothing else does; an FTP client on the VM logs in through port 21 with a passive transfer and uploads a PDF; the document appears in Paperless (`/api/documents/` count grows) within a timeout; the consume directory is empty afterwards.
- `test-ci` runs the storage role and the FTP case (paperless is placed there); `test-a` runs everything.

## 7. Migration order

1. Contract and harness: `storage` block, virtual disks, formatting in `prepare.yml`, static tests, `storage` role with mounts and pool only; test hosts on mergerfs, all existing tests green.
2. Snapraid, maintenance scripts, timer, mail; `mountpoints` retired from `host_base`.
3. `tcp_ports` in contract, playbook, traefik template, service role, static tests.
4. Paperless FTP part and `consume` volume; `paperless-upload` and the `scans` bind removed.
5. Retire `samba/`, `snapraid/`, plugins; docs; storagebaby's `storage` block written from the current mount units and `snapraid.conf`.

## 8. Operator items this phase creates

- Copy the four `by-partuuid` paths from the deployed mount units into `hosts/storagebaby/host.yml`; keep `pool.options` byte-identical to the deployed `pool.mount` so the first converge does not remount the pool (verify with `--check --diff`).
- Fill `hosts/storagebaby/secrets/mail.sops.yaml` (`smtp_password`) and the paperless `ftp_password`; set `service_config.paperless.ftp_public_address` to storagebaby's LAN address.
- Point the printer at `<storagebaby>:21`, user `config.ftp_user`.
- Remove the hand-stowed units and `/opt/scripts` after the first converge succeeds (the role installs under `/opt/storagebaby/`; the old timer must be disabled so the run does not happen twice). The README gives the commands.
- Stop and disable `smb`/`nmb` and uninstall `samba` by hand; the role does not remove packages it never installed.
