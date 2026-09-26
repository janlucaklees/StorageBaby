"""The `storage` block in `host.yml`, and the mount units the role renders from it.

Everything here is derived from the declaration: the shape it has to have, and that the
units rendered from it are ones systemd accepts. The role itself never partitions or
formats, so the only thing the tracked file can get wrong is the declaration -- which is
exactly what a converge would then mount, or refuse to.
"""

import re
import subprocess

import pytest

from conftest import REPO, host_cfg, host_names, mount_unit, storage_of

STORAGE_KEYS = {"disks", "parity", "pool", "snapraid", "mail"}
DISK_KEYS = {"name", "device", "mount", "fstype"}
# Optional, and defaulted by the template to `defaults`: a disk that wants `noatime` or
# `nofail` says so in `host.yml` instead of making every disk on every host carry it.
DISK_OPTIONAL_KEYS = {"options"}
POOL_KEYS = {"mount", "options"}
NAME_RE = re.compile(r"^[a-z][a-z0-9]*$")
# Lowercase letters, digits and slashes only: `mount_unit` escapes a path by replacing
# every slash with a dash, which is systemd's own escape *only* for a path that carries
# none of the characters systemd would hex-escape. A `/mnt/data-1` would render
# `mnt-data-1.mount`, a unit systemd reads as `/mnt/data/1`.
MOUNT_RE = re.compile(r"^(/[a-z0-9]+)+$")
FSTYPES = {"ext4", "xfs"}

STORAGE_HOSTS = [h for h in host_names() if storage_of(h)]


def entries(storage: dict) -> list[dict]:
    """Every filesystem the role mounts: the data disks and the parity disks."""
    return storage["disks"] + storage.get("parity", [])


@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_storage_block_shape(host):
    storage = storage_of(host)
    assert set(storage) <= STORAGE_KEYS, f"{host}: unknown storage keys {set(storage) - STORAGE_KEYS}"
    assert storage["disks"], f"{host}: a storage block needs at least one disk"
    for e in entries(storage):
        assert DISK_KEYS <= set(e), f"{host}/{e.get('name')}: a disk needs {sorted(DISK_KEYS)}"
        unknown = set(e) - DISK_KEYS - DISK_OPTIONAL_KEYS
        assert not unknown, f"{host}/{e['name']}: unknown disk keys {sorted(unknown)}"
        options = e.get("options", "defaults")
        assert isinstance(options, str) and options.strip(), f"{host}/{e['name']}: options must be a mount option string"
        assert NAME_RE.match(e["name"]), f"{host}: invalid disk name {e['name']!r}"
        assert e["device"].startswith("/dev/"), f"{host}/{e['name']}: device must be under /dev"
        assert MOUNT_RE.match(e["mount"]), f"{host}/{e['name']}: {e['mount']!r} is not a plain lowercase path"
        assert e["fstype"] in FSTYPES, f"{host}/{e['name']}: fstype {e['fstype']!r}"
    names = [e["name"] for e in entries(storage)]
    assert len(set(names)) == len(names), f"{host}: duplicate disk names {names}"
    mounts = [e["mount"] for e in entries(storage)]
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
    expected = {mount_unit(e["mount"]) for e in entries(storage)}
    expected.add(mount_unit(storage["pool"]["mount"]))
    present = {f.name for f in unit_dir.iterdir()}
    assert expected <= present, sorted(present)
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
    for e in entries(storage):
        assert mount_unit(e["mount"]) in text, f"{host}: pool does not require {e['name']}"


@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_disk_units_name_the_declared_device(rendered, host):
    """The one thing a wrong `.mount` would do quietly: mount something else there.

    `Where=` has to be the path the unit name escapes -- systemd refuses a unit where the
    two disagree, which is what `verify` above catches -- and `What=` has to be the
    device the tracked file declares, not a guess derived from the mount point. `Options=`
    is the optional per-entry `options`, or `defaults` when the entry declares none.
    """
    storage = host_cfg(host)["storage"]
    for e in entries(storage):
        text = (rendered / host / "storage" / mount_unit(e["mount"])).read_text()
        assert f"What={e['device']}" in text, text
        assert f"Where={e['mount']}" in text, text
        assert f"Type={e['fstype']}" in text, text
        assert f"Options={e.get('options', 'defaults')}" in text, text
