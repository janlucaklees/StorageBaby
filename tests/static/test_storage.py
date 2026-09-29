"""The `storage` block in `host.yml`, and the mount units the role renders from it.

Everything here is derived from the declaration: the shape it has to have, and that the
units rendered from it are ones systemd accepts. The role itself never partitions or
formats, so the only thing the tracked file can get wrong is the declaration -- which is
exactly what a converge would then mount, or refuse to.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO, host_cfg, host_names, mount_unit, placements, storage_of

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


SNAPRAID_KEYS = {"block_size", "excludes", "maintenance"}
MAINTENANCE_KEYS = {"on_calendar", "balance_threshold", "scrub_percent", "scrub_older_days", "stop_services"}
MAIL_REQUIRED = {"to", "from", "smtp_host", "smtp_port", "smtp_user"}
# Optional, and defaulted by the msmtprc template to the production values (`tls on`,
# `auth on`). A host whose relay is a sink on its own loopback says so.
MAIL_OPTIONAL = {"tls", "auth"}


@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_snapraid_block_shape(host):
    snapraid = storage_of(host)["snapraid"]
    assert set(snapraid) == SNAPRAID_KEYS, f"{host}: {set(snapraid) ^ SNAPRAID_KEYS}"
    assert isinstance(snapraid["block_size"], int), f"{host}: block_size must be an integer"
    assert all(isinstance(e, str) and e for e in snapraid["excludes"]), f"{host}: {snapraid['excludes']}"
    m = snapraid["maintenance"]
    assert set(m) == MAINTENANCE_KEYS, f"{host}: {set(m) ^ MAINTENANCE_KEYS}"
    assert 0 <= m["balance_threshold"] <= 100, f"{host}: balance_threshold {m['balance_threshold']}"
    assert 0 <= m["scrub_percent"] <= 100, f"{host}: scrub_percent {m['scrub_percent']}"
    assert isinstance(m["scrub_older_days"], int), f"{host}: scrub_older_days must be an integer"
    # Handed to systemd itself: an unparseable schedule is a timer that never fires, and
    # nothing on the host would report that -- the run would simply not happen.
    r = subprocess.run(["systemd-analyze", "calendar", m["on_calendar"]], capture_output=True, text=True)
    assert r.returncode == 0, f"{host}: on_calendar {m['on_calendar']!r} is no systemd calendar: {r.stderr}"
    # A plugin for a service that is not here fails every maintenance run on this host, at
    # 02:00, for as long as nobody reads the mail.
    placed = {p.name for p in placements() if p.host == host}
    unknown = set(m["stop_services"]) - placed
    assert not unknown, f"{host}: stop_services names services not placed here: {sorted(unknown)}"


@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_mail_block_shape(host):
    mail = storage_of(host)["mail"]
    unknown = set(mail) - MAIL_REQUIRED - MAIL_OPTIONAL
    assert not unknown, f"{host}: unknown mail keys {sorted(unknown)}"
    assert MAIL_REQUIRED <= set(mail), f"{host}: mail is missing {sorted(MAIL_REQUIRED - set(mail))}"
    assert isinstance(mail["smtp_port"], int), f"{host}: smtp_port must be an integer"
    assert "@" in mail["to"] and "@" in mail["from"], f"{host}: {mail['to']} -> {mail['from']}"
    if "tls" in mail:
        assert isinstance(mail["tls"], bool), f"{host}: tls must be a boolean"


def _stub_tree(conf: str, root: Path, other: Path) -> str:
    """The rendered config with every absolute path moved under `root`, and the tree made.

    snapraid only ever sees absolute paths, so a real run against the rendered file means
    prefixing each of them into a temporary directory: the disk roots become directories
    holding one small file, and the content files are written where the prefixed lines say.
    Every line is `<keyword> <rest>`, and only the paths start with a slash.

    `other` takes the *first* content file, and it has to be on a different device than
    `root`: snapraid refuses an array whose content files all live on one disk ("You must
    have at least 2 'content' files in different disks"), which a stub tree in a single
    temporary directory otherwise is. Redundant copies of the file list on different
    hardware is the point of that rule on a real host, and the rendered config satisfies it
    there by construction -- `/etc/snapraid.content` plus one per data disk.
    """
    out = []
    first_content = True
    for line in conf.splitlines():
        keyword, _, rest = line.partition(" ")
        if keyword == "disk":
            name, _, path = rest.partition(" ")
            target = root / path.lstrip("/")
            target.mkdir(parents=True, exist_ok=True)
            (target / "probe.bin").write_bytes(b"storagebaby" * 4096)
            out.append(f"disk {name} {target}/")
        elif rest.startswith("/"):
            prefix = other if (keyword == "content" and first_content) else root
            first_content = first_content and keyword != "content"
            target = prefix / rest.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            out.append(f"{keyword} {target}")
        else:
            out.append(line)
    return "\n".join(out) + "\n"


@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_snapraid_conf_renders_and_parses(rendered, tmp_path, host):
    """The rendered file is one snapraid itself accepts, not one that merely looks right.

    Checked by running a real `snapraid status` against a stub tree, which is snapraid
    loading the config, running its self-test, resolving every declared path and reporting
    the array it found -- and it names each disk by the name the config gave it, which is
    what the assertion reads.

    `sync` is deliberately *not* run here: it refuses an array whose disks share a device
    (and `--force-device` is rejected for `sync`), and nothing in a container can give four
    stub directories four devices. A real sync, on real separate disks, is what the
    maintenance case in `tests/integration/.../test_storage.py` does.
    """
    storage = host_cfg(host)["storage"]
    conf = (rendered / host / "storage" / "snapraid.conf").read_text()
    for disk in storage["disks"]:
        assert f"disk {disk['name']} {disk['mount']}/" in conf, conf
        assert f"content {disk['mount']}/snapraid.content" in conf, conf
    assert "content /etc/snapraid.content" in conf, conf
    for i, parity in enumerate(storage["parity"], start=1):
        prefix = "parity" if i == 1 else f"{i}-parity"
        assert f"{prefix} {parity['mount']}/snapraid.parity" in conf, conf
    for exclude in storage["snapraid"]["excludes"]:
        assert f"exclude {exclude}" in conf, conf
    assert f"block_size {storage['snapraid']['block_size']}" in conf, conf

    root = tmp_path / "root"
    # A second device for the first content file -- see `_stub_tree`. /dev/shm is tmpfs in
    # the devtools container and /tmp is not, which is the only pair of writable devices
    # there is in there.
    other = Path("/dev/shm") / f"storagebaby-stub-{host}"
    if other.parent.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("/dev/shm and the temporary directory are one device: no stub array is possible")
    shutil.rmtree(other, ignore_errors=True)
    stub = tmp_path / "snapraid.conf"
    stub.write_text(_stub_tree(conf, root, other))
    r = subprocess.run(["snapraid", "-c", str(stub), "status"], capture_output=True, text=True)
    shutil.rmtree(other, ignore_errors=True)
    assert r.returncode == 0, f"{host}: snapraid rejected the rendered config\n{r.stdout}{r.stderr}"
    for disk in storage["disks"]:
        assert re.search(rf"^\s*\S+\s+.*\s{re.escape(disk['name'])}$", r.stdout, flags=re.M), (
            f"{host}: snapraid's status names no disk {disk['name']}\n{r.stdout}"
        )


@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_msmtprc_renders_without_a_password(rendered, host):
    mail = host_cfg(host)["storage"]["mail"]
    text = (rendered / host / "storage" / "msmtprc").read_text()
    for expected in [
        f"host {mail['smtp_host']}",
        f"port {mail['smtp_port']}",
        f"from {mail['from']}",
        f"user {mail['smtp_user']}",
        f"auth {mail.get('auth', 'on')}",
        "tls on" if mail.get("tls", True) else "tls off",
        # The journal, not a file msmtp creates on its first send and then grows forever
        # with nothing rotating it.
        "syslog LOG_MAIL",
    ]:
        assert expected in text, f"{host}: {expected!r} not in\n{text}"
    assert "logfile" not in text, f"{host}: msmtp writes an unrotated log of its own\n{text}"
    # The render path has no age key and decrypts nothing; the placeholder is what says so.
    # A real value here would mean the static run had read a secret.
    assert "password REPLACE_ME" in text, text


@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_maintenance_parameters_are_rendered_and_not_baked_into_the_scripts(rendered, host):
    """Every parameter reaches the scripts through one rendered file, and only through it.

    The scripts are copied byte for byte out of `files/maintenance/`, so a threshold that had
    been rendered *into* one of them would be a value living in two places -- and the second
    one is a script an operator also runs by hand.
    """
    storage = host_cfg(host)["storage"]
    m = storage["snapraid"]["maintenance"]
    env = dict(
        line.split("=", 1)
        for line in (rendered / host / "storage" / "maintenance.env").read_text().splitlines()
        if "=" in line
    )
    assert env["STORAGE_POOL"] == storage["pool"]["mount"]
    assert env["BALANCE_TARGET_PERCENTAGE"] == str(m["balance_threshold"])
    assert env["SCRUB_PERCENT"] == str(m["scrub_percent"])
    assert env["SCRUB_OLDER_DAYS"] == str(m["scrub_older_days"])
    assert env["MAINTENANCE_MAIL_TO"] == storage["mail"]["to"]
    assert env["MAINTENANCE_MAIL_FROM"] == storage["mail"]["from"]
    for script in sorted((REPO / "ansible/roles/storage/files/maintenance").rglob("*.sh")):
        assert "{{" not in script.read_text(), f"{script.name} carries a template expression"


@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_maintenance_units_verify(rendered, host):
    """The timer and its service are units systemd accepts, with the declared schedule.

    `systemd-analyze verify` resolves `ExecStart=` on the filesystem, so the script the unit
    names is installed at its real path from the role's own `files/` first -- which makes
    this the other half of the claim: the unit points at a script the role ships, under the
    name it will carry on a host. The devtools container is throwaway, so writing under /opt
    there costs nothing.
    """
    unit_dir = rendered / host / "storage"
    storage = host_cfg(host)["storage"]
    source = REPO / "ansible/roles/storage/files/maintenance"
    installed = Path("/opt/storagebaby/maintenance")
    installed.mkdir(parents=True, exist_ok=True)
    for script in sorted(source.glob("*.sh")):
        target = installed / script.name
        target.write_text(script.read_text())
        target.chmod(0o755)

    timer = (unit_dir / "storage-maintenance.timer").read_text()
    assert f"OnCalendar={storage['snapraid']['maintenance']['on_calendar']}" in timer, timer
    assert "Persistent=true" in timer, timer
    service = (unit_dir / "storage-maintenance.service").read_text()
    assert f"ExecStart={installed}/storage-maintenance-unattended.sh" in service, service

    # The pool the run requires is also what can keep it from starting at all, in which case
    # ExecStart= -- and the wrapper's own mail with it -- never runs. The notifier is the
    # only thing that reports that night, so the unit has to name it and the notifier has to
    # point at a script the role ships, under the name it will carry on a host.
    assert "OnFailure=storage-maintenance-failed.service" in service, service
    notifier = (unit_dir / "storage-maintenance-failed.service").read_text()
    assert f"ExecStart={installed}/storage-maintenance-failed.sh" in notifier, notifier

    # The mount units come along: the service `Requires=` the pool, and verify reports a
    # requirement it cannot find as an error.
    units = [
        str(unit_dir / "storage-maintenance.timer"),
        str(unit_dir / "storage-maintenance.service"),
        str(unit_dir / "storage-maintenance-failed.service"),
        str(unit_dir / mount_unit(storage["pool"]["mount"])),
        *(str(unit_dir / mount_unit(e["mount"])) for e in entries(storage)),
    ]
    r = subprocess.run(["systemd-analyze", "verify", *units], capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stdout + r.stderr
