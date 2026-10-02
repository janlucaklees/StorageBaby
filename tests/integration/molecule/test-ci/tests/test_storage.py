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


def maintenance(host) -> dict:
    """The host's `storage.snapraid.maintenance` block."""
    return storage(host)["snapraid"]["maintenance"]


def top_unit(host, service: str) -> str:
    """`<name>-pod.service` when the service is a pod, `<name>.service` otherwise.

    The same resolution the hook scripts and `storagebaby-svc` do -- a pod service has
    no `<name>.service` at all, and `systemctl` answers for a unit it does not know in a
    way that would let a check pass without checking anything.
    """
    listed = host.run(f"systemctl --user -M svc-{service}@ list-unit-files {service}-pod.service")
    return f"{service}-pod.service" if f"{service}-pod.service" in listed.stdout else f"{service}.service"


def test_snapraid_conf_declares_every_disk(host):
    s = storage(host)
    conf = host.file("/etc/snapraid.conf")
    assert conf.exists and conf.mode == 0o644, conf.mode
    for disk in s["disks"]:
        assert f"disk {disk['name']} {disk['mount']}/" in conf.content_string, conf.content_string
        assert f"content {disk['mount']}/snapraid.content" in conf.content_string, conf.content_string
    assert f"parity {s['parity'][0]['mount']}/snapraid.parity" in conf.content_string, conf.content_string
    assert f"block_size {s['snapraid']['block_size']}" in conf.content_string, conf.content_string


def test_the_maintenance_scripts_are_installed_with_their_parameters(host):
    """The scripts, the rendered parameter file, and a plugin directory per stopped service.

    The env file is the whole parameter path: the scripts carry no rendered value, so a
    threshold that did not arrive here did not arrive at all.
    """
    s = storage(host)
    m = maintenance(host)
    for script in [
        "storage-maintenance.sh",
        "storage-maintenance-unattended.sh",
        "storage-maintenance-failed.sh",
        "sync.sh",
        "scrub.sh",
        "balance_disks.sh",
    ]:
        f = host.file(f"/opt/storagebaby/maintenance/{script}")
        assert f.exists and f.mode == 0o755 and f.user == "root", f"{script}: {f.mode}"
    env = host.file("/opt/storagebaby/maintenance/maintenance.env").content_string
    assert f"STORAGE_POOL={s['pool']['mount']}" in env, env
    assert f"BALANCE_TARGET_PERCENTAGE={m['balance_threshold']}" in env, env
    assert f"SCRUB_PERCENT={m['scrub_percent']}" in env, env
    assert f"SCRUB_OLDER_DAYS={m['scrub_older_days']}" in env, env
    assert f"MAINTENANCE_MAIL_TO={s['mail']['to']}" in env, env
    assert f"MAINTENANCE_MAIL_FROM={s['mail']['from']}" in env, env

    # Exactly the declared set, no more: a directory left behind from a service this host no
    # longer stops would be run every night by a script nothing points at.
    present = sorted(host.run("ls -1 /opt/storagebaby/maintenance/plugins").stdout.split())
    assert present == sorted(m["stop_services"]), present
    for service in m["stop_services"]:
        for hook in ("on-before-balance.sh", "on-after-scrub.sh", "on-failure.sh"):
            f = host.file(f"/opt/storagebaby/maintenance/plugins/{service}/{hook}")
            assert f.exists and f.mode == 0o755, f"{service}/{hook}"


def test_msmtprc_is_root_only_and_names_the_declared_relay(host):
    s = storage(host)
    f = host.file("/etc/msmtprc")
    assert f.exists and f.mode == 0o600 and f.user == "root", f.mode
    text = f.content_string
    assert f"host {s['mail']['smtp_host']}" in text, "the relay is not the declared one"
    assert f"port {s['mail']['smtp_port']}" in text
    assert f"from {s['mail']['from']}" in text
    assert f"user {s['mail']['smtp_user']}" in text
    # The decrypt really happened: the render-only placeholder must not be what is on a host.
    assert "REPLACE_ME" not in text, "msmtprc still carries the render placeholder"


def test_maintenance_timer_is_enabled(host):
    m = maintenance(host)
    assert host.service("storage-maintenance.timer").is_enabled
    assert host.service("storage-maintenance.timer").is_running
    unit = host.file("/etc/systemd/system/storage-maintenance.timer").content_string
    assert f"OnCalendar={m['on_calendar']}" in unit, unit
    assert "Persistent=true" in unit, unit
    # And the service is *not* started by the converge: starting it is the nightly run.
    #
    # `is-active` cannot say that: it reads `inactive` for a service that never ran and for
    # one that ran and finished, and a full run on this array takes about six seconds while
    # the verifier runs minutes after the converge. `ExecMainStartTimestamp` is empty until
    # the service is started the first time and keeps its value afterwards, so it is the
    # one thing that still answers the question after the case below has run a real run --
    # which matters, because the timer is created `Persistent=true` and started in the same
    # task run, and whether systemd back-fills a missed elapse on a first `start` is exactly
    # what this is here to settle. The price is that this case is not re-runnable inside one
    # VM either: the run the next case starts sets the timestamp for good, so a second
    # `molecule verify` over the same machine fails here.
    started = host.check_output("systemctl show storage-maintenance.service -p ExecMainStartTimestamp --value")
    assert started == "", f"the converge started a maintenance run at {started}"


def test_a_maintenance_run_is_clean(host):
    """One real run, end to end: balance, sync, scrub, status, SMART, and the mail.

    A few files go into the pool first, because an array with nothing in it proves nothing
    about a sync -- and they are what the content and parity files below are evidence of.
    The unit is a oneshot, so `start` blocks until the whole run is done.
    """
    s = storage(host)
    m = maintenance(host)
    probe = f"{s['pool']['mount']}/maintenance-probe"
    host.run(f"mkdir -p {probe}")
    for n in range(3):
        host.run(f"dd if=/dev/urandom of={probe}/{n}.bin bs=1M count=4 status=none")
    host.run("rm -f /var/spool/test-mail/*.eml")

    r = host.run("timeout 1800 systemctl start storage-maintenance.service")
    journal = host.run("journalctl -u storage-maintenance.service --no-pager | tail -200").stdout
    assert r.rc == 0, journal
    assert host.check_output("systemctl show storage-maintenance.service -p Result --value") == "success", journal
    log = host.file("/var/log/storage-maintenance/storage-maintenance.log").content_string
    for section in [
        "=== Balancing disks ===",
        "=== Syncing ===",
        "=== Scrubbing ===",
        "=== Final Snapraid Status ===",
        "=== SMART Report ===",
        "=== Job Finished with Status: SUCCESS ===",
    ]:
        assert section in log, log[-6000:]
    for disk in s["disks"]:
        assert host.file(f"{disk['mount']}/snapraid.content").exists, disk["name"]
    assert host.file("/etc/snapraid.content").exists
    assert host.file(f"{s['parity'][0]['mount']}/snapraid.parity").exists

    # Stopped before the balance and started again after the scrub: what must not survive the
    # run is a service left down, which is the whole reason the hooks exist. Both test hosts
    # declare one, so neither loop is vacuous -- jellyfin on test-a, yuzukam on test-ci, both
    # single containers. The pod branch of `top_unit` is exercised on no host today.
    assert m["stop_services"], "this host stops nothing, so the hook path below proves nothing"
    for service in m["stop_services"]:
        unit = top_unit(host, service)
        assert unit, f"{service}: no unit resolved, so neither assertion below would mean anything"
        state = host.run(f"systemctl --user -M svc-{service}@ is-active {unit}").stdout.strip()
        assert state == "active", f"{unit} is {state!r} after the maintenance run\n{log[-4000:]}"
        assert f"Stopping {unit}" in log, log[-6000:]
        assert f"Starting {unit}" in log, log[-6000:]
    host.run(f"rm -rf {probe}")


def test_the_maintenance_mail_reaches_the_sink(host):
    """Runs after the maintenance case, and is about the message rather than about msmtp.

    The sink writes one file per message, so a report msmtp could not send simply is not
    there -- which is the failure this catches: a wrong port, a refused auth or a mutt with
    no sendmail would all leave the maintenance run itself green.
    """
    s = storage(host)
    listing = sorted(host.run("ls -1 /var/spool/test-mail").stdout.split())
    assert listing, "the sink holds no message after the maintenance run"
    body = host.file(f"/var/spool/test-mail/{listing[-1]}").content_string
    assert f"To: {s['mail']['to']}" in body, body[:2000]
    assert "SnapRAID Sync Report" in body, body[:2000]
    assert "=== Job Finished with Status: SUCCESS ===" in body, body[:4000]


def test_a_run_that_cannot_start_still_mails(host):
    """The `OnFailure=` notifier, which is the only report a blocked run can produce.

    `storage-maintenance.service` `Requires=` the pool, and that has to stay -- a sync over
    an unmounted branch writes an empty disk into parity. The price is that a pool which
    does not come up fails the *start job*: ExecStart= never runs, so the wrapper never
    runs, so the nightly mail never arrives and the one failure that matters most is the
    silent one. `OnFailure=` is what breaks that silence.

    Provoking it for real would mean taking a branch away from a host that has every
    service's data on the union -- too destructive for a verifier that has to leave the VM
    usable afterwards, and the missing-device refusal in `test_deploy.py` already covers
    what an absent disk does to a converge. So the two halves are checked separately: that
    the maintenance unit names the notifier, and that the notifier really puts a mail in
    the sink naming the unit it is reporting about.
    """
    s = storage(host)
    unit = host.file("/etc/systemd/system/storage-maintenance.service").content_string
    assert "OnFailure=storage-maintenance-failed.service" in unit, unit

    before = set(host.run("ls -1 /var/spool/test-mail").stdout.split())
    r = host.run("systemctl start storage-maintenance-failed.service")
    journal = host.run("journalctl -u storage-maintenance-failed.service --no-pager | tail -50").stdout
    assert r.rc == 0, journal
    new = sorted(set(host.run("ls -1 /var/spool/test-mail").stdout.split()) - before)
    assert new, f"the notifier sent nothing\n{journal}"

    body = host.file(f"/var/spool/test-mail/{new[-1]}").content_string
    assert f"To: {s['mail']['to']}" in body, body[:2000]
    assert "Storage maintenance could not start" in body, body[:2000]
    # The report is about a named unit and carries its state, not just a subject line.
    assert "storage-maintenance.service could not start." in body, body[:4000]
    assert "systemctl status storage-maintenance.service" in body, body[:4000]
