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

`storage.snapraid` and `storage.mail` belong to the same block and are Task 2's.

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
  for `snapraid`; Task 2 adds `mergerfs-tools-git`.
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

**What the rest of the playbook does under `--check` is a different matter, and the run
does not finish green today.** The `service` role has the idiom this role was fixed for —
`service/tasks/secrets.yml` registers a `sops --decrypt` command and parses its `stdout`,
and check mode skips a `command` — so the run aborts at the first service's secrets with a
`from_json` error, **after** the storage diff has been printed. That failure says nothing
about storage. Until the `service` role carries `check_mode: false` too, read the pre-flight
for what it shows above that point and do not expect `failed=0`.

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
