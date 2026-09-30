# Phase 4: Storage Roles, Samba Retirement and FTP through Traefik — Implementation Plan

> **What was built is recorded in the spec, not here.** Eleven amendments came out of the
> implementation and every one of them is in
> `../specs/2026-09-25-phase-4-storage-and-ftp-design.md`, §9 "Amendments". Where this plan and
> that list disagree -- `config.ftp_public_address` (it became `tcp_bind_address`), the test
> disks' size (4 GB, not 1), the AUR build route, the image pre-pull -- the list is what happened.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the storage half of a host into git — disk and parity mounts, the mergerfs pool, snapraid, the nightly maintenance run and its mail — give the platform TCP routing so a scanner delivers by FTP straight into Paperless's consume directory, and retire `samba/`, `snapraid/`, the snapshot plugins and `paperless-upload`. After this phase nothing on a host is hand-stowed.

**Architecture:** One new role, `ansible/roles/storage`, included by the playbook **before** `host_base` and skipped on a host whose `host.yml` declares no `storage` block. It renders `.mount` units from the declaration, mounts the mergerfs pool over them, writes `/etc/snapraid.conf`, installs the maintenance scripts under `/opt/storagebaby/maintenance/` with a timer, and configures `msmtp` from the host's `mail` secret. It **never** partitions, formats or wipes. The `service` role gains one feature: `tcp_ports`, a per-service list of plain-TCP ports Traefik listens on and forwards to loopback, which is what carries FTP. Paperless gains a `paperless-ftp` part and a `consume` volume; `paperless-upload` goes.

**Tech Stack:** As before. New: systemd `.mount` units, mergerfs (Chaotic-AUR), snapraid and `mergerfs-tools-git` (built from the AUR by the role, pinned by AUR commit), msmtp, Traefik TCP entrypoints and routers, `pure-ftpd` in the paperless pod.

**Spec:** `docs/superpowers/specs/2026-09-25-phase-4-storage-and-ftp-design.md`

## Global Constraints

- **The role never formats a disk.** No `mkfs`, `wipefs`, `parted`, `sgdisk`, `snapraid fix` in any role task, ever. Partitioning is a documented one-time hand step; the role mounts what `host.yml` declares and fails, naming the entry, when a declared device is absent. The only `parted`/`mkfs` in this repo is in the Molecule `prepare` play, against the VM's throwaway virtual disks.
- Tests never use real data or secrets. Test hosts generate every secret, `mail.sops.yaml` included. storagebaby's unknown values are the literal `REPLACE_ME`, listed in the README operator steps.
- Rootless only, one user per service, loopback publishing only, nothing privileged, no hostname in roles/templates/tests/tasks (the host name reaches templates only as `hostname`).
- Commands via `make` only — never docker/virsh/molecule/pytest directly, never a pacman on the workstation. Verification is `make test-static`, `make test-integration`, `MOLECULE_HOST=test-ci make test-integration`, `make molecule CMD=...`, `make molecule-exec CMD='...'`.
- Commit per task on `podman-platform` (standing permission); do not push. One concern per commit. Every commit message ends with:

  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  ```

- Static tests enumerate the filesystem (`Path.glob`, `rglob`), never `git ls-files`: this is a worktree, and git inside the devtools container cannot resolve its `.git` pointer.
- Ansible idioms as in the existing roles: directories that hold data are **create-only** (`stat` then `file:` guarded by `when: not stat.exists`), `no_log: true` on every task that carries a decrypted secret, `find` with `follow: true` for symlinked service directories, the per-host `service_config` merge stays in the playbook (an `include_role` param outranks any `set_fact` the role could make), `| bool` on any conditional fed from a `set_fact` or `-e`.
- Quadlet conventions stay enforced by `tests/static/test_quadlet_conventions.py`: `HealthCmd=`, `HealthOnFailure=kill`, `Restart=always`, `ContainerName=` on every container; `PublishPort=` only on the pod and only `127.0.0.1:`; no `AddHost=…:host-gateway` in any service template.
- **VM memory unchanged** (`MOLECULE_VM_MEMORY_MIB` defaults 12288, CI 5120). The VM gains four 1 GB disks and nothing else.
- `ansible-lint --offline ansible/` stays clean (`tests/static/test_lint.py`), and `make fmt-check` stays green — prettier formats the YAML, Markdown and shell this plan adds.

---

### Task 1: The `storage` contract, the mergerfs pool and the test VM's disks

Spec §2, §3 (1–4), §6 harness, §7 step 1. Mounts and pool only — snapraid, maintenance and mail are Task 2.

**Files:**

- Create: `tests/static/test_storage.py`
- Modify: `tests/static/conftest.py` (host-config helpers), `tests/static/test_hosts.py` (`mountpoints` retired), `tests/static/test_render.py` (the storage render output)
- Create: `ansible/roles/storage/defaults/main.yml`, `ansible/roles/storage/tasks/{main,tools,aur_build,mounts,render}.yml`, `ansible/roles/storage/templates/{disk.mount.j2,pool.mount.j2}`, `ansible/roles/storage/README.md`
- Modify: `ansible/playbook.yml` (include the role before `host_base`)
- Modify: `ansible/roles/host_base/tasks/main.yml` (drop the `mountpoints` check)
- Modify: `hosts/storagebaby/host.yml`, `hosts/test-a/host.yml`, `hosts/test-ci/host.yml`
- Modify: `tests/integration/molecule/test-ci/molecule.yml`, `create.yml`, `destroy.yml`, `prepare.yml`, `tests/integration/molecule/templates/domain.xml.j2`
- Create: `tests/integration/molecule/test-ci/tests/test_storage.py`

**Interfaces later tasks rely on:**

- `host.yml` key `storage` with `storage.disks[]`, `storage.parity[]` (each `{name, device, mount, fstype}`) and `storage.pool.{mount,options}`. Task 2 adds `storage.snapraid` and `storage.mail` to the same block.
- `storage.disks[].name` is the disk's identity: it is what `snapraid.conf` will call the disk in Task 2 **and** the GPT partition label the harness writes, so storagebaby's are `d1`, `d2`, `d3` (what the live array already knows) and the parity one is `parity1`.
- Role facts: `storage_devices` (`disks + parity`), `pool_unit` (`pool.mount`'s unit name). Unit name of any declared mount: `{{ m.mount | regex_replace('^/', '') | regex_replace('/', '-') }}.mount`.
- Render output for the storage role: `<render_output>/storage/` — `test_render.py` and Task 2's checks read it there.
- Test-host python helpers: `conftest.host_cfg(host)`, `conftest.storage_of(host)`.
- Harness: `storage_disk_count: 4` in `create.yml`/`destroy.yml`, disks named `<vm>-storage<n>.qcow2`, attached as `vdb`…`vde`; `prepare.yml` labels partition _n_ with the _n_-th declared disk's `name`.

- [ ] **Step 1: Static tests first**

`tests/static/conftest.py`, append:

```python
def host_cfg(host: str) -> dict:
    return load_yaml(HOSTS / host / "host.yml")


def storage_of(host: str) -> dict | None:
    """The host's `storage` block, or None on a host that declares none."""
    return host_cfg(host).get("storage")


def mount_unit(path: str) -> str:
    """The systemd unit name of a mount point, the way systemd-escape spells it.

    The naive escape is exact only because `test_storage` restricts a declared mount
    path to lowercase letters, digits and slashes: a `-`, `.` or `_` in the path would
    need `\\x2d`-style escaping and this would silently produce the wrong unit name.
    """
    return path.lstrip("/").replace("/", "-") + ".mount"
```

`tests/static/test_hosts.py`: drop `"mountpoints"` from `REQUIRED`, drop the two `mountpoints` assertions, and add:

```python
def test_mountpoints_is_retired(host):
    cfg = load_yaml(HOSTS / host / "host.yml")
    assert "mountpoints" not in cfg, (
        f"{host}: `mountpoints` is Phase 3's hand-written list. The `storage` role "
        "asserts every mount it declares itself; declare `storage` instead."
    )
```

`tests/static/test_storage.py`, new:

```python
import re
import subprocess

import pytest

from conftest import HOSTS, REPO, host_cfg, host_names, mount_unit, storage_of

STORAGE_KEYS = {"disks", "parity", "pool", "snapraid", "mail"}
DISK_KEYS = {"name", "device", "mount", "fstype"}
POOL_KEYS = {"mount", "options"}
NAME_RE = re.compile(r"^[a-z][a-z0-9]*$")
# Lowercase letters, digits and slashes only: `mount_unit` escapes a path by replacing
# every slash with a dash, which is systemd's own escape *only* for a path that carries
# none of the characters systemd would hex-escape. A `/mnt/data-1` would render
# `mnt-data-1.mount`, a unit systemd reads as `/mnt/data/1`.
MOUNT_RE = re.compile(r"^(/[a-z0-9]+)+$")
FSTYPES = {"ext4", "xfs"}

STORAGE_HOSTS = [h for h in host_names() if storage_of(h)]


@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_storage_block_shape(host):
    storage = storage_of(host)
    assert set(storage) <= STORAGE_KEYS, f"{host}: unknown storage keys {set(storage) - STORAGE_KEYS}"
    assert storage["disks"], f"{host}: a storage block needs at least one disk"
    entries = storage["disks"] + storage.get("parity", [])
    for e in entries:
        assert set(e) == DISK_KEYS, f"{host}/{e.get('name')}: a disk needs exactly {sorted(DISK_KEYS)}"
        assert NAME_RE.match(e["name"]), f"{host}: invalid disk name {e['name']!r}"
        assert e["device"].startswith("/dev/"), f"{host}/{e['name']}: device must be under /dev"
        assert MOUNT_RE.match(e["mount"]), f"{host}/{e['name']}: {e['mount']!r} is not a plain lowercase path"
        assert e["fstype"] in FSTYPES, f"{host}/{e['name']}: fstype {e['fstype']!r}"
    names = [e["name"] for e in entries]
    assert len(set(names)) == len(names), f"{host}: duplicate disk names {names}"
    mounts = [e["mount"] for e in entries]
    assert len(set(mounts)) == len(mounts), f"{host}: duplicate mount points {mounts}"
    pool = storage["pool"]
    assert set(pool) == POOL_KEYS, f"{host}: pool takes exactly {sorted(POOL_KEYS)}"
    assert MOUNT_RE.match(pool["mount"]), f"{host}: pool mount {pool['mount']!r}"
    assert pool["mount"] not in mounts, f"{host}: the pool cannot be mounted on a branch"
    # Every branch is a *data* disk: parity is not part of the union, it holds the
    # parity file. A pool over the parity disk would hand mergerfs its own redundancy.
    assert "category.create=" in pool["options"], f"{host}: pool options declare no create policy"


@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_storage_roots_live_under_the_pool(host):
    """The `pool` class root is inside the union, which is what the mount assertion protects.

    `host_base` creates every storage root with `file: state=directory`, which creates
    each missing parent on the way -- so a pool root outside the declared pool, or a
    pool that is not asserted, is how an unmounted filesystem becomes an empty tree on
    the root disk, unattended, from the nightly deploy timer.
    """
    cfg = host_cfg(host)
    pool_mount = cfg["storage"]["pool"]["mount"]
    root = cfg["storage_roots"]["pool"]
    assert root.startswith(pool_mount.rstrip("/") + "/"), f"{host}: storage_roots.pool {root} is not under {pool_mount}"


@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_rendered_mount_units_verify(rendered, host):
    """Every rendered `.mount` passes `systemd-analyze verify`.

    Run over the whole directory at once, not file by file: `pool.mount` requires every
    disk unit, and verify reports a requirement it cannot find as an error.
    """
    unit_dir = rendered / host / "storage"
    storage = host_cfg(host)["storage"]
    expected = {mount_unit(e["mount"]) for e in storage["disks"] + storage.get("parity", [])}
    expected.add(mount_unit(storage["pool"]["mount"]))
    assert expected <= {f.name for f in unit_dir.iterdir()}, sorted(f.name for f in unit_dir.iterdir())
    units = sorted(str(unit_dir / name) for name in expected)
    r = subprocess.run(["systemd-analyze", "verify", *units], capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_pool_unit_unions_the_data_disks(rendered, host):
    storage = host_cfg(host)["storage"]
    text = (rendered / host / "storage" / mount_unit(storage["pool"]["mount"])).read_text()
    assert f"What={':'.join(d['mount'] for d in storage['disks'])}" in text, text
    assert "Type=fuse.mergerfs" in text, text
    assert f"Options={storage['pool']['options']}" in text, text
    # Requires= every declared mount, parity included: the parity file is written by the
    # maintenance run, and a pool that outlives its parity disk hides that it is gone.
    for e in storage["disks"] + storage.get("parity", []):
        assert mount_unit(e["mount"]) in text, f"{host}: pool does not require {e['name']}"
```

`tests/static/test_render.py`: `expected_units` is unchanged (it is per service), but the session `rendered` fixture now also produces `<host>/storage/`. Nothing to change there — the fixture runs the whole playbook. Add nothing; `test_storage.py` imports the same fixture, so move `rendered` into `conftest.py` unchanged and import it in both files (`from conftest import ...` does not carry fixtures — define `rendered` in `conftest.py` and delete it from `test_render.py`).

Run `make test-static`: `test_storage.py` fails (no `storage` block anywhere yet, so it collects nothing) and `test_hosts.py::test_mountpoints_is_retired` fails on all three hosts. That is the RED.

- [ ] **Step 2: `host.yml` on all three hosts**

`hosts/storagebaby/host.yml` — replace the `mountpoints: [/pool]` block (comment included) with the declaration, copied from the units this phase retires (`snapraid/config/etc/systemd/system/*.mount`). The partuuids and the parity `by-id` path are taken verbatim from those files; the names are what `snapraid.conf` already calls the disks:

```yaml
# The filesystems this host is made of. The `storage` role mounts exactly these and
# refuses to converge when one of them is missing -- which is what `mountpoints` used
# to do by hand. It never partitions, formats or wipes: a new disk is prepared once by
# hand (README, "Adding a disk") and then declared here.
storage:
  disks:
    - {
        name: d1,
        device: /dev/disk/by-partuuid/a36d7f0a-aa65-4214-98e0-f3818954a494,
        mount: /mnt/data/data1,
        fstype: ext4
      }
    - {
        name: d2,
        device: /dev/disk/by-partuuid/243e4d8c-d018-4b9a-af9d-dcd3626138d4,
        mount: /mnt/data/data2,
        fstype: ext4
      }
    - {
        name: d3,
        device: /dev/disk/by-partuuid/d3b1a8bb-3da9-48dc-a082-2603cf186444,
        mount: /mnt/data/data3,
        fstype: ext4
      }
  parity:
    - {
        name: parity1,
        device: /dev/disk/by-id/ata-WDC_WD40EFRX-68N32N0_WD-WCC7K2EYYVV1-part1,
        mount: /mnt/parity/parity1,
        fstype: ext4
      }
  pool:
    mount: /pool
    options: defaults,allow_other,use_ino,category.create=pfrd,moveonenospc=true,minfreespace=20G,fsname=mergerfsPool,cache.writeback=false,cache.symlinks=false,cache.readdir=false,async_read=true,threads=4
```

`hosts/test-a/host.yml` and `hosts/test-ci/host.yml` — identical block in both, and `storage_roots.pool` moves onto the pool:

```yaml
storage_roots:
  pool: /pool/apps
  fast: /srv/fast
# Four 1 GB virtual disks the Molecule harness attaches, partitions and labels: the
# device paths are GPT partition labels it writes from these very `name`s, so the
# tracked file names the disks and the harness makes those names exist. `minfreespace`
# is the one option that cannot be storagebaby's: 20G on a 1 GB branch means every
# create fails.
storage:
  disks:
    - {
        name: d1,
        device: /dev/disk/by-partlabel/d1,
        mount: /mnt/data/data1,
        fstype: ext4
      }
    - {
        name: d2,
        device: /dev/disk/by-partlabel/d2,
        mount: /mnt/data/data2,
        fstype: ext4
      }
    - {
        name: d3,
        device: /dev/disk/by-partlabel/d3,
        mount: /mnt/data/data3,
        fstype: ext4
      }
  parity:
    - {
        name: parity1,
        device: /dev/disk/by-partlabel/parity1,
        mount: /mnt/parity/parity1,
        fstype: ext4
      }
  pool:
    mount: /pool
    options: defaults,allow_other,use_ino,category.create=pfrd,moveonenospc=true,minfreespace=100M,fsname=mergerfsPool,cache.writeback=false,cache.symlinks=false,cache.readdir=false,async_read=true,threads=2
```

**Why the tracked file carries a device path at all, and what the harness supplies.**
The device path has to be resolvable by a converge the harness is not driving: `test_deploy.py` runs `storagebaby-deploy.service`, which is `ansible-pull` against the seeded git remote with no Molecule inventory anywhere near it. A device that existed only in a generated inventory variable would make that converge render a _different_ `What=` and remount `/pool` under the running services' bind mounts. So the stable name lives in git as a `by-partlabel` path, and `prepare.yml` supplies the device by writing that very label onto the partition it creates — the same contract the spec describes, with the indirection resolved at partition time instead of at converge time. Static tests need nothing special: they render the tracked path like any other string.

- [ ] **Step 3: The role**

`ansible/roles/storage/defaults/main.yml`:

> **Amended 2026-09-26 (JLK's ruling, ledger).** Neither mergerfs nor snapraid comes
> from an upstream artefact unpacked under `/opt`. mergerfs comes from **Chaotic-AUR**, a
> signed binary repository of AUR builds that the role configures the project's own
> documented way. Packages Chaotic-AUR does not carry — `snapraid`, and
> `mergerfs-tools-git` in Task 2 — are **built from the AUR by the role**: a system build
> user, `base-devel`, one clone at a pinned AUR commit, `makepkg`, `pacman -U`. The
> reusable task file is `tasks/aur_build.yml` (`aur_package`, `aur_commit`,
> `aur_version`). The blocks below are the amended ones; the plan's original
> static-build/`pacman -U`-a-release text is superseded.

```yaml
# Where the storage tools come from, pinned and bumped deliberately, exactly like a
# container image tag.
#
# mergerfs is not in Arch's official repositories. It *is* in Chaotic-AUR, a signed
# binary repository of AUR builds, so the role configures that repository the project's
# documented way and installs the package -- no compiler, no AUR helper, no upstream
# tarball unpacked under /opt.
chaotic_key_id: 3056513887B78AEB
chaotic_keyserver: keyserver.ubuntu.com
chaotic_packages:
  - https://cdn-mirror.chaotic.cx/chaotic-aur/chaotic-keyring.pkg.tar.zst
  - https://cdn-mirror.chaotic.cx/chaotic-aur/chaotic-mirrorlist.pkg.tar.zst

# snapraid is in neither, so `aur_build.yml` builds it from the AUR. The commit is the
# pin; the version is what that commit produces, and the role asserts the two agree
# before it builds -- so a bumped commit with a stale version fails loudly instead of
# reinstalling the same package every night.
aur_build_user: aurbuild
aur_build_home: /var/lib/aurbuild
snapraid_aur_commit: 94c51545d0ce36dee0402dc19c1b3cc4336b3ae9
snapraid_version: 14.9-1
# Rendered into msmtprc when nothing decrypted a real one -- the render-only path the
# static tests use. A converge always overwrites it from hosts/<host>/secrets/mail.sops.yaml.
smtp_password: REPLACE_ME
```

`ansible/roles/storage/tasks/main.yml`:

```yaml
- name: Derive storage facts
  ansible.builtin.set_fact:
    storage_devices: '{{ storage.disks + storage.parity | default([]) }}'
    pool_unit: "{{ storage.pool.mount | regex_replace('^/', '') | regex_replace('/', '-') }}.mount"

- name: Render only
  ansible.builtin.include_tasks: render.yml
  when: render_only | bool

- name: Tools the pool and the array need
  ansible.builtin.include_tasks: tools.yml
  when: not (render_only | bool)

- name: Mounts and the pool
  ansible.builtin.include_tasks: mounts.yml
  when: not (render_only | bool)
```

`ansible/roles/storage/tasks/tools.yml`:

Amended: Chaotic-AUR for mergerfs, `aur_build.yml` for snapraid. Every step is guarded so
a second converge reports no change, and `state: present` (never `latest`) is what makes a
host that already has mergerfs or snapraid from its own AUR builds keep them.

```yaml
# The whole Chaotic-AUR dance is gated on one cheap check: once `chaotic-keyring` is
# installed it owns the repository's keys, so the key import has nothing left to do.
- name: Is Chaotic-AUR already set up on this host?
  ansible.builtin.command: pacman -Qq chaotic-keyring chaotic-mirrorlist
  register: _chaotic_pkgs
  changed_when: false
  failed_when: false

- name: Receive and locally sign the Chaotic-AUR signing key
  ansible.builtin.command: '{{ item }}'
  loop:
    - pacman-key --recv-key {{ chaotic_key_id }} --keyserver {{ chaotic_keyserver }}
    - pacman-key --lsign-key {{ chaotic_key_id }}
  when: _chaotic_pkgs.rc != 0
  changed_when: true

- name: Install the Chaotic-AUR keyring and mirrorlist
  ansible.builtin.command: pacman -U --noconfirm {{ chaotic_packages | join(' ') }}
  when: _chaotic_pkgs.rc != 0
  changed_when: true

- name: The [chaotic-aur] repository in pacman.conf
  ansible.builtin.blockinfile:
    path: /etc/pacman.conf
    marker: '# {mark} ANSIBLE MANAGED chaotic-aur'
    block: |
      [chaotic-aur]
      Include = /etc/pacman.d/chaotic-mirrorlist
  register: _chaotic_conf

- name: Refresh the package databases when the repository list changed
  community.general.pacman:
    update_cache: true
  when: _chaotic_conf.changed

- name: mergerfs, from Chaotic-AUR
  community.general.pacman:
    name: [mergerfs]
    state: present

# mergerfs ships its own mount helper as `mount.mergerfs`; `Type=fuse.mergerfs` makes
# mount(8) look for `mount.fuse.mergerfs` first. Linked only when absent, so nothing is
# laid over a path a package may come to own -- and mergerfs parses the helper argv
# under either name.
- name: Link mergerfs's mount helper under the subtype name
  ansible.builtin.file:
    src: /usr/bin/mount.mergerfs
    dest: /usr/bin/mount.fuse.mergerfs
    state: link
  when: not _fuse_helper.stat.exists

- name: snapraid, built from the AUR
  ansible.builtin.include_tasks: aur_build.yml
  vars:
    aur_package: snapraid
    aur_commit: '{{ snapraid_aur_commit }}'
    aur_version: '{{ snapraid_version }}'
```

`ansible/roles/storage/tasks/aur_build.yml` — one AUR package, built on the host. The
whole file is one `block` under `when: the pinned version is not installed`, so a
converged host does no git, no `makepkg` and no network. The build user gets **no** sudo
or doas rights: `makepkg -s` would need pacman as root, and a NOPASSWD pacman rule for a
service account is a root-equivalent grant that outlives the build — so the dependencies
are read out of `.SRCINFO` and installed by the play, which is already root, and
`makepkg` runs without `-s`. Every git command runs _as_ the build user, so root never
touches a repository it does not own (`detected dubious ownership`).

`ansible/roles/storage/tasks/mounts.yml`:

```yaml
- name: Mount point directories
  ansible.builtin.file:
    path: '{{ item.mount }}'
    state: directory
    owner: root
    group: root
    mode: '0755'
  loop: '{{ storage_devices + [storage.pool] }}'
  loop_control:
    label: '{{ item.mount }}'

# The precondition the role exists to check. A device that is not there is a disk that
# has been pulled, renamed or has failed to enumerate -- and mounting *nothing* there
# and carrying on is how a pool comes up short a branch and a converge writes service
# data onto the root filesystem.
- name: Look for every declared device
  ansible.builtin.stat:
    path: '{{ item.device }}'
    follow: true
  loop: '{{ storage_devices }}'
  loop_control:
    label: '{{ item.name }} -> {{ item.device }}'
  register: _declared_devices

- name: Every declared device is present
  ansible.builtin.assert:
    that: item.stat.exists
    fail_msg: >-
      Disk {{ item.item.name }} is declared in hosts/{{ inventory_hostname }}/host.yml
      with device {{ item.item.device }}, and nothing is there. Refusing to converge:
      the role does not create, partition or format a disk. Attach it, check
      `lsblk -o NAME,SIZE,PARTUUID,PARTLABEL`, then converge again.
    quiet: true
  loop: '{{ _declared_devices.results }}'
  loop_control:
    label: '{{ item.item.name }}'

- name: Render the disk and parity mount units
  ansible.builtin.template:
    src: disk.mount.j2
    dest: "/etc/systemd/system/{{ item.mount | regex_replace('^/', '') | regex_replace('/', '-') }}.mount"
    owner: root
    group: root
    mode: '0644'
  loop: '{{ storage_devices }}'
  loop_control:
    label: '{{ item.name }}'
  register: disk_units

- name: Render the pool mount unit
  ansible.builtin.template:
    src: pool.mount.j2
    dest: '/etc/systemd/system/{{ pool_unit }}'
    owner: root
    group: root
    mode: '0644'
  register: pool_unit_render

- name: Reload systemd for changed mount units
  ansible.builtin.systemd:
    daemon_reload: true
  when: disk_units.changed or pool_unit_render.changed

- name: Disk and parity mounts enabled and mounted
  ansible.builtin.systemd:
    name: "{{ item.mount | regex_replace('^/', '') | regex_replace('/', '-') }}.mount"
    enabled: true
    state: mounted
  loop: '{{ storage_devices }}'
  loop_control:
    label: '{{ item.name }}'

- name: Restart a disk mount whose unit changed
  ansible.builtin.systemd:
    name: "{{ item.item.mount | regex_replace('^/', '') | regex_replace('/', '-') }}.mount"
    state: restarted
  loop: '{{ disk_units.results }}'
  loop_control:
    label: '{{ item.item.name }}'
  when: item.changed

# Loud, and before the restart rather than after it: remounting the union drops every
# bind mount a running container holds under it, so the services on this host come back
# reading an empty directory until they are restarted. The README says to run the
# playbook with `--check --diff` before pushing a pool option change.
- name: Warn that the pool is about to be remounted
  ansible.builtin.debug:
    msg: >-
      {{ pool_unit }} changed and will be remounted. Every service with a pool-class
      volume loses its bind mount across this and has to be restarted afterwards --
      `make stop SERVICE=<name>` before, `make start SERVICE=<name>` after.
  when: pool_unit_render.changed

- name: The pool enabled and mounted
  ansible.builtin.systemd:
    name: '{{ pool_unit }}'
    enabled: true
    state: mounted

- name: Remount the pool when its unit changed
  ansible.builtin.systemd:
    name: '{{ pool_unit }}'
    state: restarted
  when: pool_unit_render.changed

# `state: mounted` above already fails when it cannot mount, so this is the second half:
# that what is mounted there is really a mount point and not a directory that happens to
# exist. It is what replaces `host_base`'s `mountpoints` list.
- name: Check every declared mount
  ansible.builtin.command: mountpoint -q -- {{ item }}
  loop: "{{ (storage_devices | map(attribute='mount') | list) + [storage.pool.mount] }}"
  register: _mounted
  changed_when: false
  failed_when: false
  check_mode: false

- name: Require every declared mount to be mounted
  ansible.builtin.assert:
    that: item.rc == 0
    fail_msg: >-
      {{ item.item }} is declared in hosts/{{ inventory_hostname }}/host.yml under
      `storage` but nothing is mounted there. Refusing to converge: the storage roots
      below it would be created on the root filesystem instead.
    quiet: true
  loop: '{{ _mounted.results }}'
  loop_control:
    label: '{{ item.item }}'
```

`ansible/roles/storage/templates/disk.mount.j2`:

```jinja
{# One per entry of `storage.disks` and `storage.parity`. The role mounts what the
   host declares and nothing else: it never partitions, formats or wipes a device. #}
[Unit]
Description={{ item.name }} ({{ item.mount }})

[Mount]
What={{ item.device }}
Where={{ item.mount }}
Type={{ item.fstype }}
Options=defaults

[Install]
RequiredBy=local-fs.target
```

`ansible/roles/storage/templates/pool.mount.j2`:

```jinja
{# The mergerfs union over every data disk. Parity is *not* a branch -- it carries the
   parity file -- but it is required, because a pool that outlives its parity disk hides
   that the array is unprotected. #}
[Unit]
Description=mergerfs pool ({{ storage.pool.mount }})
Requires={% for m in storage_devices %}{{ m.mount | regex_replace('^/', '') | regex_replace('/', '-') }}.mount{{ ' ' if not loop.last }}{% endfor %}

After={% for m in storage_devices %}{{ m.mount | regex_replace('^/', '') | regex_replace('/', '-') }}.mount{{ ' ' if not loop.last }}{% endfor %}


[Mount]
What={{ storage.disks | map(attribute='mount') | join(':') }}
Where={{ storage.pool.mount }}
Type=fuse.mergerfs
Options={{ storage.pool.options }}

[Install]
RequiredBy=local-fs.target
```

`ansible/roles/storage/tasks/render.yml`:

```yaml
- name: Render output dir
  ansible.builtin.file:
    path: '{{ render_output }}/storage'
    state: directory
    mode: '0755'

- name: Render the disk and parity mount units
  ansible.builtin.template:
    src: disk.mount.j2
    dest: "{{ render_output }}/storage/{{ item.mount | regex_replace('^/', '') | regex_replace('/', '-') }}.mount"
    mode: '0644'
  loop: '{{ storage_devices }}'
  loop_control:
    label: '{{ item.name }}'

- name: Render the pool mount unit
  ansible.builtin.template:
    src: pool.mount.j2
    dest: '{{ render_output }}/storage/{{ pool_unit }}'
    mode: '0644'
```

- [ ] **Step 4: Playbook and `host_base`**

`ansible/playbook.yml`, replace the `roles:` block:

```yaml
roles:
  # Before host_base, which creates the storage class roots: the pool has to be
  # mounted before anything writes below it. Skipped on a host that declares no
  # storage -- the role is the declaration's converger, not a host requirement.
  - role: storage
    when: storage is defined
  # `| bool`: ansible-core rejects a non-boolean conditional, and an
  # `-e render_only=...` on the command line arrives as a string.
  - role: host_base
    when: not (render_only | bool)
```

`ansible/roles/host_base/tasks/main.yml`: delete "Check the mountpoints declared in host.yml" and "Require every declared mountpoint to be mounted" together with their comment block, and put in their place a one-line pointer above "Storage class roots":

```yaml
# Everything below this point writes into the storage roots, and `file: state=directory`
# creates every missing parent on the way. The precondition -- every declared filesystem
# really mounted -- is asserted by the `storage` role, which the playbook runs first.
```

- [ ] **Step 5: Harness — four disks**

`tests/integration/molecule/test-ci/molecule.yml`, no change needed: the disk count is a property of the scenario, not of the platform. Both `create.yml` and `destroy.yml` get `storage_disk_count: 4` and `storage_disk_size: 1G` in their play `vars`.

`create.yml`, after "Overlay disks":

```yaml
# Plain qcow2, no backing file: these are the storage role's disks, partitioned and
# formatted by `prepare`. 1 GB each is enough for a real mergerfs union, a real
# snapraid parity file and a maintenance run, and adds 4 GB to a 20 GB overlay.
- name: Storage disks
  ansible.builtin.command: >-
    qemu-img create -f qcow2
    {{ images_dir }}/{{ item.0.name }}-storage{{ item.1 }}.qcow2 {{ storage_disk_size }}
  args:
    creates: '{{ images_dir }}/{{ item.0.name }}-storage{{ item.1 }}.qcow2'
  loop: '{{ molecule_yml.platforms | product(range(1, storage_disk_count + 1) | list) | list }}'
  loop_control:
    label: '{{ item.0.name }}-storage{{ item.1 }}'
```

`tests/integration/molecule/templates/domain.xml.j2`, after the root disk:

```xml
{% for n in range(1, storage_disk_count + 1) %}
    <disk type="file" device="disk">
      <driver name="qemu" type="qcow2"/>
      <source file="{{ images_dir }}/{{ item.name }}-storage{{ n }}.qcow2"/>
      <target dev="vd{{ 'bcdefghi'[n - 1] }}" bus="virtio"/>
    </disk>
{% endfor %}
```

`destroy.yml`, extend the removal:

```yaml
- name: Remove storage disks
  ansible.builtin.file:
    path: '{{ images_dir }}/{{ item.0.name }}-storage{{ item.1 }}.qcow2'
    state: absent
  loop: '{{ molecule_yml.platforms | product(range(1, storage_disk_count + 1) | list) | list }}'
  loop_control:
    label: '{{ item.0.name }}-storage{{ item.1 }}'
```

- [ ] **Step 6: Harness — partition and label**

`tests/integration/molecule/test-ci/prepare.yml`, a new play **before** the bootstrap play (the converge needs the partitions, and nothing earlier does):

```yaml
- name: Partition the VM's storage disks the way an operator prepares a new disk
  hosts: all
  gather_facts: false
  vars:
    storagebaby_repo_root: /repo
  tasks:
    - name: Wait for cloud-init to finish
      ansible.builtin.command: cloud-init status --wait
      changed_when: false
      failed_when: false

    - name: Load the host's declaration
      ansible.builtin.include_vars:
        file: '{{ storagebaby_repo_root }}/hosts/{{ inventory_hostname }}/host.yml'

    - name: parted, for the one thing no role in this repo is allowed to do
      community.general.pacman:
        name: [parted]
        state: present

    - name: The disks this host declares, in order
      ansible.builtin.set_fact:
        _declared: '{{ storage.disks + storage.parity }}'

    - name: One virtual disk per declared disk
      ansible.builtin.assert:
        that: _declared | length == 4
        fail_msg: >-
          {{ inventory_hostname }} declares {{ _declared | length }} disks and create.yml
          attaches 4. Change storage_disk_count in create.yml and destroy.yml together.

    # This is the hand step the role refuses to take, done once per VM: a GPT label, one
    # partition spanning the disk, named after the declared disk -- which is what makes
    # `/dev/disk/by-partlabel/<name>` in the tracked host.yml resolve -- and ext4 on it.
    # Guarded on the label already existing so a second `prepare` on a live VM keeps the
    # data, exactly like the secret generation below it.
    - name: Partition, label and format each disk
      ansible.builtin.shell: |
        set -euo pipefail
        dev="/dev/vd{{ 'bcde'[idx | int] }}"
        if [ -e "/dev/disk/by-partlabel/{{ item.name }}" ]; then echo keeping; exit 0; fi
        parted -s "$dev" mklabel gpt mkpart {{ item.name }} ext4 1MiB 100%
        udevadm settle
        mkfs.ext4 -q -F -L {{ item.name }} "${dev}1"
        udevadm settle
        echo created
      args:
        executable: /bin/bash
      loop: '{{ _declared }}'
      loop_control:
        index_var: idx
        label: '{{ item.name }} -> {{ item.device }}'
      register: _partitioned
      changed_when: _partitioned.stdout == 'created'
```

Delete the now-duplicated "Wait for cloud-init to finish" from the bootstrap play.

- [ ] **Step 7: Integration checks**

`tests/integration/molecule/test-ci/tests/test_storage.py`:

```python
"""The host's filesystems: the declared mounts, and the union over them.

Read from the VM's own `host.yml` in the repo, like every other verifier here, so a
host that declares no storage skips instead of asserting something it never asked for.
"""

from pathlib import Path

import pytest
import yaml

HOSTS = Path("/repo/hosts")


def storage(host) -> dict:
    with (HOSTS / host.check_output("uname -n") / "host.yml").open() as fh:
        cfg = yaml.safe_load(fh)
    if "storage" not in cfg:
        pytest.skip("this host declares no storage")
    return cfg["storage"]


def unit(path: str) -> str:
    return path.lstrip("/").replace("/", "-") + ".mount"


def test_tools_are_installed(host):
    storage(host)
    assert host.run("mergerfs --version").rc == 0
    assert host.run("snapraid --version").rc == 0


def test_every_declared_mount_is_active(host):
    s = storage(host)
    for entry in s["disks"] + s.get("parity", []):
        assert host.service(unit(entry["mount"])).is_enabled, entry["name"]
        assert host.run(f"mountpoint -q -- {entry['mount']}").rc == 0, entry["mount"]
        assert host.file(entry["device"]).exists, entry["device"]


def test_pool_is_mergerfs_with_the_declared_options(host):
    s = storage(host)
    mount = s["pool"]["mount"]
    assert host.run(f"mountpoint -q -- {mount}").rc == 0
    assert host.check_output(f"findmnt -no FSTYPE {mount}") == "fuse.mergerfs"
    # The kernel's option string is not mergerfs's: the policy options live in the fuse
    # process, and mergerfs publishes its running configuration as extended attributes
    # on the mount point. That is the value that actually decides where a file lands.
    for option in s["pool"]["options"].split(","):
        if "=" not in option or not option.startswith(("category.", "minfreespace", "moveonenospc")):
            continue
        key, value = option.split("=", 1)
        got = host.check_output(f"getfattr --only-values -n user.mergerfs.{key} {mount}")
        assert got.strip().lower().rstrip("b") in value.lower().rstrip("b"), f"{key}: {got!r} != {value!r}"


def test_a_file_written_to_the_pool_lands_on_exactly_one_branch(host):
    """The union is a union, not a copy: one file, one branch, visible through the pool."""
    s = storage(host)
    probe = f"{s['pool']['mount']}/.storagebaby-branch-probe"
    host.run(f"rm -f {probe} " + " ".join(f"{d['mount']}/.storagebaby-branch-probe" for d in s["disks"]))
    assert host.run(f"sh -c 'echo probe > {probe}'").rc == 0
    on = [d["mount"] for d in s["disks"] if host.file(f"{d['mount']}/.storagebaby-branch-probe").exists]
    host.run(f"rm -f {probe}")
    assert len(on) == 1, f"the probe landed on {on}"
```

- [ ] **Step 8: Role README and run**

`ansible/roles/storage/README.md`: the contract, what the role refuses to do, why the mount unit name is a naive escape, where the tools come from (Chaotic-AUR for mergerfs, an in-role `makepkg` build for what Chaotic-AUR does not carry) and how a pin is bumped, that the first converge on storagebaby remounts `/pool`, and the `--check --diff` rule before a pool option change.

```bash
make test-static                            # green, storage tests included
make test-integration                       # test-a: all services on mergerfs now
MOLECULE_HOST=test-ci make test-integration # test-ci likewise
```

Both runs include the idempotence step, which is where a mount task that reports changed every time shows up.

- [ ] **Step 9: Commit**

```bash
git add ansible hosts tests docs
git commit -m "Mount the disks and the mergerfs pool from host.yml"
```

---

### Task 2: Snapraid, the maintenance run, its timer and its mail

Spec §2 (`snapraid`, `mail`), §3 (5–7), §6, §7 step 2.

**Files:**

- Create: `ansible/roles/storage/tasks/{snapraid,maintenance,mail}.yml`
- Create: `ansible/roles/storage/templates/{snapraid.conf.j2,msmtprc.j2,storage-maintenance.service.j2,storage-maintenance.timer.j2}`
- Create: `ansible/roles/storage/templates/maintenance/{storage-maintenance-unattended.sh.j2,balance_disks.sh.j2,scrub.sh.j2,plugin-stop.sh.j2,plugin-start.sh.j2}`
- Create: `ansible/roles/storage/files/maintenance/{storage-maintenance.sh,sync.sh}`
- Create: `hosts/storagebaby/secrets/mail.sops.yaml`
- Modify: `hosts/{storagebaby,test-a,test-ci}/host.yml` (`storage.snapraid`, `storage.mail`)
- Modify: `ansible/roles/storage/tasks/{main,tools,render}.yml`, `defaults/main.yml`, `README.md`
- Modify: `devtools/Dockerfile` (snapraid, for the static parse check), `tests/static/test_smoke.py`
- Modify: `tests/static/test_storage.py`, `tests/static/test_secrets.py`
- Create: `tests/integration/molecule/test-ci/files/test-smtp-sink.py`
- Modify: `tests/integration/molecule/test-ci/prepare.yml` (mail secret, SMTP sink), `tests/integration/molecule/test-ci/tests/test_storage.py`, `tests/integration/molecule/test-ci/tests/test_deploy.py`

**Interfaces later tasks rely on:**

- `storage.snapraid.{block_size,excludes,maintenance}` and `storage.snapraid.maintenance.{on_calendar,balance_threshold,scrub_percent,scrub_older_days,stop_services}`.
- `storage.mail.{to,from,smtp_host,smtp_port,smtp_user,tls,auth}`; the password is `hosts/<host>/secrets/mail.sops.yaml`, key `smtp_password`.
- On the host: `/etc/snapraid.conf`, `/opt/storagebaby/maintenance/` (`storage-maintenance.sh`, `sync.sh`, `scrub.sh`, `balance_disks.sh`, `bin/mergerfs.balance`, `plugins/<service>/*.sh`, `storage-maintenance-unattended.sh`), `/var/log/storage-maintenance/`, `storage-maintenance.{service,timer}`, `/etc/msmtprc`.
- On a test VM: `/var/spool/test-mail/*.eml`, written by `test-smtp-sink.service` on `127.0.0.1:2525`.

- [ ] **Step 1: Contract additions to `host.yml`, static tests first**

All three hosts gain, inside `storage`:

```yaml
snapraid:
  block_size: 256
  excludes:
    - '*.bak'
    - '*.unrecoverable'
    - /tmp/
    - /lost+found/
    - .AppleDouble
    - ._AppleDouble
    - .DS_Store
    - .Thumbs.db
    - .fseventsd
    - .Spotlight-V100
    - .TemporaryItems
    - .Trashes
    - .AppleDB
    - /apps/nextcloud/html/apps/
    - /apps/nextcloud/html/3rdparty/
    - /apps/nextcloud/html/custom_apps/
    - /apps/nextcloud/html/data/jlk/files/Projects/Kannji/_app_workspace/app/build/
    - '*.tmp'
    - '*.log'
    - /cache/
  maintenance:
    on_calendar: '02:00'
    balance_threshold: 5
    scrub_percent: 8
    scrub_older_days: 12
    # Services stopped before the balance moves their files between branches and
    # started again after the scrub -- and on failure, so a maintenance run that
    # aborts never leaves one down. Every name must be a service placed on this host.
    stop_services: [jellyfin]
```

storagebaby and `test-a` both place jellyfin, so both declare `stop_services: [jellyfin]`; `test-ci` does not place it and declares `stop_services: []`. The Nextcloud excludes are the deployed ones with the paths moved onto the platform's volume layout (`/apps/nextcloud/volumes/nextcloud/…` → `/apps/nextcloud/html/…`).

`mail`, per host:

```yaml
# storagebaby
mail:
  to: email@janlucaklees.de
  from: storagebaby@janlucaklees.de
  smtp_host: REPLACE_ME
  smtp_port: 587
  smtp_user: REPLACE_ME
```

```yaml
# test-a and test-ci: the sink `prepare` installs on the VM. `tls: false` because
# nothing but the loopback is between the two ends, and `auth: plain` explicitly
# because msmtp's automatic choice considers only SCRAM without TLS -- against a sink
# that offers PLAIN it would fail to authenticate and never send.
mail:
  to: root@test.local
  from: storagebaby@test.local
  smtp_host: 127.0.0.1
  smtp_port: 2525
  smtp_user: test
  tls: false
  auth: plain
```

`tests/static/test_storage.py`, add:

```python
SNAPRAID_KEYS = {"block_size", "excludes", "maintenance"}
MAINTENANCE_KEYS = {"on_calendar", "balance_threshold", "scrub_percent", "scrub_older_days", "stop_services"}
MAIL_KEYS = {"to", "from", "smtp_host", "smtp_port", "smtp_user", "tls", "auth"}


@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_snapraid_block_shape(host):
    snapraid = storage_of(host)["snapraid"]
    assert set(snapraid) == SNAPRAID_KEYS, f"{host}: {set(snapraid) ^ SNAPRAID_KEYS}"
    assert isinstance(snapraid["block_size"], int)
    assert all(isinstance(e, str) and e for e in snapraid["excludes"])
    m = snapraid["maintenance"]
    assert set(m) == MAINTENANCE_KEYS, f"{host}: {set(m) ^ MAINTENANCE_KEYS}"
    assert 0 <= m["balance_threshold"] <= 100 and 0 <= m["scrub_percent"] <= 100
    assert isinstance(m["scrub_older_days"], int)
    r = subprocess.run(["systemd-analyze", "calendar", m["on_calendar"]], capture_output=True, text=True)
    assert r.returncode == 0, f"{host}: on_calendar {m['on_calendar']!r} is not a systemd calendar: {r.stderr}"
    placed = {p.name for p in placements() if p.host == host}
    unknown = set(m["stop_services"]) - placed
    assert not unknown, f"{host}: stop_services names services not placed here: {unknown}"


@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_mail_block_shape(host):
    mail = storage_of(host)["mail"]
    assert set(mail) <= MAIL_KEYS and {"to", "from", "smtp_host", "smtp_port", "smtp_user"} <= set(mail)
    assert isinstance(mail["smtp_port"], int)
    assert "@" in mail["to"] and "@" in mail["from"]


@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_snapraid_conf_renders_and_parses(rendered, host):
    """The rendered file is one snapraid itself accepts, not one that merely looks right.

    Checked by running a real `snapraid sync` against a stub tree: the paths in the
    rendered file are the host's absolute ones, so a copy is made with every one of them
    prefixed into a temporary directory. An empty array syncs in milliseconds and writes
    the content and parity files, which is the whole claim -- the file parses, the disk
    and parity set is consistent, and snapraid agrees about both.
    """
    storage = host_cfg(host)["storage"]
    conf = (rendered / host / "storage" / "snapraid.conf").read_text()
    for disk in storage["disks"]:
        assert f"disk {disk['name']} {disk['mount']}/" in conf, conf
        assert f"content {disk['mount']}/snapraid.content" in conf
    for i, parity in enumerate(storage["parity"], start=1):
        prefix = "parity" if i == 1 else f"{i}-parity"
        assert f"{prefix} {parity['mount']}/snapraid.parity" in conf
    for exclude in storage["snapraid"]["excludes"]:
        assert f"exclude {exclude}" in conf
    assert f"block_size {storage['snapraid']['block_size']}" in conf


@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_msmtprc_renders_without_a_password(rendered, host):
    mail = host_cfg(host)["storage"]["mail"]
    text = (rendered / host / "storage" / "msmtprc").read_text()
    for expected in [f"host {mail['smtp_host']}", f"port {mail['smtp_port']}",
                     f"from {mail['from']}", f"user {mail['smtp_user']}"]:
        assert expected in text, text
    # The render path has no age key and decrypts nothing; the placeholder is what says
    # so. A real value here would mean the static run had read a secret.
    assert "password REPLACE_ME" in text, text
```

The `snapraid sync` stub-tree run is a helper in the same file:

```python
def _stubbed(conf: str, root) -> str:
    """The rendered config with every absolute path moved under `root`, and the dirs made."""
    out = []
    for line in conf.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1].startswith("/"):
            target = str(root) + parts[1]
            (root / parts[1].lstrip("/")).parent.mkdir(parents=True, exist_ok=True)
            if parts[0] == "disk":
                (root / parts[1].split(" ")[-1].lstrip("/")).mkdir(parents=True, exist_ok=True)
            line = f"{parts[0]} {target}"
        out.append(line)
    return "\n".join(out) + "\n"
```

and the test runs `subprocess.run(["snapraid", "-c", str(stub_conf), "sync"])`, asserting `returncode == 0` and that each `snapraid.content` and the `snapraid.parity` exist under the stub root.

`devtools/Dockerfile` — snapraid for that check. **Amended (2026-09-26), and this one
still has a choice in it.** The plan's
`https://github.com/amadvance/snapraid/releases/download/v14.9/snapraid-14.9-1-x86_64.pkg.tar.zst`
does not exist: upstream ships a source tarball, not an Arch package. Chaotic-AUR does not
carry snapraid either — verified against `chaotic-aur.db`, whose 3166 packages include
`mergerfs-2.42.0-2` and no `snapraid` and no `mergerfs-tools-git`. So the tooling image has
to build it. Two honest routes, to be decided in Task 2:

1. `./configure && make install` of the pinned source tarball, whose URL **and sha256** are
   in the AUR clone's `.SRCINFO` at `snapraid_aur_commit` (14.9:
   `40c216979d9d9853248060497341f74feaa07c8ae15927b6b14972c4f9d143d5`). No `makepkg`, no
   build user, no `base-devel` in the image — it is four lines and a checksum.
2. `makepkg` at the same pinned commit as a throwaway build user, which keeps one source
   of truth with the role at the cost of `base-devel` in the tooling image.

Either way the version comes from `ansible/roles/storage/defaults/main.yml`'s
`snapraid_version` / `snapraid_aur_commit`, so the image and the host agree by construction.

and `tests/static/test_smoke.py::test_tooling_present` gains `"snapraid"` and `"systemd-analyze"`.

`tests/static/test_secrets.py`, add:

```python
@pytest.mark.parametrize("host", [h for h in host_names() if not h.startswith("test-")])
def test_mail_secret_exists_for_a_host_that_sends_mail(host):
    cfg = load_yaml(HOSTS / host / "host.yml")
    if "mail" not in cfg.get("storage", {}):
        pytest.skip("this host sends no maintenance mail")
    doc = load_yaml(HOSTS / host / "secrets" / "mail.sops.yaml")
    assert "smtp_password" in set(doc) - {"sops"}
```

Run `make devtools && make test-static`: red on every new assertion.

- [ ] **Step 2: The maintenance scripts move**

`ansible/roles/storage/files/maintenance/storage-maintenance.sh` — `snapraid/storage-maintenance/storage-maintenance.sh` byte for byte, with one deletion: the `ROOT_DIR` line and its `# TODO` (nothing reads it). `SCRIPT_DIR` already derives from `BASH_SOURCE`, so the orchestrator finds `plugins/`, `balance_disks.sh`, `sync.sh` and `scrub.sh` beside itself wherever it is installed.

`ansible/roles/storage/files/maintenance/sync.sh` — verbatim.

`ansible/roles/storage/templates/maintenance/scrub.sh.j2`:

```bash
#!/bin/bash
# Rendered from `storage.snapraid.maintenance` in host.yml.

# Ensure log directory exists
mkdir -p /var/log/snapraid

# Runs the scrub.
snapraid scrub --plan new --log /var/log/snapraid/snapraid-scrub-new.log
snapraid scrub --plan {{ storage.snapraid.maintenance.scrub_percent }} \
	--older-than {{ storage.snapraid.maintenance.scrub_older_days }} \
	--log /var/log/snapraid/snapraid-scrub.log
```

`ansible/roles/storage/templates/maintenance/balance_disks.sh.j2` — the existing script with two values rendered: `DEFAULT_BALANCE_TARGET_PERCENTAGE="{{ storage.snapraid.maintenance.balance_threshold }}"` and the pool path `{{ storage.pool.mount }}` in the `mergerfs.balance` call, which becomes `/opt/storagebaby/maintenance/bin/mergerfs.balance`.

`ansible/roles/storage/templates/maintenance/plugin-stop.sh.j2` and `plugin-start.sh.j2` — one pair rendered per name in `stop_services`, which is what replaces the hand-written `plugins/jellyfin/` and retires `plugins/samba/` and the four `snapshot-*` plugins with it:

```bash
#!/bin/bash
# Rendered by the `storage` role from `storage.snapraid.maintenance.stop_services`.
# {{ item }} is stopped before the balance moves its files between branches.
# A pod service has no `<name>.service` at all, so the unit is asked for rather than
# guessed -- the same resolution the Makefile's service targets do.
unit={{ item }}.service
if systemctl --user -M svc-{{ item }}@ list-unit-files {{ item }}-pod.service 2> /dev/null \
	| grep -q '^{{ item }}-pod.service'; then
	unit={{ item }}-pod.service
fi
systemctl --user -M svc-{{ item }}@ stop "$unit"
```

`plugin-start.sh.j2` is the same with `start`, and is rendered to both `on-after-scrub.sh` and `on-failure.sh` — the failure path is what keeps a service that was stopped for a run that aborted from staying down.

`ansible/roles/storage/templates/maintenance/storage-maintenance-unattended.sh.j2` — the current wrapper with three rendered values: `EMAIL='{{ storage.mail.to }}'`, the orchestrator path `/opt/storagebaby/maintenance/storage-maintenance.sh`, and the mail command, which now names msmtp explicitly:

```bash
echo -e "$message\n\nLog Content:\n$(cat "$LOG_FILE")" \
	| mutt -e 'set sendmail="/usr/bin/msmtp"' -e 'set from="{{ storage.mail.from }}"' \
		-s "$subject" -- "$EMAIL"
```

- [ ] **Step 3: Role tasks**

`ansible/roles/storage/tasks/main.yml` gains, after `mounts.yml`:

```yaml
- name: Snapraid configuration
  ansible.builtin.include_tasks: snapraid.yml
  when: not (render_only | bool)

- name: Maintenance scripts and timer
  ansible.builtin.include_tasks: maintenance.yml
  when: not (render_only | bool)

- name: Outgoing mail
  ansible.builtin.include_tasks: mail.yml
  when: not (render_only | bool)
```

`tools.yml` gains the official packages and the balance script:

```yaml
- name: Packages the maintenance run needs
  community.general.pacman:
    name: [smartmontools, msmtp, mutt, python]
    state: present

# Amended 2026-09-26 (JLK's ruling): mergerfs-tools is not in Chaotic-AUR either
# (verified against chaotic-aur.db), so it is built from the AUR by the same task file
# snapraid uses. The pin is the AUR commit; note that the AUR package is a `-git` one,
# so the commit pins the *recipe*, not the upstream source it checks out.
- name: mergerfs-tools, built from the AUR (mergerfs.balance)
  ansible.builtin.include_tasks: aur_build.yml
  vars:
    aur_package: mergerfs-tools-git
    aur_commit: '{{ mergerfs_tools_aur_commit }}'
    aur_version: '{{ mergerfs_tools_version }}'
```

with `defaults/main.yml` gaining `mergerfs_tools_aur_commit`
(`098c987faa2690271c09b879dc49c8995e1558f7` is the AUR head as of 2026-09-26; Task 2 reads
the `pkgver`/`pkgrel` that commit produces and pins `mergerfs_tools_version` to it).

`balance_disks.sh.j2` then calls `mergerfs.balance` from `/usr/bin` like any other
installed tool, and the `/opt/storagebaby/maintenance/bin/` directory the original plan
invented for one downloaded script is not needed.

`ansible/roles/storage/tasks/snapraid.yml`:

```yaml
- name: Snapraid configuration
  ansible.builtin.template:
    src: snapraid.conf.j2
    dest: /etc/snapraid.conf
    owner: root
    group: root
    mode: '0644'
```

Nothing restarts on a change: snapraid is run by a timer and reads the file every time.

`ansible/roles/storage/tasks/maintenance.yml`:

```yaml
- name: Maintenance directories
  ansible.builtin.file:
    path: '{{ item }}'
    state: directory
    owner: root
    group: root
    mode: '0755'
  loop:
    - /opt/storagebaby/maintenance
    - /opt/storagebaby/maintenance/bin
    - /var/log/storage-maintenance
    - /var/log/snapraid

- name: Scripts that need nothing from host.yml
  ansible.builtin.copy:
    src: maintenance/{{ item }}
    dest: /opt/storagebaby/maintenance/{{ item }}
    owner: root
    group: root
    mode: '0755'
  loop: [storage-maintenance.sh, sync.sh]

- name: Scripts rendered from the maintenance block
  ansible.builtin.template:
    src: maintenance/{{ item }}.j2
    dest: /opt/storagebaby/maintenance/{{ item }}
    owner: root
    group: root
    mode: '0755'
  loop: [scrub.sh, balance_disks.sh, storage-maintenance-unattended.sh]

- name: Plugin directories, one per service the run stops
  ansible.builtin.file:
    path: /opt/storagebaby/maintenance/plugins/{{ item }}
    state: directory
    owner: root
    group: root
    mode: '0755'
  loop: '{{ storage.snapraid.maintenance.stop_services }}'

- name: Stop and start hooks per service
  ansible.builtin.template:
    src: maintenance/{{ item.1.src }}
    dest: /opt/storagebaby/maintenance/plugins/{{ item.0 }}/{{ item.1.hook }}
    owner: root
    group: root
    mode: '0755'
  loop: >-
    {{ storage.snapraid.maintenance.stop_services | product([
         {'src': 'plugin-stop.sh.j2', 'hook': 'on-before-balance.sh'},
         {'src': 'plugin-start.sh.j2', 'hook': 'on-after-scrub.sh'},
         {'src': 'plugin-start.sh.j2', 'hook': 'on-failure.sh'}]) | list }}
  loop_control:
    label: '{{ item.0 }}/{{ item.1.hook }}'
  vars:
    item: '{{ item }}'

# A plugin directory left behind is not the inert thing a stale unit is: the orchestrator
# globs `plugins/*/<hook>.sh` and runs whatever it finds, so a service removed from
# `stop_services` would still be stopped every night, by a script nothing points at.
- name: Find every plugin directory on disk
  ansible.builtin.find:
    paths: /opt/storagebaby/maintenance/plugins
    file_type: directory
  register: _plugins_present

- name: Remove the plugins of services this host no longer stops
  ansible.builtin.file:
    path: '{{ item.path }}'
    state: absent
  loop: '{{ _plugins_present.files }}'
  loop_control:
    label: '{{ item.path | basename }}'
  when: (item.path | basename) not in storage.snapraid.maintenance.stop_services

- name: Maintenance unit and timer
  ansible.builtin.template:
    src: '{{ item }}.j2'
    dest: /etc/systemd/system/{{ item }}
    owner: root
    group: root
    mode: '0644'
  loop: [storage-maintenance.service, storage-maintenance.timer]
  register: _maintenance_units

- name: Reload systemd for the maintenance units
  ansible.builtin.systemd:
    daemon_reload: true
  when: _maintenance_units.changed

- name: Maintenance timer enabled
  ansible.builtin.systemd:
    name: storage-maintenance.timer
    enabled: true
    state: started
```

The `product` loop above needs `item.0`/`item.1` and a template that reads `item.0` — rename the loop variable instead (`loop_control: loop_var: plugin`) and have `plugin-stop.sh.j2` read `plugin.0`. Keep the template's variable name a single word: set `loop_var: hook_for` and use `hook_for.0` in the templates.

`ansible/roles/storage/tasks/mail.yml`:

```yaml
- name: Copy the host's mail secrets
  ansible.builtin.copy:
    src: '{{ storagebaby_repo_root }}/hosts/{{ inventory_hostname }}/secrets/mail.sops.yaml'
    dest: /etc/storagebaby/mail.secrets.sops.yaml
    owner: root
    group: root
    mode: '0600'

- name: Decrypt the mail secrets
  ansible.builtin.command: sops --decrypt --output-type json /etc/storagebaby/mail.secrets.sops.yaml
  environment:
    SOPS_AGE_KEY_FILE: /etc/storagebaby/age.key
  register: _mail_secrets
  changed_when: false
  failed_when: false
  no_log: true

- name: The mail secrets decrypted
  ansible.builtin.assert:
    that: _mail_secrets.rc == 0
    fail_msg: >-
      sops could not decrypt hosts/{{ inventory_hostname }}/secrets/mail.sops.yaml.
      Is this host's age recipient in .sops.yaml, and has `sops updatekeys` run?
    quiet: true

- name: Read the SMTP password
  ansible.builtin.set_fact:
    smtp_password: '{{ (_mail_secrets.stdout | from_json).smtp_password }}'
  no_log: true

- name: msmtp configuration
  ansible.builtin.template:
    src: msmtprc.j2
    dest: /etc/msmtprc
    owner: root
    group: root
    mode: '0600'
  no_log: true
```

`ansible/roles/storage/templates/msmtprc.j2`:

```jinja
{# Rendered from `storage.mail` in host.yml and the host's `mail.sops.yaml`. 0600 and
   root-only: msmtp wants the password in clear, and the maintenance wrapper is the only
   thing that sends through it. #}
defaults
{% if storage.mail.tls | default(true) %}
tls on
tls_starttls on
tls_trust_file /etc/ssl/certs/ca-certificates.crt
{% else %}
tls off
{% endif %}
logfile /var/log/msmtp.log

account default
host {{ storage.mail.smtp_host }}
port {{ storage.mail.smtp_port }}
from {{ storage.mail.from }}
{# `auth on` lets msmtp choose, and without TLS it considers only SCRAM -- so a host
   that talks to a plain sink names the method it means. #}
auth {{ storage.mail.auth | default('on') }}
user {{ storage.mail.smtp_user }}
password {{ smtp_password }}

account default : default
```

`render.yml` gains the same three renders into `{{ render_output }}/storage/` (`snapraid.conf`, `msmtprc`, and the maintenance scripts under `maintenance/`), with `smtp_password` coming from the role default.

- [ ] **Step 4: Secrets — storagebaby's file and the test hosts' generator**

```bash
make sops FILE=hosts/storagebaby/secrets/mail.sops.yaml
```

with a single key, `smtp_password: REPLACE_ME` (the operator's age key has to be present; `test_recipients_match_sops_config` then checks it against the `hosts/storagebaby/**` rule).

`tests/integration/molecule/test-ci/prepare.yml`, in the secret-generating play, after the host-secret-set task:

```yaml
# The maintenance mail password. It belongs to no service's `secrets` and to no
# `host_secrets` set, so neither generator above covers it -- and the storage role
# refuses to converge without it. Same keep-if-ours rule as the others.
- name: Write and encrypt the mail secret
  ansible.builtin.shell: |
    set -euo pipefail
    if [ -f "$OUT" ] && grep -q '{{ storagebaby_age_pub }}' "$OUT"; then
      echo keeping
      exit 0
    fi
    printf 'smtp_password: "%s"\n' "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" > "$TMP"
    sops --encrypt --age {{ storagebaby_age_pub }} --input-type yaml --output-type yaml "$TMP" > "$OUT"
    rm -f "$TMP"
  args:
    executable: /bin/bash
    chdir: /tmp
  environment:
    TMP: '/tmp/{{ inventory_hostname }}-mail.yaml'
    OUT: '{{ _hosts_root }}/{{ inventory_hostname }}/secrets/mail.sops.yaml'
  delegate_to: localhost
  register: _mail_secret
  changed_when: _mail_secret.stdout != 'keeping'
```

- [ ] **Step 5: The SMTP sink on the test VM**

`tests/integration/molecule/test-ci/files/test-smtp-sink.py` — the simplest thing that is still real SMTP, and it needs nothing but the python the Arch cloud image already has (`smtpd` left the standard library in 3.12 and `aiosmtpd` would be a package on a host that is supposed to look like storagebaby):

```python
#!/usr/bin/env python3
"""A throwaway SMTP sink for the test VM: one file per message under /var/spool/test-mail.

Speaks exactly as much SMTP as msmtp uses -- EHLO, AUTH PLAIN, MAIL, RCPT, DATA, QUIT --
and accepts any credentials: the password is a generated throwaway and what the test
asserts is that a message arrived, not who sent it. No TLS; both ends are 127.0.0.1 and
the host declares `tls: false` for exactly this reason.
"""

import itertools
import os
import socketserver

SPOOL = "/var/spool/test-mail"
counter = itertools.count(1)


class Handler(socketserver.StreamRequestHandler):
    def reply(self, line: str) -> None:
        self.wfile.write(line.encode() + b"\r\n")
        self.wfile.flush()

    def handle(self) -> None:
        self.reply("220 test-smtp-sink")
        body: list[str] = []
        in_data = False
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if in_data:
                if line == ".":
                    path = os.path.join(SPOOL, f"{next(counter):04d}.eml")
                    with open(path, "w") as fh:
                        fh.write("\n".join(body) + "\n")
                    body, in_data = [], False
                    self.reply("250 2.0.0 Ok: queued")
                else:
                    body.append(line[1:] if line.startswith("..") else line)
                continue
            verb = line.split(" ", 1)[0].upper()
            if verb == "EHLO":
                self.reply("250-test-smtp-sink")
                self.reply("250 AUTH PLAIN LOGIN")
            elif verb == "HELO":
                self.reply("250 test-smtp-sink")
            elif verb == "AUTH":
                self.reply("235 2.7.0 Authentication successful")
            elif verb in {"MAIL", "RCPT", "RSET", "NOOP"}:
                self.reply("250 2.1.0 Ok")
            elif verb == "DATA":
                in_data = True
                self.reply("354 End data with <CR><LF>.<CR><LF>")
            elif verb == "QUIT":
                self.reply("221 2.0.0 Bye")
                return
            else:
                self.reply("502 5.5.2 Command not implemented")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    os.makedirs(SPOOL, exist_ok=True)
    Server(("127.0.0.1", 2525), Handler).serve_forever()
```

`prepare.yml`, in the bootstrap play:

```yaml
- name: Install the throwaway SMTP sink
  ansible.builtin.copy:
    src: files/test-smtp-sink.py
    dest: /usr/local/sbin/test-smtp-sink
    mode: '0755'

- name: The sink's unit
  ansible.builtin.copy:
    dest: /etc/systemd/system/test-smtp-sink.service
    mode: '0644'
    content: |
      [Unit]
      Description=Throwaway SMTP sink for the integration tests

      [Service]
      ExecStart=/usr/local/sbin/test-smtp-sink
      Restart=always

      [Install]
      WantedBy=multi-user.target

- name: The sink runs
  ansible.builtin.systemd:
    name: test-smtp-sink.service
    daemon_reload: true
    enabled: true
    state: started
```

- [ ] **Step 6: Integration checks**

`tests/integration/molecule/test-ci/tests/test_storage.py`, add:

```python
def test_snapraid_conf_declares_every_disk(host):
    s = storage(host)
    conf = host.file("/etc/snapraid.conf")
    assert conf.exists and conf.mode == 0o644
    for disk in s["disks"]:
        assert f"disk {disk['name']} {disk['mount']}/" in conf.content_string
    assert f"parity {s['parity'][0]['mount']}/snapraid.parity" in conf.content_string


def test_maintenance_timer_is_enabled(host):
    storage(host)
    assert host.service("storage-maintenance.timer").is_enabled
    assert host.service("storage-maintenance.timer").is_running


def test_a_maintenance_run_is_clean(host):
    """One real run, end to end: balance, sync, scrub, status, SMART, and the mail.

    A few files are written into the pool first, because an array with nothing in it
    proves nothing about a sync -- and one of them is what the content and parity files
    below are evidence of. The unit is a oneshot, so `start` blocks until it is done.
    """
    s = storage(host)
    host.run(f"mkdir -p {s['pool']['mount']}/maintenance-probe")
    for n in range(3):
        host.run(f"dd if=/dev/urandom of={s['pool']['mount']}/maintenance-probe/{n}.bin bs=1M count=4 status=none")
    host.run("rm -f /var/spool/test-mail/*.eml")

    r = host.run("timeout 1800 systemctl start storage-maintenance.service")
    journal = host.run("journalctl -u storage-maintenance.service --no-pager | tail -120").stdout
    assert r.rc == 0, journal
    assert host.run("systemctl show storage-maintenance.service -p Result --value").stdout.strip() == "success"
    log = host.file("/var/log/storage-maintenance/storage-maintenance.log").content_string
    for section in ["=== Balancing disks ===", "=== Syncing ===", "=== Scrubbing ===",
                    "=== Final Snapraid Status ===", "=== Job Finished with Status: SUCCESS ==="]:
        assert section in log, log[-4000:]
    for disk in s["disks"]:
        assert host.file(f"{disk['mount']}/snapraid.content").exists, disk["name"]
    assert host.file("/etc/snapraid.content").exists
    assert host.file(f"{s['parity'][0]['mount']}/snapraid.parity").exists


def test_the_maintenance_mail_reaches_the_sink(host):
    """Run after the maintenance test, and about the message rather than about msmtp.

    The sink writes one file per message, so a report that msmtp could not send simply
    is not there -- which is the failure this catches: a wrong port, a refused auth or a
    mutt that has no sendmail would all leave the maintenance run itself green.
    """
    storage(host)
    listing = host.run("ls -1 /var/spool/test-mail")
    assert listing.stdout.split(), "the sink holds no message after the maintenance run"
    body = host.file(f"/var/spool/test-mail/{sorted(listing.stdout.split())[-1]}").content_string
    assert "SnapRAID Sync Report" in body, body[:2000]
    assert "=== Job Finished with Status: SUCCESS ===" in body, body[:2000]
```

`tests/integration/molecule/test-ci/tests/test_deploy.py`, two more `@pytest.mark.order(-1)` cases, placed **after** the existing ones because both disturb the pool:

```python
@pytest.mark.order(-1)
def test_deploy_refuses_to_converge_with_a_disk_unmounted(host):
    """A missing filesystem stops the converge before any service is touched.

    This is the property `mountpoints` used to carry and the reason the storage role runs
    first: with a branch gone, `/pool/apps` is an empty directory on the root filesystem,
    and a converge that carried on would recreate every volume under it -- empty, at
    02:00, from the deploy timer. So the run has to fail, name the disk, and leave the
    services alone.

    Stopping a branch stops the pool with it (`Requires=`), so this runs last: the
    containers' bind mounts under the union do not survive the remount.
    """
    s = storage_of(host)
    if not s:
        pytest.skip("this host declares no storage")
    disk = s["disks"][-1]
    unit = disk["mount"].lstrip("/").replace("/", "-") + ".mount"
    traefik_before = active_since(host, "svc-traefik", "traefik.service")

    assert host.run(f"systemctl stop {unit}").rc == 0
    r = host.run(DEPLOY)
    assert r.rc != 0, "the converge did not fail with a declared disk unmounted"
    journal = host.run(JOURNAL).stdout
    assert disk["name"] in journal or disk["mount"] in journal, journal
    assert active_since(host, "svc-traefik", "traefik.service") == traefik_before, "a service was restarted anyway"

    assert host.run(f"systemctl start {unit}").rc == 0
    assert host.run(f"systemctl start {unit_of_pool(s)}").rc == 0
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout


@pytest.mark.order(-1)
def test_a_pool_option_change_remounts_the_pool_exactly_once(host):
    s = storage_of(host)
    if not s:
        pytest.skip("this host declares no storage")
    pool_unit = unit_of_pool(s)
    before = host.run(f"systemctl show {pool_unit} -p ActiveEnterTimestampMonotonic --value").stdout.strip()
    r = host.run(
        f"cd {SEEDED} && sed -i 's/threads=2/threads=3/' hosts/$(uname -n)/host.yml "
        "&& git -c user.name=t -c user.email=t@t commit -qam 'change a pool option' && git push -q origin stable"
    )
    assert r.rc == 0, r.stderr
    assert host.run(DEPLOY).rc == 0, host.run(JOURNAL).stdout
    after = host.run(f"systemctl show {pool_unit} -p ActiveEnterTimestampMonotonic --value").stdout.strip()
    assert after != before, "the pool was not remounted by its changed option"
    assert host.run(f"mountpoint -q -- {s['pool']['mount']}").rc == 0, "the pool is not mounted any more"
    # Once, not on every converge: the second deploy renders the same unit and must move
    # nothing. A remount per nightly deploy would drop every container's bind mount.
    assert host.run(DEPLOY).rc == 0
    assert host.run(f"systemctl show {pool_unit} -p ActiveEnterTimestampMonotonic --value").stdout.strip() == after
```

- [ ] **Step 7: Run**

```bash
make devtools
make fmt-check
make test-static
make test-integration
MOLECULE_HOST=test-ci make test-integration
```

If `snapraid touch` fails on a virgin array (no content file yet), the fix is in the **test**, not the role: run `snapraid sync` once from the verifier before the maintenance case. Do not make the role sync — the role never writes parity.

- [ ] **Step 8: Commit**

```bash
git add ansible hosts tests devtools docs
git commit -m "Converge snapraid, the nightly maintenance run and its mail"
```

---

### Task 3: `tcp_ports` — plain TCP through Traefik

Spec §5 contract, §6 static, §7 step 3. Nothing is placed on a TCP port yet; Task 4 is the first user.

**Files:**

- Modify: `tests/static/conftest.py` (`tcp_ports` helper), `tests/static/test_contract.py`, `tests/static/test_ports.py`, `tests/static/test_quadlet_conventions.py`, `tests/static/test_render.py`
- Modify: `ansible/playbook.yml` (`placed_tcp_ports`)
- Modify: `ansible/roles/host_base/tasks/main.yml` (the unprivileged-port sysctl)
- Modify: `hosts/shared/services/traefik/quadlet/traefik.container.j2`, `hosts/shared/services/traefik/README.md`
- Create: `ansible/roles/service/templates/traefik-tcp.yml.j2`
- Modify: `ansible/roles/service/tasks/{main,units,render}.yml`, `ansible/roles/service/README.md`
- Modify: `tests/integration/molecule/test-ci/tests/{test_traefik.py,test_host_base.py,test_service.py}`

**Interfaces later tasks rely on:**

- `service.yml` key `tcp_ports`: a list of `{port, target}` (target defaults to port) and `{range: [lo, hi]}` (each port forwarded to itself).
- Playbook fact `placed_tcp_ports`: every port of every placed service, ranges expanded, unique and sorted. Read by the traefik template and by `host_base`.
- Traefik entrypoint naming: `tcp-<port>`, address `:<port>`.
- Route file `/etc/storagebaby/traefik/dynamic.d/<name>-tcp.yml`; router and service named `<name>-tcp-<port>`.
- Role fact `tcp_enabled`.
- Static helper `conftest.tcp_ports(spec) -> list[{"port", "target"}]`, used by `test_ports` and by the pod publish check.

- [ ] **Step 1: Static tests first**

`tests/static/conftest.py`:

```python
def tcp_ports(spec: dict) -> list[dict]:
    """Every plain-TCP port a service claims, normalised to `{port, target}`.

    `{port, target}` is the explicit form -- Traefik listens on `port` and forwards to
    `127.0.0.1:target`. `{range: [lo, hi]}` expands to one entry per port, each
    forwarded to the same number, because the only thing that needs a range is an FTP
    passive port range, where the server advertises the port it is listening on and the
    two sides have to agree.
    """
    out = []
    for entry in spec.get("tcp_ports", []):
        if "range" in entry:
            lo, hi = entry["range"]
            out += [{"port": p, "target": p} for p in range(lo, hi + 1)]
        else:
            out.append({"port": entry["port"], "target": entry.get("target", entry["port"])})
    return out
```

`tests/static/test_contract.py`: add `"tcp_ports"` to `SPEC_KEYS` and, inside `test_service_contract`:

```python
    for entry in spec.get("tcp_ports", []):
        assert set(entry) <= {"port", "target", "range"}, f"{p.name}: unknown tcp_ports keys"
        if "range" in entry:
            assert set(entry) == {"range"}, f"{p.name}: a tcp range takes neither port nor target"
            lo, hi = entry["range"]
            assert isinstance(lo, int) and isinstance(hi, int), f"{p.name}: a tcp range is two integers"
            assert 1024 <= lo <= hi <= 65535, f"{p.name}: tcp range {lo}-{hi} out of bounds"
            # One entrypoint per port is one listener in Traefik and one PublishPort on
            # the pod; a range of hundreds is a configuration mistake, not a feature.
            assert hi - lo < 64, f"{p.name}: a tcp range of {hi - lo + 1} ports is too many entrypoints"
        else:
            assert isinstance(entry["port"], int) and 1 <= entry["port"] <= 65535
            assert isinstance(entry.get("target", entry["port"]), int)
    targets = [e["target"] for e in tcp_ports(spec)]
    assert len(set(targets)) == len(targets), f"{p.name}: two tcp ports forward to the same loopback port"
```

`tests/static/test_ports.py` — one namespace per host:

```python
from collections import defaultdict

from conftest import host_names, placements, route_ports, tcp_ports

# Traefik's own two listeners. A service claiming either would either fail to bind or
# take HTTPS off the host.
RESERVED = {80: "traefik's web entrypoint", 443: "traefik's websecure entrypoint"}


def test_ports_are_unique_per_host():
    """Route ports, TCP entrypoints and TCP targets are one namespace.

    A Traefik entrypoint binds `:<port>`, which is every address including 127.0.0.1 --
    so a public TCP port that equals another service's loopback port is a real collision,
    not a coincidence of numbers. Checking the three sets separately would miss exactly
    that, which is the case the FTP control port (21) is one sysctl away from.
    """
    all_placements = placements()
    for host in host_names():
        seen = defaultdict(list)
        for p in all_placements:
            if p.host != host:
                continue
            for port in route_ports(p.spec):
                seen[port].append(f"{p.name} route")
            for entry in tcp_ports(p.spec):
                seen[entry["port"]].append(f"{p.name} tcp entrypoint")
                if entry["target"] != entry["port"]:
                    seen[entry["target"]].append(f"{p.name} tcp target")
        dupes = {port: names for port, names in seen.items() if len(names) > 1}
        assert not dupes, f"{host}: port collisions {dupes}"
        clash = {port: RESERVED[port] for port in set(seen) & set(RESERVED)}
        assert not clash, f"{host}: {clash} cannot be claimed by a service"
```

`tests/static/test_quadlet_conventions.py::test_pod_publishes_exactly_the_route_ports` — the pod now also publishes the TCP targets, and a range is one line:

```python
PUBLISH = re.compile(r"^PublishPort=127\.0\.0\.1:(\d+)(?:-(\d+)):", flags=re.M)


def published_ports(pod_text: str) -> list[int]:
    """Every host port a pod publishes, a `lo-hi` range counted port by port."""
    out = []
    for lo, hi in PUBLISH.findall(pod_text):
        out += list(range(int(lo), int(hi or lo) + 1))
    return out
```

and the assertion becomes
`assert sorted(published_ports(pod)) == sorted(route_ports(p.spec) + [e["target"] for e in tcp_ports(p.spec)])`.

`tests/static/test_render.py`, a new case:

```python
@pytest.mark.parametrize("p", [p for p in placements() if p.spec.get("tcp_ports")], ids=lambda p: f"{p.host}/{p.name}")
def test_tcp_routes_rendered(rendered, p):
    """One router and one service per declared port, and an entrypoint for each in traefik's unit.

    The two halves have to agree or the port is dead in a way nothing reports: a router
    on an entrypoint that does not exist is ignored by Traefik with a log line, and an
    entrypoint with no router accepts the connection and closes it.
    """
    doc = yaml.safe_load((rendered / p.host / "traefik-dynamic.d" / f"{p.name}-tcp.yml").read_text())
    expected = tcp_ports(p.spec)
    assert set(doc["tcp"]["routers"]) == {f"{p.name}-tcp-{e['port']}" for e in expected}
    for entry in expected:
        router = doc["tcp"]["routers"][f"{p.name}-tcp-{entry['port']}"]
        assert router["rule"] == "HostSNI(`*`)"
        assert router["entryPoints"] == [f"tcp-{entry['port']}"]
        service = doc["tcp"]["services"][router["service"]]
        assert service["loadBalancer"]["servers"] == [{"address": f"127.0.0.1:{entry['target']}"}]
    unit = (rendered / p.host / "traefik" / "traefik.container").read_text()
    for entry in expected:
        assert f"--entrypoints.tcp-{entry['port']}.address=:{entry['port']}" in unit, unit
```

Everything here collects nothing until Task 4 — which is the point of writing it now: the moment paperless declares `tcp_ports`, these fail if the plumbing is wrong.

- [ ] **Step 2: The playbook collects the ports**

`ansible/playbook.yml`, after the route-domain collection:

```yaml
# The host's TCP listeners, collected here for the same reason `placed_fqdns` is: a
# service's spec knows its own ports, and what traefik's unit and the unprivileged
# port sysctl need is the *host's* whole list.
- name: Collect the TCP port declarations of every placed service
  ansible.builtin.set_fact:
    _placed_tcp: >-
      {{ (_placed_tcp | default([])) + (_spec.tcp_ports | default([])) }}
  vars:
    _spec: "{{ lookup('file', placed_service) | from_yaml }}"
  loop: '{{ placed_services }}'
  loop_control:
    loop_var: placed_service
    label: '{{ placed_service | dirname | basename }}'

- name: Expand every declared TCP port, ranges included
  ansible.builtin.set_fact:
    _tcp_expanded: >-
      {{ (_tcp_expanded | default([]))
         + (range(tcp_entry.range[0], tcp_entry.range[1] + 1) | list
            if tcp_entry.range is defined else [tcp_entry.port]) }}
  loop: '{{ _placed_tcp | default([]) }}'
  loop_control:
    loop_var: tcp_entry
    label: '{{ tcp_entry }}'

# Sorted and de-duplicated, like placed_fqdns and for the same reason: it is rendered
# into traefik's unit, and a list in a different order every run would restart traefik
# on every converge.
- name: The host's TCP entrypoint ports, in a stable order
  ansible.builtin.set_fact:
    placed_tcp_ports: '{{ _tcp_expanded | default([]) | unique | sort | list }}'
```

- [ ] **Step 3: Traefik listens**

`hosts/shared/services/traefik/quadlet/traefik.container.j2`, inside the `Exec=` continuation, after the `traefik` entrypoint line:

```jinja
{% for port in placed_tcp_ports | default([]) %}
  --entrypoints.tcp-{{ port }}.address=:{{ port }} \
{% endfor %}
```

**The sysctl has to come down with it.** `host_base` sets `net.ipv4.ip_unprivileged_port_start` to 80 so a rootless Traefik can bind 80 and 443; FTP's control port is 21, which that setting forbids. Replace the task:

```yaml
# Traefik is rootless, so every port it binds has to be above this line. 80 is the
# baseline (the web entrypoint); a placed service that claims a lower TCP port -- FTP's
# control port 21 is the case this exists for -- brings it down to that port and no
# further, which is why it is a `min` over the placement rather than a constant.
- name: Allow unprivileged binding from the lowest port Traefik must listen on
  ansible.posix.sysctl:
    name: net.ipv4.ip_unprivileged_port_start
    value: '{{ ([80] + (placed_tcp_ports | default([]))) | min }}'
    sysctl_set: true
    state: present
    reload: true
```

- [ ] **Step 4: The service role renders the routers**

`ansible/roles/service/tasks/main.yml`, beside the route facts:

```yaml
- name: Whether this service claims plain TCP ports
  ansible.builtin.set_fact:
    tcp_enabled: '{{ (service.tcp_ports | default([])) | length > 0 }}'
```

The expansion itself stays in the template — it is the only consumer of the pair form, and Jinja does a range-to-entries loop far more readably than a chain of `set_fact`s.

`ansible/roles/service/templates/traefik-tcp.yml.j2`:

```jinja
{# Every plain-TCP port this service claims, as one Traefik TCP router and one TCP
   service each. Plain TCP carries no hostname, so `HostSNI(`*`)` is the rule -- the one
   Traefik requires for a non-TLS TCP router -- and the entrypoint is what selects the
   backend. That is also why a port belongs to exactly one service per host: there is no
   second thing to route on. `test_ports` is what holds that. #}
{% set entries = [] %}
{% for e in service.tcp_ports %}
{%   if e.range is defined %}
{%     for p in range(e.range[0], e.range[1] + 1) %}
{%       set _ = entries.append({'port': p, 'target': p}) %}
{%     endfor %}
{%   else %}
{%     set _ = entries.append({'port': e.port, 'target': e.target | default(e.port)}) %}
{%   endif %}
{% endfor %}
tcp:
  routers:
{% for entry in entries %}
    {{ service.name }}-tcp-{{ entry.port }}:
      rule: "HostSNI(`*`)"
      entryPoints: [tcp-{{ entry.port }}]
      service: {{ service.name }}-tcp-{{ entry.port }}
{% endfor %}
  services:
{% for entry in entries %}
    {{ service.name }}-tcp-{{ entry.port }}:
      loadBalancer:
        servers:
          - address: "127.0.0.1:{{ entry.target }}"
{% endfor %}
```

`ansible/roles/service/tasks/units.yml`, beside the HTTP route render:

```yaml
- name: Render the service's TCP routes
  ansible.builtin.template:
    src: traefik-tcp.yml.j2
    dest: '/etc/storagebaby/traefik/dynamic.d/{{ service.name }}-tcp.yml'
    owner: root
    group: root
    mode: '0644'
  when: tcp_enabled | bool

# Same rule as the pre-Phase-3 route file: a stale *configuration* file is not the inert
# thing a stale unit is. Traefik reads the whole directory, so a left-behind router would
# keep forwarding a port the service no longer claims -- to whatever else has since been
# published on it.
- name: Remove the TCP routes of a service that claims none
  ansible.builtin.file:
    path: '/etc/storagebaby/traefik/dynamic.d/{{ service.name }}-tcp.yml'
    state: absent
  when: not (tcp_enabled | bool)
```

`render.yml` gets the same render into `{{ render_output }}/traefik-dynamic.d/`.

- [ ] **Step 5: Integration checks that wait for Task 4**

`tests/integration/molecule/test-ci/tests/test_service.py`, a helper beside `placed_route_fqdns`:

```python
def placed_tcp_entries(host) -> list[tuple[str, int, int]]:
    """(service, entrypoint port, loopback target) for every TCP port placed on this VM."""
    hostname = host.check_output("uname -n")
    found = []
    for owner, spec_path in SPECS:
        if owner not in ("shared", hostname):
            continue
        spec = load_spec(spec_path)
        for entry in spec.get("tcp_ports", []):
            if "range" in entry:
                found += [(spec["name"], p, p) for p in range(entry["range"][0], entry["range"][1] + 1)]
            else:
                found.append((spec["name"], entry["port"], entry.get("target", entry["port"])))
    return sorted(found)
```

`test_traefik.py`:

```python
def test_traefik_is_the_only_listener_on_every_placed_tcp_port(host):
    """Traefik listens on each declared port, on every address, and the pod only on loopback.

    Both halves matter: a port Traefik does not listen on is a scanner that cannot
    connect, and a pod that published the same port on 0.0.0.0 would be reachable past
    Traefik -- which is the rule the whole platform is built on.
    """
    entries = placed_tcp_entries(host)
    if not entries:
        pytest.skip("no service on this host claims a TCP port")
    listeners = host.check_output("ss -H -lntp")
    for _, port, target in entries:
        public = [ln for ln in listeners.splitlines() if f":{port} " in ln]
        assert public, f"nothing listens on {port}:\n{listeners}"
        assert all("traefik" in ln for ln in public), f"something other than traefik holds {port}:\n{public}"
        loopback = [ln for ln in listeners.splitlines() if f"127.0.0.1:{target} " in ln]
        assert loopback, f"nothing listens on 127.0.0.1:{target}"
```

`test_host_base.py::test_unprivileged_ports` becomes derived:

```python
def test_unprivileged_ports(host):
    expected = min([80] + [port for _, port, _ in placed_tcp_entries(host)])
    assert host.run("sysctl -n net.ipv4.ip_unprivileged_port_start").stdout.strip() == str(expected)
```

- [ ] **Step 6: Docs and run**

`ansible/roles/service/README.md`: a `## TCP ports` section — the contract, the one-service-per-port rule and why (plain TCP has no hostname), the entrypoint naming, the sysctl consequence, and that the pod must publish every target on loopback.
`hosts/shared/services/traefik/README.md`: a paragraph on the TCP entrypoints, that they are rendered from the host's placement, and that a change to the set restarts traefik once.

`make test-static` green; `make test-integration` and `MOLECULE_HOST=test-ci make test-integration` green with the new checks skipping.

- [ ] **Step 7: Commit**

```bash
git add ansible hosts tests docs
git commit -m "Route plain TCP through Traefik from a service's tcp_ports"
```

---

### Task 4: Paperless's FTP drop, and the end of `paperless-upload`

Spec §5 Paperless, §4 (paperless-upload, the `scans` bind), §7 step 4.

**Image decision: `docker.io/stilliard/pure-ftpd:trixie-1.0.50`.** Pinned to a tag that exists and is current (rebuilt 2026-09-15; the project still ships `bookworm-`/`trixie-` variants per pure-ftpd release). It is the candidate that fits a rootless pod without an argument: the image builds pure-ftpd from source **specifically to remove the need for `CAP_SYS_NICE` and `CAP_DAC_READ_SEARCH`**, leaving `CAP_SYS_CHROOT`, which is in podman's default capability set. It takes exactly the four things this needs from the environment — one virtual user (`FTP_USER_NAME`, `FTP_USER_PASS`, `FTP_USER_HOME`, `FTP_USER_UID`/`FTP_USER_GID`), a passive range (`-p lo:hi`), a listen port (`-S`) and a public address (`-P`) — and it ships `bash`, so the health check is the same `/dev/tcp` connect `paperless-tika` uses. The alternative, `delfer/alpine-ftp-server` (vsftpd), publishes only `latest`, which this repo does not pin to for anything that carries state or credentials.

**First step is still a probe**, because the claim "runs unprivileged in a rootless pod" is the one thing that cannot be read off a Dockerfile:

> On the running VM, `make molecule-exec CMD='...'`: pull the image, run it as `svc-paperless` with the consume volume, `-S 2121 -p 21100:21109 -P 127.0.0.1`, and upload a file with `curl --ftp-pasv -T`. **Decision rule:** if the upload completes and the file appears in the volume owned by the mapped uid, keep pure-ftpd. If it fails on a capability, a chroot or the passive range, switch to `docker.io/delfer/alpine-ftp-server:latest` (vsftpd; `USERS="<user>|<pass>|/consume|1000"`, `ADDRESS`, `MIN_PORT`, `MAX_PORT`) and record the failure in the service README. Time-box: 10 minutes.

**Files:**

- Modify: `hosts/storagebaby/services/paperless/service.yml`, `quadlet/paperless.pod.j2`, `quadlet/paperless-app.container.j2`, `secrets.sops.yaml`, `README.md`
- Create: `hosts/storagebaby/services/paperless/quadlet/paperless-ftp.container.j2`
- Modify: `hosts/storagebaby/host.yml` (`service_config.paperless.ftp_public_address`), `hosts/test-a/host.yml`, `hosts/test-ci/host.yml`
- Delete: `hosts/storagebaby/services/paperless-upload/` (whole folder), `hosts/test-a/services/paperless-upload`, `hosts/test-ci/services/paperless-upload` (symlinks)
- Modify: `tests/static/test_storage.py` (nothing), `tests/static/test_contract.py` (a config check, below)
- Create: `tests/integration/molecule/test-ci/tests/test_ftp.py`
- Modify: `tests/integration/molecule/test-ci/tests/test_service.py` (drop the uploader hop test), `README.md` (services table row)

**Interfaces later tasks rely on:**

- `service.config.ftp_user`, `service.config.ftp_public_address` (per host through `service_config`), secret `ftp_password`, volume `consume` (class `fast`), container `paperless-ftp`, pod ports `2121` and `21100-21109`.

- [ ] **Step 1: The probe above.** Record the result in the paperless README; if it fails, substitute the fallback image throughout this task and stop to re-plan the unit's environment.

- [ ] **Step 2: `service.yml`**

```yaml
volumes:
  data: { class: pool }
  media: { class: pool }
  consume: { class: fast }
  database: { class: fast }
  broker: { class: fast }
  backups: { class: fast }
tcp_ports:
  # Traefik listens on 21 and forwards to the pod's 2121: the control connection.
  - { port: 21, target: 2121 }
  # The passive data connections. Each port is forwarded to itself because the server
  # advertises the one it is listening on, and a rewritten port would send the client
  # somewhere nothing answers.
  - { range: [21100, 21109] }
config:
  database_name: paperless
  database_user: paperless
  ocr_language: deu
  trusted_proxies: ''
  ftp_user: scanner
  # The address the FTP server advertises for a passive data connection. It has to be
  # the address the *client* can reach, which the server cannot work out for itself
  # behind a TCP proxy -- so every host that places paperless sets it.
  ftp_public_address: REPLACE_ME
secrets: [database_password, secret_key, ftp_password]
```

`hosts/storagebaby/host.yml`:

```yaml
service_config:
  paperless:
    # storagebaby's LAN address, the one the scanner connects to. An operator item:
    # README, "Fill the placeholders".
    ftp_public_address: REPLACE_ME
```

`hosts/test-a/host.yml` and `hosts/test-ci/host.yml` — the `paperless-upload` override goes, and paperless gains:

```yaml
paperless:
  # The FTP client in the integration suite runs on the VM itself, so the address it
  # can reach the passive ports on is 127.0.0.1 -- which is also a real value rather
  # than one the harness has to discover, and it still goes through Traefik: the
  # entrypoints bind every address, loopback included.
  ftp_public_address: 127.0.0.1
```

A static check so nobody forgets it (`tests/static/test_contract.py`, module level):

```python
@pytest.mark.parametrize("p", [p for p in placements() if p.spec.get("tcp_ports")], ids=lambda p: f"{p.host}/{p.name}")
def test_a_passive_service_declares_its_public_address(p):
    """A TCP service that advertises an address of its own has to be told which one.

    FTP is the case: the server puts an address into its PASV reply, and behind a TCP
    proxy the one it sees is never the one the client used. Left unset it advertises the
    pod's, and every transfer hangs until it times out -- a failure with no error on
    either side.
    """
    cfg = load_yaml(HOSTS / p.host / "host.yml").get("service_config", {}).get(p.name, {})
    value = cfg.get("ftp_public_address", p.spec.get("config", {}).get("ftp_public_address"))
    if value is None:
        pytest.skip("this service advertises no address of its own")
    assert value and value != "REPLACE_ME" or p.host == "storagebaby", (
        f"{p.host}/{p.name}: ftp_public_address is still {value!r}"
    )
```

(storagebaby is exempt until the operator fills it — the same `REPLACE_ME` convention every other unknown production value uses, and it is listed in the README's operator table.)

- [ ] **Step 3: The units**

`quadlet/paperless.pod.j2`:

```ini
[Pod]
PodName=paperless
{# Literal, not `{{ service.port }}`: the static check reads these lines out of the raw
   template to compare the published ports against the declared routes and tcp targets. #}
PublishPort=127.0.0.1:8000:8000
PublishPort=127.0.0.1:2121:2121
PublishPort=127.0.0.1:21100-21109:21100-21109
```

> **Task 3 amendment.** These publish lines stay exactly as written: the entrypoints now bind
> `{{ tcp_bind_address }}:<port>` instead of the wildcard, which is what lets a port be
> forwarded to itself on loopback. `service_config.paperless.ftp_public_address` is that same
> address — `tcp_bind_address` — since it is the address the client reached Traefik on.

`quadlet/paperless-ftp.container.j2`:

```ini
[Unit]
Description=Paperless FTP drop (the scanner's inbox)

[Container]
Image=docker.io/stilliard/pure-ftpd:trixie-1.0.50
ContainerName=paperless-ftp
Pod=paperless.pod
Environment=TZ={{ tz }}
Environment=FTP_USER_NAME={{ service.config.ftp_user }}
Environment=FTP_USER_HOME=/usr/src/paperless/consume
{# The uid paperless-ngx runs as inside its own container. Both parts share the pod's
   user namespace, so a file the scanner uploads is written by the uid the consumer
   expects to own it -- and the consumer deletes it once the document is ingested. #}
Environment=FTP_USER_UID=1000
Environment=FTP_USER_GID=1000
Secret=ftp_password,type=env,target=FTP_USER_PASS
{# The image's own CMD with three flags added. `-S 2121` because Traefik owns 21 on the
   host; `-p` is the declared passive range, read from `tcp_ports` so the two cannot
   drift; `-P` is the address the PASV reply advertises. #}
{% set passive = (service.tcp_ports | selectattr('range', 'defined') | first).range %}
Exec=/run.sh -l puredb:/etc/pure-ftpd/pureftpd.pdb -E -j -R -S 2121 -P {{ service.config.ftp_public_address }} -p {{ passive[0] }}:{{ passive[1] }}
Volume={{ volumes.consume }}:/usr/src/paperless/consume
{# The image declares VOLUMEs for both; a tmpfs keeps podman from making an anonymous
   volume per start for a virtual-user database that is rebuilt from the environment
   every time anyway. #}
Tmpfs=/etc/pure-ftpd/passwd
Tmpfs=/home/ftpusers
{# No curl in the image, and its /bin/sh is dash, which has no /dev/tcp -- the same
   shape as paperless-tika's probe, which names the shell that does. #}
HealthCmd=bash -c "exec 3<>/dev/tcp/127.0.0.1/2121"
HealthOnFailure=kill

[Service]
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

`quadlet/paperless-app.container.j2`, four lines added:

```ini
Environment=PAPERLESS_CONSUMPTION_DIR=/usr/src/paperless/consume
{# Polling, not inotify: the file is written by another container through a bind mount,
   and inotify across that boundary is not something to bet a scan on. 10 s is the
   scanner's latency budget, not the platform's. #}
Environment=PAPERLESS_CONSUMER_POLLING=10
Environment=PAPERLESS_CONSUMER_RECURSIVE=false
Volume={{ volumes.consume }}:/usr/src/paperless/consume
```

`secrets.sops.yaml` gains `ftp_password` — generated, not `REPLACE_ME`: it is a free choice, like the kopia client passwords, and the only other place it exists is the scanner's configuration.

```bash
make sops FILE=hosts/storagebaby/services/paperless/secrets.sops.yaml
```

- [ ] **Step 4: Retire `paperless-upload`**

```bash
git rm -r hosts/storagebaby/services/paperless-upload
git rm hosts/test-a/services/paperless-upload hosts/test-ci/services/paperless-upload
```

and with it: the `service_config.paperless-upload` block in both test host files (done in Step 2), `test_service.py::test_uploader_reaches_paperless_through_traefik` and its `TRAEFIK_HOP_JS` constant, the `paperless-upload` row in the README services table, and the `scans` half of README operator step 7 (`chgrp -R scans …`, the `find … g+s`) — the `scans` bind and its group leave the platform with the service, and `/pool/shared/scans` stays on disk as data nothing serves.

Two consequences to record rather than paper over, both in the commit message and in Task 5's docs pass:

1. **The platform loses its only `.build` unit**, so nothing exercises the build-unit path in `units.yml` any more (`settled_builds`, build-before-pod ordering, a config change re-running the build). The code stays; its integration coverage does not. See "Open decisions".
2. **The uploader was the only container-to-Traefik hop test.** The kopia backup sidecars make the same hop on every converge (`https://kopia.<domain>` from inside a pod) and `test_backup_sidecar_snapshots_to_the_server` already asserts it end to end, so the mechanism stays covered; what goes is the explicit test named for it.

- [ ] **Step 5: The integration test**

`tests/integration/molecule/test-ci/tests/test_ftp.py`:

```python
"""The scanner path: FTP through Traefik into Paperless's consume directory.

One test, on purpose. Splitting "can log in", "can upload" and "the document appeared"
into three would make the first two pass on a platform where nothing is ever ingested --
and the thing this replaces (`paperless-upload`) is exactly a service that looked healthy
while doing nothing.
"""

import pytest
from conftest import run_as
from test_service import HOSTS, SPECS, load_spec, placed_tcp_entries, volume_path

PROBE = "storagebaby-ftp-probe.txt"


def paperless(host):
    hostname = host.check_output("uname -n")
    for owner, spec_path in SPECS:
        if spec_path.parent.name == "paperless" and owner in ("shared", hostname):
            return load_spec(spec_path), load_spec(HOSTS / hostname / "host.yml")
    pytest.skip("paperless is not placed on this host")


def documents(host) -> int:
    """How many documents paperless holds, asked of its database rather than its API.

    `/api/documents/` needs a user and a token, and inventing a superuser on a test host
    to read a count is a production hazard for a value the database states plainly. The
    row count is the same fact: the consumer creates exactly one row per ingested file.
    """
    r = run_as(host, "svc-paperless", "podman exec paperless-database "
               "psql -U paperless -d paperless -tAc 'select count(*) from documents_document'")
    assert r.rc == 0, r.stderr
    return int(r.stdout.strip())


def test_an_ftp_upload_becomes_a_document(host):
    spec, hostvars = paperless(host)
    entries = [e for e in placed_tcp_entries(host) if e[0] == "paperless"]
    assert entries, "paperless declares no tcp ports on this host"
    control = min(port for _, port, _ in entries)

    # The password only exists in the container's process: an env-type podman secret
    # shows as `*******` in `podman inspect`, which is the whole point of it.
    password = run_as(host, "svc-paperless", "podman exec paperless-ftp printenv FTP_USER_PASS").stdout.strip()
    assert password, "paperless-ftp runs without FTP_USER_PASS"
    user = spec["config"]["ftp_user"]
    consume = volume_path(hostvars, spec, "consume")

    before = documents(host)
    assert host.run(f"printf 'Storagebaby FTP probe\\n' > /tmp/{PROBE}").rc == 0
    # --ftp-pasv is curl's default; it is named here because passive mode is the whole
    # point: the data connection goes to a second port, through a second entrypoint.
    r = host.run(
        f"curl -sS --ftp-pasv --connect-timeout 20 --max-time 120 "
        f"-T /tmp/{PROBE} ftp://{user}:{password}@127.0.0.1:{control}/"
    )
    assert r.rc == 0, f"the upload failed: {r.stderr}"

    # Polled: the consumer wakes on its polling interval, then OCRs and files the
    # document. A minute is generous for a one-line text file on this VM.
    grown = host.run(
        "for _ in $(seq 60); do "
        f"c=$(runuser -u svc-paperless -- env XDG_RUNTIME_DIR=/run/user/$(id -u svc-paperless) "
        "podman exec paperless-database psql -U paperless -d paperless -tAc "
        "'select count(*) from documents_document'); "
        f"[ \"$c\" -gt {before} ] && break; sleep 5; done; echo $c"
    )
    journal = host.run("journalctl _SYSTEMD_USER_UNIT=paperless-app.service --no-pager | tail -60").stdout
    assert int(grown.stdout.strip()) > before, f"no document appeared after the upload\n{journal}"
    # And the inbox is empty again: the consumer deletes what it ingested, which is what
    # keeps the volume transient and out of the backup paths.
    assert host.run(f"ls -1 {consume}").stdout.strip() == "", f"{consume} still holds the upload"
```

- [ ] **Step 6: Paperless README**

New sections: the FTP part (image, why it, the pinned tag), the passive range and why each port is forwarded to itself, `ftp_public_address` and the failure mode when it is wrong (transfers hang, no error on either side), **no TLS** — "printers speak plain FTP on the LAN and explicit FTPS cannot be terminated by Traefik; the credentials and the documents cross the LAN in clear, and the exposure is one scanner account with write access to a consume directory" — the `consume` volume being transient and therefore absent from `backup.paths`, and the replacement of the old `paperless-upload` path (the drop location moves from the `/pool/shared/scans` Samba share to an FTP login).

- [ ] **Step 7: Run**

```bash
make test-static
make test-integration
MOLECULE_HOST=test-ci make test-integration
```

- [ ] **Step 8: Commit**

```bash
git add -A hosts tests README.md docs
git commit -m "Take scans by FTP into the paperless pod and retire paperless-upload"
```

---

### Task 5: Retire `samba/` and `snapraid/`, and write the phase down

Spec §4, §8, §7 step 5.

**Files:**

- Delete: `samba/` (whole tree), `snapraid/` (whole tree)
- Modify: `README.md`, `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-09-25-phase-4-storage-and-ftp-design.md` (status line)
- Modify: `ansible/roles/storage/README.md` (the adding-a-disk procedure lands here)

- [ ] **Step 1: Check the declaration against what is being deleted, then delete**

Before `git rm`, diff the two by hand — this is the last moment the deployed truth is in the repo:

```bash
grep -h What= snapraid/config/etc/systemd/system/*.mount
grep -e '^disk' -e '^parity' -e '^content' -e '^block_size' snapraid/config/etc/snapraid.conf
```

against `hosts/storagebaby/host.yml`'s `storage` block. Every device path, every mount point, every `disk <name> <path>/` and the parity path must match. The disk **names** especially: snapraid identifies a disk by the name in the config, and renaming `d1` to something else would make it treat the whole disk as new — a full parity rewrite on the first sync.

```bash
git rm -r samba snapraid
```

That takes with it: the `samba` maintenance plugin, the five `snapshot-*` plugins (nextcloud, immich, paperless, openproject, mailflow — the services they snapshotted live here now and Kopia is the only backup path), both `install.sh` files, the stowed units and `smb.conf`.

- [ ] **Step 2: README**

- Services table: drop the `paperless-upload` row (Task 4 did) and add to the paperless row that it also takes FTP on 21 → 2121 with a passive range.
- Layout section: `hosts/<host>/host.yml` now really does carry "disks/snapraid" — drop the "later" from that line.
- New section **"Storage"**, after "Services": the `storage` block, the role running before `host_base`, the mount assertion that replaced `mountpoints`, the maintenance timer and where its scripts and logs live, and the mail.
- New section **"Adding a disk"** (the procedure from the deleted `snapraid/README.md`, rewritten): partition and `mkfs` by hand (`parted`, `mkfs.ext4`), read the stable path (`lsblk -o NAME,SIZE,PARTUUID,PARTLABEL`), add the entry to `host.yml`, push. The role mounts it, adds it to the pool and to `snapraid.conf`; the first `snapraid sync` after that writes its content file. Note that **adding a branch changes `pool.mount`**, so the converge that adds a disk remounts the pool — do it with the services stopped.
- Operator steps, new entries (spec §8):
  1. `hosts/storagebaby/secrets/mail.sops.yaml` → `smtp_password`, and `storage.mail.smtp_host`/`smtp_user` in `host.yml`, which are `REPLACE_ME`.
  2. `hosts/storagebaby/services/paperless/secrets.sops.yaml` → `ftp_password` (generated, free choice — it is what the scanner is configured with).
  3. `service_config.paperless.ftp_public_address` → storagebaby's LAN address.
  4. Point the scanner at `<storagebaby>:21`, user `config.ftp_user`, passive.
  5. **The first converge remounts `/pool`.** The role renders `pool.mount` from the declaration, which differs from the stowed one (its `What=` is the glob `/mnt/data/*`, and the description differs), so the unit changes and the pool is remounted once. Every service with a pool-class volume loses its bind mount across that. Run `ansible-playbook … --check --diff --limit storagebaby` first to see exactly what changes, stop the services, converge, start them.
  6. **It remounts the four branch units with it, so `smb` and `nmb` have to be stopped too.** Each rendered disk unit's `Description=` differs from the stowed one (`d1 (/mnt/data/data1)` against `Data Disk 1 mount`), so all four change; a changed branch takes the pool down first, and `systemctl stop pool.mount` is a plain `umount` that fails with `EBUSY` while anything holds `/pool` open — which `smbd` does, serving `/pool/shared/*`, until step 7 retires it. So the one planned outage is: stop every pool-class service, `systemctl stop smb nmb`, converge, start them again.
  7. After the first successful converge, retire the hand-stowed half: `systemctl disable --now storage-maintenance.timer` is **not** it — the unit name is the same, so the role's file has already replaced it; what must go is `/opt/scripts/storage-maintenance-unattended.sh` and the old `~/StorageBaby` checkout the old wrapper pointed at. Verify with `systemctl cat storage-maintenance.service` that `ExecStart` is `/opt/storagebaby/maintenance/storage-maintenance-unattended.sh`, then `rm -rf /opt/scripts`.
  8. Samba: `systemctl disable --now smb nmb`, then remove the package by hand (`pacman -Rns samba`) — the role does not remove a package it never installed. The share trees (`/pool/shared/utility`, `/pool/shared/maki`, `/pool/mk`, `/pool/jlk/backups`, `/pool/shared/scans`) stay on disk, untouched, with nothing serving them.
- Close the Phase 2 open item on shared-tree permissions: only jellyfin's `media` tree remains, `o+rX` as documented.
- "What is left of the old repository is Phase 4" → nothing is left; the root holds no service directories any more.

- [ ] **Step 3: CLAUDE.md**

- "Managing services": `host.yml` keys — `mountpoints` out, `storage` in; `service.yml` keys — `tcp_ports` in; the retired `paperless-upload` out of the migrated list; the placement list updated.
- "Storage architecture": the pool create policy is **`pfrd`**, not `eplfs` — the documented `eplfs` never matched the deployed `pool.mount` and the correction is part of this phase. Rewrite the paragraph to describe `pfrd` (proportional free random distribution: a branch is chosen at random, weighted by free space), and point at `hosts/<host>/host.yml` as the source of the option string.
- "Storage maintenance pipeline": paths become `/opt/storagebaby/maintenance/`, the timer and unit are rendered by the role, the plugin set is `storage.snapraid.maintenance.stop_services` rather than a directory of hand-written scripts, the `snapshot-*` and `samba` plugins are gone, and the manual invocations change accordingly.
- "Networking": Traefik also owns the declared TCP entrypoints; a service's `tcp_ports` become one entrypoint and one `HostSNI('*')` router each; the unprivileged-port sysctl follows the lowest declared port (21 on storagebaby).
- "Adding a new disk": point at the README section, not at the deleted `snapraid/README.md`.
- Drop the whole "The root `install.sh` … Phase 4 ports mounts, mergerfs, snapraid and samba into Ansible roles" paragraph; it is done.

- [ ] **Step 4: Spec status**

`docs/superpowers/specs/2026-09-25-phase-4-storage-and-ftp-design.md`, line 3:

```
Status: implemented (2026-09-25)
```

- [ ] **Step 5: Full runs, both hosts**

```bash
make fmt-check
make test-static
make test-integration
MOLECULE_HOST=test-ci make test-integration
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Retire samba and the stowed snapraid tree, and document the storage phase"
```

---

## Self-review notes

**Spec coverage map**

| Spec section                                     | Task                                                               |
| ------------------------------------------------ | ------------------------------------------------------------------ |
| §2 `storage` contract — disks, parity, pool      | Task 1 (contract test, role, all three hosts)                      |
| §2 — `snapraid`, `mail`, `mountpoints` retired   | Task 2 (`snapraid`/`mail`), Task 1 (`mountpoints`)                 |
| §3.1 packages                                    | Task 1 (mergerfs, snapraid), Task 2 (smartmontools, msmtp, mutt)   |
| §3.2–3.4 mounts, pool, assertion                 | Task 1                                                             |
| §3.5 snapraid.conf, scripts, timer               | Task 2                                                             |
| §3.6 msmtprc                                     | Task 2                                                             |
| §3.7 change handling, the pool warning           | Task 1 (warning + restart rules), Task 2 (scripts need no restart) |
| §4 retirements — `samba/`, `snapraid/`, plugins  | Task 5                                                             |
| §4 — `paperless-upload`, the `scans` bind        | Task 4                                                             |
| §4 — docs, the adding-a-disk procedure           | Task 5                                                             |
| §5 `tcp_ports` contract, playbook, traefik, role | Task 3                                                             |
| §5 Paperless — `-ftp` part, `consume`, no TLS    | Task 4                                                             |
| §6 harness — disks, partitioning, `/pool/apps`   | Task 1                                                             |
| §6 harness — SMTP sink, mail secret              | Task 2                                                             |
| §6 static — mount units, snapraid.conf, msmtprc  | Task 1 (units), Task 2 (conf, msmtprc)                             |
| §6 static — `tcp_ports`, entrypoints, routers    | Task 3                                                             |
| §6 static — retired directories and key gone     | Task 1 (`mountpoints`), Task 5 (directories)                       |
| §6 integration — mounts, pool, one-branch write  | Task 1                                                             |
| §6 integration — maintenance, parity, mail       | Task 2                                                             |
| §6 integration — unmounted disk, pool option     | Task 2 (both in `test_deploy.py`)                                  |
| §6 integration — Traefik TCP, FTP upload         | Task 3 (listeners), Task 4 (upload → document)                     |
| §7 migration order                               | Tasks 1–5, in order, one deliberate change (below)                 |
| §8 operator items                                | Task 5 README, with the pool remount added                         |

**Deliberate deviations from the spec, each with its reason**

1. **storagebaby's `storage` block is written in Task 1, not Task 5.** `mountpoints` and the block are two halves of one safety property; removing the first without the second leaves a window in which a converge with `/pool` unmounted recreates `/pool/apps` on the root filesystem. Task 5 keeps the verification against the deleted units and the operator items.
2. **The harness supplies the device by writing a GPT partition label, not by generating an inventory variable.** `test_deploy.py` converges through `ansible-pull`, which sees no Molecule inventory; a device path that existed only there would render a different `.mount` on that run and remount `/pool` under the running services.
3. **`snapraid.maintenance.stop_services` replaces a hand-copied `plugins/jellyfin/`.** `test-ci` does not place jellyfin, and a verbatim plugin would stop a service that is not there and fail every maintenance run on that host. It also retires the `samba` plugin by construction.
4. **`mail` gains optional `tls` and `auth`.** With `auth on` and no TLS, msmtp considers only SCRAM — against the test sink it would never authenticate. Both default to the production values (`tls: true`, `auth: on`), so storagebaby's block is the spec's.
5. **~~`mergerfs`, `snapraid` and `mergerfs.balance` come from upstream artefacts, not from pacman.~~ Superseded 2026-09-26 by JLK's ruling (ledger):** they come from pacman after all — `mergerfs` from Chaotic-AUR, which the role configures the project's documented way, and `snapraid` and `mergerfs-tools-git`, which Chaotic-AUR does not carry, built from the AUR by the role itself (`tasks/aur_build.yml`: build user, `base-devel`, one clone at a pinned AUR commit, `makepkg`, `pacman -U`). Pins live in the role's defaults and are bumped like an image tag, `state: present` never upgrades, and a host that already has these packages keeps them. Verified 2026-09-26 against `chaotic-aur.db`: 3166 packages, `mergerfs-2.42.0-2` present, no `snapraid`, no `mergerfs-tools-git`. The plan's unverified `mergerfs_sha256` and its snapraid release-package URL are both gone; the snapraid AUR pin is commit `94c51545d0ce36dee0402dc19c1b3cc4336b3ae9` = 14.9-1.
6. **The FTP integration test counts documents in the database rather than through `/api/documents/`.** The API needs a user and a token; inventing a superuser on a test host to read a count is a production hazard for a fact the database states plainly.

**Names, consistent across the tasks:** `storage.disks[].name`, `storage.pool.mount`, `storage_devices`, `pool_unit`, `mount_unit()` (python) / the `regex_replace` pair (Jinja), `storage.snapraid.maintenance.stop_services`, `storage.mail.smtp_password` (in `mail.sops.yaml`), `/opt/storagebaby/maintenance/`, `tcp_ports`, `tcp_enabled`, `placed_tcp_ports`, entrypoint `tcp-<port>`, route file `<name>-tcp.yml`, router `<name>-tcp-<port>`, `paperless-ftp`, volume `consume`, `config.ftp_user`, `config.ftp_public_address`, secret `ftp_password`, `placed_tcp_entries()` (integration), `conftest.tcp_ports()` / `host_cfg()` / `storage_of()` (static).

---

## Open decisions the implementer must not make alone

1. **storagebaby's `ftp_public_address`.** The scanner's route to the host — a LAN address, or a name that resolves to it on the LAN. Not knowable from the repo; written as `REPLACE_ME` and listed as an operator item. If `*.home.klees.io` resolves to the LAN address from inside the network, a name is the better value (it survives a DHCP change).
2. **The FTP image, if the Task 4 probe fails.** Primary `docker.io/stilliard/pure-ftpd:trixie-1.0.50`, fallback `docker.io/delfer/alpine-ftp-server:latest` — and the fallback means pinning to `latest`, which this repo does not do elsewhere. If the probe fails, stop and decide between the unpinned image, a digest pin of it, and building an FTP image in-repo with a `.build` unit (which would also give the platform back the `.build` coverage item 4 loses).
3. **The platform's only `.build` unit goes with `paperless-upload`.** Nothing will exercise the build-unit path in `ansible/roles/service/tasks/units.yml` (`settled_builds`, build-before-pod ordering, rebuild on a config change) any more. Either accept it and say so in the role README, or keep a minimal `.build` fixture service on the test hosts. Not a decision to make silently while deleting a folder.
4. **The first converge on storagebaby remounts `/pool`.** The rendered `pool.mount` cannot be byte-identical to the stowed one (its `What=` is the glob `/mnt/data/*`, the role's is the explicit branch list). Either accept a scheduled outage with the services stopped, or decide that `pool.options`/`What=` should be reproduced verbatim through an extra `pool.branches` key. The plan assumes the first.
5. **Where the maintenance mail goes and through which relay.** `storage.mail.smtp_host`/`smtp_user` are `REPLACE_ME`; the deployed wrapper's `mutt` used whatever MTA the host had, which this repo never captured. Needs the real relay before the first converge, or the maintenance run's mail step fails nightly while the run itself succeeds.
6. **Whether `test-a` should keep running the maintenance suite.** A full `snapraid sync`, scrub and SMART pass on four 1 GB disks costs a couple of minutes per run on both hosts. If CI time becomes the constraint, the honest lever is to run the maintenance case only on `test-a` and leave `test-ci` with mounts, pool and FTP — a placement decision, not a code one.
