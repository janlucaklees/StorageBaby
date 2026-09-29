# The `storage` role

Turns a host's `storage` block in `hosts/<host>/host.yml` into the filesystems the host is
made of: one `.mount` unit per declared disk and parity disk, a `fuse.mergerfs` union over
the data disks, and an assertion that every one of them is really mounted. The playbook
runs it **before `host_base`**, because `host_base` creates the storage class roots below
the pool, and it is skipped entirely on a host whose `host.yml` declares no `storage`.

## What it refuses to do

**It never partitions, formats or wipes a device.** There is no `mkfs`, `wipefs`, `parted`
or `sgdisk` anywhere in it, and there never will be. Preparing a new disk is a documented
one-time hand step; the role mounts what the declaration names and **fails, naming the
entry**, when a declared device is not there:

```
Disk d2 is declared in hosts/storagebaby/host.yml with device
/dev/disk/by-partuuid/243e…, and nothing is there. Refusing to converge: the role does
not create, partition or format a disk.
```

That refusal is the whole point of the role. `host_base`'s `file: state=directory` creates
every missing parent on the way, so a converge with `/pool` unmounted would recreate
`/pool/apps` and every service volume under it on the root filesystem — empty, unattended,
from the nightly deploy timer — and the services would come up on the wrong data. Two
checks stand in the way: every declared device exists, and after mounting, every declared
path is really a mount point (`mountpoint -q`). The second is what replaced `host_base`'s
hand-written `mountpoints` list.

## The contract

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
```

- **`name`** is the disk's identity across the whole platform: it is what `snapraid.conf`
  calls the disk, and on a test host it is the GPT partition label the Molecule harness
  writes so that `/dev/disk/by-partlabel/<name>` resolves.
- **`device`** is a stable path under `/dev` — `by-partuuid` on storagebaby, `by-partlabel`
  on a test VM. Never `/dev/sdX`.
- **`options`** is optional per entry and becomes the unit's `Options=`; left out, it is
  `defaults`, which is what every disk declared today wants and what the hand-stowed units
  carried. A disk that needs `noatime` or `nofail` says so here. Changing it rewrites that
  disk's unit, so it is a remount — read "Change handling" below before pushing one.
- **`parity` is required but is not a branch.** The pool unit `Requires=` it, because a
  pool that outlives its parity disk hides that the array is unprotected; the union is
  over the data disks only, or mergerfs would be handed its own redundancy to spread
  around.
- **`pool.options`** is the verbatim mergerfs option string. `storage_roots.pool` has to
  live under `pool.mount`, which `tests/static/test_storage.py` enforces.

`storage.snapraid` and `storage.mail` belong to the same block; "The array, the nightly run
and its mail" below has them.

### Why the unit name is a naive escape

A mount unit's name is its `Where=` path with the slashes turned into dashes — which is
`systemd-escape`'s answer only for a path that carries none of the characters systemd
hex-escapes. `/mnt/data-1` would render `mnt-data-1.mount`, a unit systemd reads as
`/mnt/data/1`, and it would mount the right device in the wrong place. So the static
contract restricts a declared `mount` to lowercase letters, digits and slashes, and the
templates escape naively. Widen that regex and the escape has to become real.

## Where the tools come from

- **mergerfs: Chaotic-AUR.** It is not in Arch's official repositories. Chaotic-AUR is a
  signed binary repository of AUR builds, so the role imports and locally signs the
  project's bootstrap key (`3056513887B78AEB`), installs `chaotic-keyring` and
  `chaotic-mirrorlist` from the project's own package URLs, appends the `[chaotic-aur]`
  section to `/etc/pacman.conf` and installs `mergerfs`. The key dance is gated on
  `chaotic-keyring` already being installed, which is what makes a second converge report
  no change.
- **Everything Chaotic-AUR does not carry: built from the AUR, here, pinned.**
  `tasks/aur_build.yml` takes `aur_package`, `aur_commit` and `aur_version`, and is one
  block under one condition — the pinned `pkgver-pkgrel` is not the installed one — so a
  converged host does no clone, no compile and no network. It clones the AUR package at the
  pinned commit **as an unprivileged build user** (`aurbuild`, home under
  `/var/lib/aurbuild`, no login shell), asserts that the commit really produces
  `aur_version`, installs the recipe's `depends`/`makedepends` **as root**, and runs
  `makepkg --nocheck` as the build user before `pacman -U`ing the result. Task 1 uses it
  for `snapraid` and for `mergerfs-tools-git` -- whose pin works differently, see below.
- **The build user has no sudo or doas rights, on purpose.** `makepkg -s` would need to run
  pacman as root, and a NOPASSWD pacman rule for a service account is a root-equivalent
  grant that outlives the build. The dependencies come from the pinned `.SRCINFO` and are
  installed by the play, which is already root.
- **Every git command in the build runs as the build user**, never as root: git refuses to
  operate in a repository owned by someone else, and a system-wide `safe.directory` entry
  is a worse thing to leave behind than a clone nothing else touches.
- **`state: present`, never `latest`.** A host that already has mergerfs or snapraid from
  its own AUR builds keeps them; a version is changed by bumping the pin in
  `defaults/main.yml` and pushing, exactly like a container image tag.
- **`mount.fuse.mergerfs`.** `Type=fuse.mergerfs` makes `mount(8)` look for a
  `mount.fuse.mergerfs` helper; the package ships it as `mount.mergerfs`. The role links
  the subtype name to it, only when absent, so nothing is laid over a path a package owns.

## Change handling, and the one outage this role can cause

A changed `.mount` reloads systemd and restarts that unit. A changed `pool.mount` — or a
changed branch, since a branch cannot be unmounted while mergerfs holds it open, so the
pool comes down first — **remounts the union**, and every container holding a bind mount
under it comes back reading an empty directory until it is restarted. The role prints a
warning task naming the change before it touches anything:

```
pool.mount or one of its branches changed and will be remounted. Every service with a
pool-class volume loses its bind mount across this and has to be restarted afterwards.
```

**So: run the playbook with `--check --diff` before pushing a change to `pool.options`, a
branch's `device`, `mount` or `fstype`.** If the diff shows `pool.mount` changing, stop the
affected services first (`make stop SERVICE=<name>`) and start them after.

This role is check-mode clean: every read-only probe carries `check_mode: false` so its
result is still there for the conditional that reads it, and every step that installs
something is skipped, so a `--check` run reports what it would install and installs
nothing. Read the whole role's diff and the warning task from that run.

**The whole playbook is check-mode clean, not just this role.** The `service` role had the
same idiom in five places — a registered `command` whose `stdout` or `rc` a later
`set_fact`, `until` or `when` reads, where check mode skips the command and leaves behind a
result with neither — so every one of them carries `check_mode: false` now, and a
`--check --diff` run of the whole `ansible/playbook.yml` ends `failed=0`. A test on the VM
converges the playbook in check mode the way `ansible-pull` does and asserts exactly that
(`tests/integration/molecule/test-ci/tests/test_deploy.py`). So the pre-flight really is
one: run it, read the whole diff, then push.

**This applies to storagebaby's very first converge, and it remounts more than the pool.**
None of the five rendered units can be byte-identical to the hand-stowed ones they replace:
`pool.mount`'s `What=` is the explicit branch list `/mnt/data/data1:…:data3` where the
stowed unit had the glob `/mnt/data/*`, and each of the four branch units gets a
`Description=` of `d1 (/mnt/data/data1)` where the stowed one said `Data Disk 1 mount`. The
options are the deployed ones verbatim, `category.create=pfrd` included, but those lines
differ — so the first converge **will** remount all four branches **and** `/pool`, in one
planned outage.

Which means **stopping every pool-class service is not enough: `smb` and `nmb` have to be
stopped too.** Taking a branch down takes the pool down first, and `systemctl stop
pool.mount` is a plain `umount` — it fails with `EBUSY` while anything at all holds `/pool`
open, and until Samba is retired (Task 5) `smbd` serves `/pool/shared/*` the whole time.
So: stop every service with a pool-class volume, `systemctl stop smb nmb`, converge, then
start them again. Task 5's operator steps carry the same list.

Script and configuration changes need no restart.

## The array, the nightly run and its mail

The same `storage` block carries the rest of it.

```yaml
storage:
  snapraid:
    block_size: 256
    excludes: ['*.bak', /lost+found/, /apps/nextcloud/html/apps/, …]
    maintenance:
      on_calendar: '02:00'
      balance_threshold: 5
      scrub_percent: 8
      scrub_older_days: 12
      stop_services: [jellyfin]
  mail:
    to: email@janlucaklees.de
    from: storagebaby@janlucaklees.de
    smtp_host: smtp.example.org
    smtp_port: 587
    smtp_user: storagebaby@example.org
    # tls: true   (default)
    # auth: on    (default)
```

- **`/etc/snapraid.conf`** is rendered from `disks`, `parity` and `snapraid`: one `content`
  line per data disk plus `/etc/snapraid.content` on the root filesystem, one parity line per
  `parity` entry in list order (`parity`, `2-parity`, …), one `disk <name> <mount>/` per data
  disk, the `excludes` and the `block_size`. Nothing restarts when it changes — snapraid is
  run by a timer and reads the file every time.
- **`snapraid.excludes` are paths relative to a _disk's_ root**, which for a branch of this
  pool is the same as relative to `pool.mount`. That is why the Nextcloud entries are
  `/apps/nextcloud/html/…`: the pool-class volume `html` of the `nextcloud` service.
- **The disk `name` is snapraid's identity for that disk.** Renaming one makes snapraid treat
  the whole disk as new, which is a full parity rewrite on the next sync.
- **`/opt/storagebaby/maintenance/`** holds the orchestrator, `sync.sh`, `scrub.sh`,
  `balance_disks.sh` and the unattended wrapper, all 0755 root, **copied byte for byte** from
  the role's `files/maintenance/`. Logs go to `/var/log/storage-maintenance/` (the wrapper's
  own, and the body of the mail) and `/var/log/snapraid/` (each snapraid command's).
- **Parameters reach those scripts through one rendered file and only through it**:
  `/opt/storagebaby/maintenance/maintenance.env`, which every script sources. No threshold,
  percentage, pool path or address is rendered _into_ a script — so a change touches one
  file, and running `bash /opt/storagebaby/maintenance/sync.sh` by hand behaves exactly like
  the timer's run. A static test asserts that none of the shipped scripts contains a template
  expression at all.
- **`stop_services`** is the plugin set. It replaces the hand-written
  `snapraid/storage-maintenance/plugins/` directory: the role creates
  `plugins/<service>/` for each declared name and drops the same two scripts into it —
  `on-before-balance.sh` (stop) and `on-after-scrub.sh` plus `on-failure.sh` (start). The
  failure copy is the half that matters: a run that aborts after stopping a service must not
  leave it down. Those scripts read the service name off **their own directory**, so they
  carry no rendered value and are identical on every host, and they resolve
  `<name>-pod.service` against `<name>.service` the way `make stop SERVICE=` does. A
  directory for a service no longer in the list is **removed**: the orchestrator globs
  `plugins/*/<hook>.sh` and would otherwise keep stopping it every night.
- **The timer** is `storage-maintenance.timer`, `OnCalendar` from the block and
  `Persistent=true`, enabled and started; the role restarts it when the unit changed, because
  systemd keeps running the schedule it read at load time. `storage-maintenance.service` is a
  oneshot that the role never starts — starting it _is_ the nightly run.
- **`/etc/msmtprc`** is 0600 root and carries the relay from `mail` plus `smtp_password` out
  of `hosts/<host>/secrets/mail.sops.yaml`, decrypted on the host under `no_log`. `tls`
  defaults to on and `auth` to `on`; a test host points at a loopback sink and says
  `tls: false, auth: plain`, because with `auth on` and no TLS msmtp considers only SCRAM and
  would never authenticate. The wrapper composes with `mutt` and names msmtp explicitly as
  its `sendmail` — the host runs no MTA, so mutt's default path does not exist.

**Operator items on storagebaby:** `storage.mail.smtp_host` and `smtp_user` are `REPLACE_ME`,
and `hosts/storagebaby/secrets/mail.sops.yaml` holds `smtp_password: REPLACE_ME`. The relay
was never captured in this repository — the old wrapper used whatever MTA the host happened
to have — so all three have to be filled in before the first converge. Until they are, the
maintenance run succeeds and its mail step fails, nightly.

### `mergerfs-tools`, and why its pin works differently

`balance_disks.sh` calls `mergerfs.balance`, which ships in `mergerfs-tools` — in neither
Arch's repositories nor Chaotic-AUR, so it is built by the same `aur_build.yml`. The AUR
package is `mergerfs-tools-git`: a VCS recipe, whose `pkgver()` runs at build time and
produces a version from whatever upstream commit it checked out. That version is never the
one in `.SRCINFO`, so the "is the pinned version installed" guard could never match and the
package would be rebuilt and reinstalled on every converge — nightly, from the deploy timer.

So `aur_version: ''` says "this recipe versions itself", and the build is guarded on a stamp
instead: `/var/lib/aurbuild/stamps/<package>`, written after a successful install, holding the
AUR commit it was built from. Bumping `mergerfs_tools_aur_commit` is what rebuilds it.
snapraid keeps the version guard, which is the stronger check — it also catches a pin bumped
without its version.
