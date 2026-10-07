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
Disk d2 is declared in hosts/StorageBaby/host.yml with device
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
  no change. `bootstrap.sh` does the same thing, with the same key, the same package URLs
  and the same `blockinfile` marker lines, **before** its one `pacman -Syu` — so a fresh
  host's upgrade already knows the repository, and the first converge finds its own markers
  and changes nothing. `tests/static/test_bootstrap.py` holds the two copies in agreement.
- **Package upgrades are the operator's job, not this role's.** It never runs `pacman -Syu`
  and never reboots. What it does run is one `-Sy` database refresh, and only on the
  converge that changed the repository list — a host whose packages are stale is a host its
  operator has not upgraded, which is a decision, not drift. The only full upgrade in this
  repository is `bootstrap.sh`'s, once, on a host that has nothing running on it yet.
- **Everything Chaotic-AUR does not carry: built from the AUR, here, pinned.**
  `tasks/aur_build.yml` takes `aur_package`, `aur_commit` and `aur_version`, and is one
  block under one condition — the installed version is **older** than the pinned
  `pkgver-pkgrel`, asked of `vercmp`, pacman's own comparison — so a converged host does no
  clone, no compile and no network. The direction matters: a host whose snapraid is _newer_
  than the pin (the operator's own `pacman -Syu`, or Arch shipping it one day) keeps it, and
  the converge says so in a line naming both versions. Nothing here downgrades the program
  that owns a live array's parity; a version moves forward by bumping the pin. It clones the
  AUR package at the pinned commit **as an unprivileged build user** (`aurbuild`, home under
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
affected services first (`storagebaby-svc stop <name>`) and start them after.

This role is check-mode clean: every read-only probe carries `check_mode: false` so its
result is still there for the conditional that reads it, and every step that installs
something is skipped, so a `--check` run reports what it would install and installs
nothing. The mount-point assertion is skipped in check mode too (nothing was mounted), so
a `--check` run on a fresh host shows the units it would write, not whether they mount.

**The whole playbook is check-mode clean, with one limit.** A `--check --diff` run of
`ansible/playbook.yml` ends `failed=0` on a host that has never converged as well as on
one that has: `prepare.yml` runs it on the fresh test VM before the first converge and
fails the scenario otherwise, and `tests/integration/molecule/test-ci/tests/test_deploy.py`
runs it again on the converged host. The limit: the `service` role is skipped in check
mode for a service whose user does not exist yet, because nothing of that service can be
probed before its first converge. So on a never-converged host the pre-flight covers
`host_base`, this role and nothing else; on a converged host it covers everything.

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
open, and Samba is retired only **after** that converge — `smbd` serves `/pool/shared/*`
the whole time until it is. So: stop every service with a pool-class volume,
`systemctl stop smb nmb`, converge, then start them again. The root `README.md`'s operator
steps carry the same list, in the order they have to be run in.

Script and configuration changes need no restart.

## Adding a disk

The role does not create the filesystem, so a new disk is prepared once by hand and then
declared. Four steps, and the third is the only one that is committed:

1. **Partition and format it, on the host.** One partition over the whole disk, GPT, and
   `ext4` on it — the same shape every declared disk has today:

   ```bash
   doas parted -s /dev/sdX mklabel gpt mkpart primary ext4 0% 100%
   doas mkfs.ext4 /dev/sdX1
   ```

2. **Read its stable path.** Never `/dev/sdX` — that is enumeration order, and it changes:

   ```bash
   lsblk -o NAME,SIZE,PARTUUID,PARTLABEL
   ```

   `/dev/disk/by-partuuid/<the partuuid>` is what goes into the declaration.

3. **Declare it** in `hosts/<host>/host.yml` under `storage.disks` (or `storage.parity`) —
   a `name`, the `device` from step 2, a `mount` under `/mnt/data/`, `fstype: ext4` — and
   push. The role mounts it, adds it to the pool's branch list and gives it its `disk` and
   `content` lines in `snapraid.conf`; the next nightly run's `snapraid sync` writes that
   content file and folds the disk into the array.
4. **Expect the remount.** A new branch changes `pool.mount`, so the converge that adds a
   disk takes the pool down and back up — "Change handling" above is the whole of it. Run
   the pre-flight, stop every pool-class service first, start them afterwards.

The disk's `name` is snapraid's identity for it: choosing one that is already in use, or
renaming one later, makes snapraid treat the whole disk as new and rewrite parity.

## The array, the nightly run and its mail

The same `storage` block carries the rest of it.

```yaml
storage:
  snapraid:
    block_size: 256
    excludes:
      ['*.bak', /lost+found/, /apps/nextcloud/data/jlk/files/…/build/, …]
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
    # tls: true       (default)
    # starttls: true  (default; false for an smtps:// relay on 465)
    # auth: on        (default)
```

- **`/etc/snapraid.conf`** is rendered from `disks`, `parity` and `snapraid`: one `content`
  line per data disk plus `/etc/snapraid.content` on the root filesystem, one parity line per
  `parity` entry in list order (`parity`, `2-parity`, …), one `disk <name> <mount>/` per data
  disk, the `excludes` and the `block_size`. Nothing restarts when it changes — snapraid is
  run by a timer and reads the file every time.
- **`snapraid.excludes` are paths relative to a _disk's_ root**, which for a branch of this
  pool is the same as relative to `pool.mount`. That is why the Nextcloud entry is
  `/apps/nextcloud/data/…`: the pool-class volume `data` of the `nextcloud` service.
- **The disk `name` is snapraid's identity for that disk.** Renaming one makes snapraid treat
  the whole disk as new, which is a full parity rewrite on the next sync.
- **`/opt/storagebaby/maintenance/`** holds six scripts, all 0755 root and **copied byte for
  byte** from the role's `files/maintenance/`: the orchestrator, `sync.sh`, `scrub.sh`,
  `balance_disks.sh`, the unattended wrapper, and `storage-maintenance-failed.sh` — the one
  `storage-maintenance-failed.service` execs on `OnFailure=`, which is the only way a pool
  that will not mount is reported at all. Logs go to `/var/log/storage-maintenance/` (the wrapper's
  own, and the body of the mail) and `/var/log/snapraid/` (each snapraid command's).
- **Parameters reach those scripts through one rendered file and only through it**:
  `/opt/storagebaby/maintenance/maintenance.env`, which every script sources. No threshold,
  percentage, pool path or address is rendered _into_ a script — so a change touches one
  file, and running `bash /opt/storagebaby/maintenance/sync.sh` by hand behaves exactly like
  the timer's run. A static test asserts that none of the shipped scripts contains a template
  expression at all.
- **`stop_services`** is the plugin set. It replaces the hand-written `plugins/` directory
  the retired `snapraid/` tree carried, one script per service per hook: the role creates
  `plugins/<service>/` for each declared name and drops the same two scripts into it —
  `on-before-balance.sh` (stop) and `on-after-scrub.sh` plus `on-failure.sh` (start). The
  failure copy is the half that matters: a run that aborts after stopping a service must not
  leave it down. Those scripts read the service name off **their own directory**, so they
  carry no rendered value and are identical on every host, and they resolve
  `<name>-pod.service` against `<name>.service` the way `storagebaby-svc` does. A
  directory for a service no longer in the list is **removed**: the orchestrator globs
  `plugins/*/<hook>.sh` and would otherwise keep stopping it every night.
- **The timer** is `storage-maintenance.timer`, `OnCalendar` from the block and
  `Persistent=true`, enabled and started; the role restarts it when the unit changed, because
  systemd keeps running the schedule it read at load time. `storage-maintenance.service` is a
  oneshot that the role never starts — starting it _is_ the nightly run.
- **`storage-maintenance-failed.service` is the mail a blocked run cannot send itself.**
  The maintenance unit `Requires=` the pool, which is not negotiable: a `snapraid sync` over
  a branch that is not mounted reads an empty directory and writes that into parity. The
  price is that a pool which does not come up fails the unit's _start job_ — `ExecStart=`
  never runs, so the wrapper never runs, so nothing arrives at 02:00 and the one failure
  that matters most is the silent one. So the unit carries
  `OnFailure=storage-maintenance-failed.service`: a oneshot that mails the unit name and a
  `systemctl status` excerpt through the same msmtp. systemd triggers `OnFailure=` on a
  start job that failed on a dependency as well as on one that failed outright. The role
  renders and reloads it and then leaves it alone — nothing but the failing unit may start
  it. A run that is _killed_ — a systemd stop, or a timeout mid-sync — fails the unit too,
  and there the wrapper is in the same cgroup and goes down with it, so it never reaches its
  own `send_email` either: the notifier is the only mail that night in that case as well.
  (The orchestrator's `on-failure` hooks still run: they are fired from its own
  `trap … INT TERM`, which is what brings a stopped service back up.)
- **`/etc/msmtprc`** is 0600 root and carries the relay from `mail` plus `smtp_password` out
  of `hosts/<host>/secrets/mail.sops.yaml`, decrypted on the host under `no_log`. `tls`
  defaults to on and `auth` to `on`; `starttls` defaults to on as well, and a relay that
  only speaks implicit TLS (`smtps://` on 465, storagebaby's) sets it to false beside
  `smtp_port: 465`. A test host points at a loopback sink and says
  `tls: false, auth: plain`, because with `auth on` and no TLS msmtp considers only SCRAM and
  would never authenticate. The wrapper composes with `mutt` and names msmtp explicitly as
  its `sendmail` — the host runs no MTA, so mutt's default path does not exist.
- **msmtp logs to the journal** (`syslog LOG_MAIL`), not to a `logfile` of its own: that file
  is created on the first send and then grows for the life of the host with nothing rotating
  it. `journalctl -t msmtp` is where a refused relay or a rejected sender shows up.

**Operator items on storagebaby:** `storage.mail.smtp_host` and `smtp_user` are `REPLACE_ME`,
and `hosts/StorageBaby/secrets/mail.sops.yaml` holds `smtp_password: REPLACE_ME`. The relay
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

**So `mergerfs_tools_aur_commit` pins the recipe, not the source.** `pkgver()` checks out
upstream `master` at build time, and the stamp records only the AUR commit — two rebuilds
months apart can therefore install different `mergerfs.balance` code with the role reporting
nothing. There is no versioned package to prefer instead: the AUR carries `mergerfs`,
`mergerfs-bin`, `mergerfs-git` and `mergerfs-tools-git`, and no `mergerfs-tools`
(checked through the AUR RPC, 2026-09-29). Upstream has cut no release of the tools either,
which is why the recipe is a `-git` one at all. If that drift ever matters, the way out is
the one `devtools/Dockerfile` takes for snapraid: build a pinned tarball instead of a recipe.
