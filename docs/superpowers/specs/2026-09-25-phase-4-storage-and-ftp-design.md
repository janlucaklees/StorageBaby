# Phase 4: storage roles, Samba retirement and FTP through Traefik

Status: reviewed, approved (2026-09-25); implemented 2026-10-01
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

- `samba/` and `snapraid/` at the repo root, `hosts/StorageBaby/services/paperless-upload/` and its symlinks, the `snapshot-*` plugins, the `samba` plugin.
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

**Amendment (2026-09-30, Task 3 review).** The entrypoints are `--entrypoints.tcp-<port>.address={{ tcp_bind_address }}:<port>`, not `:<port>`. A wildcard listener and the pod's `127.0.0.1:<port>` are mutually exclusive on Linux (EADDRINUSE, measured both orders), so the wildcard form ruled out every entry where the target equals the port — which is every port of a `range`, and the shape the FTP passive range must have, because an FTP server advertises the port it is itself listening on. `tcp_bind_address` defaults to `ansible_default_ipv4.address` and is overridable as a top-level `host.yml` key. Everything else above stands: the contract is unchanged (`target` still defaults to `port`, a range still forwards each port to itself), the pod still publishes only on loopback, and Traefik is still the only process on a routable address. The FTP part's `pasv_address` is that same address.

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

As implemented. `README.md`, "Operator steps before and right after the first storagebaby deploy", carries these as numbered steps 7–12 with the commands; this list is the summary.

- **Check the declaration against the running host** (step 7): the three data disks are `by-partuuid` paths and the parity disk a `by-id` one — four devices, not four partuuids — and the block was written from the deployed units in Task 1 rather than left for the operator to copy, so what is left is to read it back. `pool.options` has to stay byte-identical to the deployed `pool.mount`, and the disk names `d1`/`d2`/`d3` are snapraid's identity for those disks.
- **Fill `hosts/StorageBaby/secrets/mail.sops.yaml` (`smtp_password`) and `storage.mail.smtp_host`/`smtp_user`**, plus the paperless `ftp_password` (step 1's table). The relay was never captured in this repository; until it is real the maintenance run succeeds and its mail step fails, nightly.
- **Set `tcp_bind_address` if the host has more than one address** (step 8). This replaces the spec's `service_config.paperless.ftp_public_address`: the address is Traefik's to bind as well as the FTP server's to advertise, so it became one top-level `host.yml` key (§5's amendment) and the pod template reads it.
- **Point the printer at `<that address>:21`**, user `config.ftp_user` (`scanner`), passive mode.
- **The first converge is a planned outage, run by hand** (step 9): stop the deploy timer, pre-flight the whole playbook with `--check --diff`, stop every pool-class service **and `smb`/`nmb`** — `systemctl stop pool.mount` is a plain `umount` and fails `EBUSY` while `smbd` holds `/pool` — converge, start everything, start the timer. The rendered units cannot be byte-identical to the stowed ones, so all four branches and the pool are remounted once. That converge also adds Chaotic-AUR, refreshes the package databases once and may `pacman -U` the pinned snapraid over the installed one, leaving `base-devel` behind; Traefik restarts once for the new TCP entrypoints. A first converge that cannot decrypt now aborts in the `storage` role's mail task, because `storage` runs before the services.
- **Remove `/opt/scripts` and the old `~/StorageBaby` checkout after it succeeds** (step 10). The old timer is **not** disabled: `storage-maintenance.timer` and `.service` keep their names and the role overwrote both, so there is no second run — `systemctl cat storage-maintenance.service` showing `/opt/storagebaby/maintenance/…` is the check.
- **Stop, disable and uninstall Samba by hand** (step 10); the role does not remove packages it never installed. The share trees stay on disk.
- **Verify what the converge cannot** (step 12): every mount active and `/pool` on `fuse.mergerfs` with the declared options, `storage-maintenance.timer` enabled, one manual `storage-maintenance.service` run with its mail arriving, and a scan from the printer appearing in Paperless.

## 9. Amendments during implementation

Each of these changed something the sections above state. They are recorded here rather than rewritten into the text, so the spec still reads as what was approved.

1. **Packages come from Chaotic-AUR and the AUR, not from upstream artefacts** (2026-09-26, JLK). mergerfs is in Chaotic-AUR, which the role configures the project's documented way; `snapraid` and `mergerfs-tools-git` are not, so `tasks/aur_build.yml` builds them as an unprivileged build user, pinned by AUR commit. No tarball is unpacked under `/opt`.
2. **The test VM's disks are identified by GPT partition label, and are 4 GB each.** The tracked `host.yml` names `/dev/disk/by-partlabel/<name>` and the Molecule harness writes those labels, because `test_deploy.py` converges through `ansible-pull`, which sees no Molecule inventory. 1 GB branches left jellyfin too little room on the pool.
3. **`snapraid.maintenance.stop_services` replaces the hand-copied `plugins/jellyfin/` directory.** A verbatim plugin would stop a service `test-ci` does not place and fail every maintenance run there. It retires the `samba` plugin by construction.
4. **`mail` gained optional `tls` and `auth`.** With `auth on` and no TLS msmtp considers only SCRAM and would never authenticate against the test sink. Both default to the production values, so storagebaby's block is the spec's.
5. **Images are pulled before anything is written, and systemd's default start timeout is kept** (2026-09-29, JLK). A first start that pulls a large image can exceed `TimeoutStartSec=90`; the `service` role therefore pulls every image in `images.yml` before a unit is rendered, stopped or restarted, so a failed pull leaves the host byte-identical. A service that needs longer than 90 s to _start_ is itself suspect, so the timeout stays as it is.
6. **TCP entrypoints bind `tcp_bind_address`, not the wildcard** (§5's own amendment, 2026-09-30). Measured: a wildcard listener and the pod's `127.0.0.1:<port>` publish are mutually exclusive, which every `range` entry and the FTP passive range need. The same address is the FTP server's `pasv_address`, and it replaces the spec's `ftp_public_address`.
7. **A `.build` fixture service is kept on the test hosts.** `paperless-upload` was the only user of the role's build-unit path; `build-echo` keeps `settled_builds`, the build-before-pod ordering and the rebuild-on-config-change covered after it was retired.
8. **The FTP part carries `AddCapability=AUDIT_WRITE`.** Bisected: `pure-ftpd` exits 252 under podman's default capability set with no diagnostic, and `CAP_AUDIT_WRITE` — in Docker's default set, not podman's — is the one that makes it start. Rootless it is a capability inside the service user's own namespace, the same argument `AddCapability=MKNOD` on Collabora rests on.
9. **The first converge on storagebaby remounts the pool, and that is accepted** (2026-09-26, JLK) rather than avoided by reproducing the stowed `What=`. It is one planned outage, and §8 step 9 is the procedure.
10. **No role runs `pacman -Syu` and none reboots a host** (2026-09-29, JLK). Keeping a host current is the operator's job. The role's one `-Sy` database refresh, on the converge that added the repository, stays; `bootstrap.sh` adds Chaotic-AUR before its single `-Syu` so a fresh host's upgrade already knows it.
11. **`storage-maintenance-failed.service` was added.** The maintenance unit `Requires=` the pool, so a pool that does not come up fails the unit's _start job_ and `ExecStart=` never runs — the wrapper's own mail included. The `OnFailure=` notifier is what mails that night instead.
