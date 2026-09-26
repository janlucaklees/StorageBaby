"""The host's filesystems: the declared mounts, and the union over them.

Read from the VM's own `host.yml` in the repo, like every other verifier here, so a
host that declares no storage skips instead of asserting something it never asked for.
"""

from pathlib import Path

import pytest
import yaml

HOSTS = Path("/repo/hosts")


def storage(host) -> dict:
    # `uname -n`, not `hostname`: the Arch cloud image ships coreutils but not inetutils.
    with (HOSTS / host.check_output("uname -n") / "host.yml").open() as fh:
        cfg = yaml.safe_load(fh)
    if "storage" not in cfg:
        pytest.skip("this host declares no storage")
    return cfg["storage"]


def unit(path: str) -> str:
    return path.lstrip("/").replace("/", "-") + ".mount"


def test_tools_are_installed(host):
    """mergerfs from Chaotic-AUR, snapraid built from the AUR by the role itself."""
    storage(host)
    assert host.run("mergerfs --version").rc == 0
    assert host.run("snapraid --version").rc == 0


def test_the_aur_build_left_no_privileges_behind(host):
    """The build user exists, owns its clones, and can do nothing else.

    The whole reason `aur_build.yml` reads dependencies out of `.SRCINFO` instead of
    running `makepkg -s` is that `-s` needs pacman as root. A NOPASSWD pacman rule for a
    service account is a root-equivalent grant that outlives the build, so the claim
    worth checking on the VM is that no such rule was written and the account cannot log
    in.
    """
    storage(host)
    user = host.user("aurbuild")
    assert user.shell.endswith("nologin"), user.shell
    rules = host.run("grep -rl aurbuild /etc/sudoers /etc/sudoers.d /etc/doas.conf 2> /dev/null")
    assert rules.stdout.strip() == "", f"the build user was granted something: {rules.stdout}"


def test_every_declared_mount_is_active(host):
    s = storage(host)
    for entry in s["disks"] + s.get("parity", []):
        assert host.service(unit(entry["mount"])).is_enabled, entry["name"]
        assert host.run(f"mountpoint -q -- {entry['mount']}").rc == 0, entry["mount"]
        assert host.file(entry["device"]).exists, entry["device"]


def test_pool_is_mergerfs_over_the_declared_branches(host):
    s = storage(host)
    mount = s["pool"]["mount"]
    options = dict(o.split("=", 1) for o in s["pool"]["options"].split(",") if "=" in o)
    assert host.run(f"mountpoint -q -- {mount}").rc == 0
    assert host.check_output(f"findmnt -no FSTYPE {mount}") == "fuse.mergerfs"
    # `fsname=` is what mergerfs reports as the mount source, so this is the declared
    # option string reaching the running process rather than just the unit file.
    assert host.check_output(f"findmnt -no SOURCE {mount}") == options["fsname"]
    # The kernel's option string is not mergerfs's: the policy options live in the fuse
    # process, which publishes its whole running configuration as extended attributes on
    # its control file, `<mountpoint>/.mergerfs` -- not on the mount point itself, where
    # mergerfs 2.x answers "No such attribute".
    #
    # `category.create` is asserted because it is what actually decides which branch a
    # file lands on, and `branches` because it is mergerfs's own account of the union it
    # assembled. The other options are not asserted against their own spelling:
    # `moveonenospc=true` comes back as the policy name it resolved to and `minfreespace`
    # as a byte count, so comparing them to the declared string would be a test of
    # mergerfs's formatting rather than of the declaration.
    ctl = f"{mount}/.mergerfs"
    got = host.check_output(f"getfattr --only-values -n user.mergerfs.category.create {ctl}")
    assert got.strip() == options["category.create"], got
    branches = host.check_output(f"getfattr --only-values -n user.mergerfs.branches {ctl}")
    assert [b.split("=")[0] for b in branches.strip().split(":")] == [d["mount"] for d in s["disks"]], branches
    # And the unit systemd holds carries the whole declared string, byte for byte.
    unit_file = host.file(f"/etc/systemd/system/{unit(mount)}")
    assert f"Options={s['pool']['options']}" in unit_file.content_string
    assert f"What={':'.join(d['mount'] for d in s['disks'])}" in unit_file.content_string


def test_a_file_written_to_the_pool_lands_on_exactly_one_branch(host):
    """The union is a union, not a copy: one file, one branch, visible through the pool."""
    s = storage(host)
    probe = f"{s['pool']['mount']}/.storagebaby-branch-probe"
    branches = [f"{d['mount']}/.storagebaby-branch-probe" for d in s["disks"]]
    host.run("rm -f " + " ".join([probe] + branches))
    assert host.run(f"sh -c 'echo probe > {probe}'").rc == 0
    on = [d["mount"] for d in s["disks"] if host.file(f"{d['mount']}/.storagebaby-branch-probe").exists]
    assert host.file(probe).content_string.strip() == "probe"
    host.run(f"rm -f {probe}")
    assert len(on) == 1, f"the probe landed on {on}"


def test_the_storage_roots_live_on_the_pool(host):
    """The property the whole role exists to protect, checked on the running host.

    `host_base` creates the pool class root, and it runs *after* this role. If the pool
    were not mounted, that root would be an ordinary directory on the root filesystem and
    every service volume under it would be too -- which is exactly the silent failure the
    mount assertion is there to prevent, and `findmnt` is what can tell the difference.
    """
    s = storage(host)
    with (HOSTS / host.check_output("uname -n") / "host.yml").open() as fh:
        root = yaml.safe_load(fh)["storage_roots"]["pool"]
    assert host.file(root).is_directory, root
    assert host.check_output(f"findmnt -no TARGET --target {root}") == s["pool"]["mount"]
