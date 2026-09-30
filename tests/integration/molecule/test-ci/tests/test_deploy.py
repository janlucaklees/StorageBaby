"""The production deploy path: `storagebaby-deploy.service` pulling the stable branch.

The unit is a oneshot that runs `ansible-pull` against the bare repo seeded in
prepare, so `systemctl start` blocks until the whole convergence is done -- a minute
or two on the first run, which clones the repo first. `timeout` keeps a wedged pull
from hanging the verifier.

Every case here runs last (`order(-1)`, which keeps their relative order): all but the
first two push to the seeded remote and restart a service or remount the pool, which every
other test would otherwise have to account for.
"""

import re
import shlex
from pathlib import Path
from typing import NamedTuple

import pytest
from conftest import run_as
from test_service import (
    SPECS,
    container_name,
    hosts_file_map,
    load_spec,
    pod_unit,
    quadlets,
    unit_stem,
)
from test_storage import storage

DEPLOY = "timeout 900 systemctl start storagebaby-deploy.service"
# The working tree prepare seeded the bare remote from; pushing to it is what a real
# commit to `stable` looks like from a host's point of view.
SEEDED = "/srv/src"
# The checkout `ansible-pull` clones into and re-uses; the deploy unit names it.
PULLED = "/var/lib/storagebaby/repo"
JOURNAL = "journalctl -u storagebaby-deploy.service --no-pager | tail -80"
DASHBOARD = "curl -sk -o /dev/null -w '%{http_code}' -H 'Host: traefik.test.local' https://127.0.0.1/dashboard/"


class Marks(NamedTuple):
    """What a unit looks like before and after a deploy, for the restart claims below."""

    active_since: str
    restarts: str


def unit_marks(host, user, unit) -> Marks:
    """`ActiveEnterTimestampMonotonic` and `NRestarts` of a user unit, read in one call.

    Both, because "the timestamp did not move" and "the role did not restart it" are not
    the same claim -- see `assert_the_role_did_not_restart`. Parsed as `KEY=value` rather
    than asked for with `--value`, so the two values cannot be told apart by position.
    `systemctl show` answers for a unit it does not know with a 0 timestamp, which is why
    every caller that needs a *real* reading asserts against `"0"` itself.
    """
    out = host.run(f"systemctl --user -M {user}@ show {unit} -p ActiveEnterTimestampMonotonic -p NRestarts").stdout
    props = {k: (v.strip() or "0") for k, v in (ln.split("=", 1) for ln in out.splitlines() if "=" in ln)}
    return Marks(props.get("ActiveEnterTimestampMonotonic", "0"), props.get("NRestarts", "0"))


def assert_the_role_did_not_restart(host, user, unit, before: Marks, what: str):
    """Fail only when *the role* restarted `unit` since `before`.

    A moved `ActiveEnterTimestampMonotonic` used to be the whole evidence, and it is not
    enough. `systemctl restart` is all the role ever issues, and it moves that timestamp
    -- but so does a restart nothing in this repository asked for: every container here
    declares `HealthOnFailure=kill` and `Restart=always`, so one health check that times
    out under the load of back-to-back image pulls on a 2-vCPU test VM has systemd kill
    and restart the container by itself, outside any converge. That is how jellyfin made
    this assertion fail on test-a on a run whose role behaviour was correct.

    The two are told apart by the counter: a systemd auto-restart *increments*
    `NRestarts`, a manual `systemctl restart` resets it to 0. Hence `>` and not `!=` -- a
    unit that health-cycled earlier in the run sits at 1, and a role restart afterwards
    would take it back to 0, which `!=` would excuse as "systemd did it" and let a real
    regression through.

    The blind spot, stated plainly: a deploy in which the role restarts the unit *and* a
    health cycle bumps the counter is excused. That is the trade. Each of these is the
    negative half of a claim whose positive half is asserted on its own, and a case that
    fails on the VM's timing says nothing about the role.
    """
    now = unit_marks(host, user, unit)
    if now.active_since == before.active_since:
        return
    assert int(now.restarts) > int(before.restarts), (
        f"{what}: {unit} was restarted by the role (ActiveEnterTimestampMonotonic "
        f"{before.active_since} -> {now.active_since}, NRestarts {before.restarts} -> {now.restarts})"
    )


def other_placed_service(host) -> tuple[str, str] | None:
    """(name, top unit) of the first service placed here that is not traefik, or None.

    Derived from the repo rather than hard-coded: the second half of the claim below is
    "and nothing else restarted", and that is only worth asserting against a service the
    host actually runs. `SPECS` is every spec in the repo; `owner` is 'shared' or the
    host folder it lives under, which is what decides placement.

    The unit is the *pod's* when the service is a pod, because a pod service has no
    `<name>.service` at all -- and `systemctl show` answers for a unit that does not
    exist with `ActiveEnterTimestampMonotonic=0`. Asking for the wrong name would
    therefore compare 0 with 0 and let the claim pass without checking anything, which
    is exactly what happened the moment a pod sorted first on this host.
    """
    hostname = host.check_output("uname -n")
    for owner, spec_path in SPECS:
        name = spec_path.parent.name
        if name != "traefik" and owner in ("shared", hostname):
            return name, pod_unit(spec_path) or f"{name}.service"
    return None


# The pre-flight an operator runs against the live NAS before pushing: the same playbook,
# the same checkout, the same inventory, with `--check --diff`. It has to reach the end of
# the run, because what it exists to show -- the storage role's diff and its remount
# warning -- is printed by roles that come first, and a traceback further down would leave
# the operator reading half a report and guessing about the rest.
CHECK = (
    f"cd {PULLED} && timeout 900 ansible-playbook -i ansible/inventory/hosts.yml "
    "--limit $(uname -n) --check --diff ansible/playbook.yml"
)


def check_recap(result) -> list[str]:
    """The run's recap lines, once each has been asserted to carry no failure."""
    recap = [ln for ln in result.stdout.splitlines() if "failed=" in ln]
    for line in recap:
        assert "failed=0" in line, line
        assert "unreachable=0" in line, line
    return recap


@pytest.mark.order(-1)
def test_deploy_service_is_a_noop_when_nothing_changed(host):
    # The checkout renders byte for byte what the converge already put on the host, so
    # nothing is reported changed and no unit is restarted -- that is the whole claim.
    before = unit_marks(host, "svc-traefik", "traefik.service")
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout
    assert host.file("/var/lib/storagebaby/repo/ansible/playbook.yml").exists
    assert_the_role_did_not_restart(host, "svc-traefik", "traefik.service", before, "a deploy that changed nothing")


# Declared after the case above and not before it, although it changes nothing itself:
# `PULLED` is the checkout `ansible-pull` clones on its *first* run, so until one deploy
# has happened there is no directory to run this in.
@pytest.mark.order(-1)
def test_the_whole_playbook_runs_clean_in_check_mode(host):
    """`--check --diff` over the whole playbook ends `failed=0`, on the converged host.

    The failure this catches is not a wrong diff but an aborted one: a registered
    `command` whose `stdout` a later `set_fact` reads is *skipped* under check mode, and
    the skipped result carries no `stdout` at all -- so the run dies with a `from_json` or
    an attribute error somewhere in the middle and says nothing about the change the
    operator was asking about. Every such probe carries `check_mode: false`; this is what
    says so for the whole playbook rather than for one role.

    Run against the checkout `ansible-pull` maintains, with the inventory the deploy unit
    names, so a pass here is a statement about the command an operator actually types.
    """
    assert host.file(f"{PULLED}/ansible/playbook.yml").exists, f"{PULLED} has not been pulled yet"
    r = host.run(CHECK)
    assert r.rc == 0, r.stdout[-6000:] + r.stderr[-4000:]
    assert check_recap(r), r.stdout[-6000:]

    # And on a host that has drifted, which is the case the pre-flight is actually run
    # for -- nobody asks "what would this change" about a host they know matches. It is
    # its own half because the two go down different code paths: in check mode
    # `template` returns a result *without* `dest` for a file it *would* change, so a
    # task that reads `item.dest` passes over an unchanged host and dies on a changed
    # one. The drift is made on the VM and taken back immediately: a converge would
    # repair it too, but the point here is that `--check` changed nothing.
    found = single_container_service(host)
    if not found:
        pytest.skip("no single-container service is placed on this host")
    name, stem, _, _, template = found
    path = f"/etc/containers/systemd/users/{host.user(f'svc-{name}').uid}/{stem}.container"
    backup = f"{path}.harness-backup"
    assert host.run(f"cp -a {path} {backup}").rc == 0
    try:
        assert host.run(f"printf '# harness: drift the rendered unit\\n' >> {path}").rc == 0
        r = host.run(CHECK)
        assert r.rc == 0, r.stdout[-6000:] + r.stderr[-4000:]
        recap = check_recap(r)
        assert recap, r.stdout[-6000:]
        assert all("changed=0" not in line for line in recap), f"--check reported no change on a drifted host: {recap}"
        assert path in r.stdout, f"the diff does not name {path}\n{r.stdout[-6000:]}"
    finally:
        assert host.run(f"mv -f {backup} {path}").rc == 0
    assert "# harness: drift" not in host.file(path).content_string, f"{path} was not put back"

    # And the third case, which is what an operator actually runs a pre-flight *for*: a
    # version bump, where the checkout names an image the host does not have. The probe
    # that decides whether to pull carries `check_mode: false` and so runs for real --
    # that is what lets the report say which images a push would fetch -- but the pull
    # behind it must not, and the run still has to reach the end. The bump is made in the
    # pulled checkout rather than pushed to the remote, because nothing here is supposed
    # to converge: `git checkout` takes it back without a deploy.
    relative = str(Path(template).relative_to("/repo"))
    image = next(
        ln.split("=", 1)[1].strip() for ln in host.file(path).content_string.splitlines() if ln.startswith("Image=")
    )
    missing = f"{image.split('@')[0].rsplit(':', 1)[0]}:does-not-exist"
    assert run_as(host, f"svc-{name}", f"podman image exists {missing}").rc != 0, f"{missing} is in the store already"
    assert host.run(f"cd {PULLED} && sed -i 's|^Image=.*|Image={missing}|' {relative}").rc == 0
    try:
        r = host.run(CHECK)
        assert r.rc == 0, r.stdout[-6000:] + r.stderr[-4000:]
        assert check_recap(r), r.stdout[-6000:]
        # The claim that makes this more than a second copy of the case above: the
        # pre-flight left the image store alone. A `--check` that pulled would be a
        # `--check` that changed the host.
        assert run_as(host, f"svc-{name}", f"podman image exists {missing}").rc != 0, (
            f"--check fetched {missing}: the pull is not skipped in check mode"
        )
    finally:
        assert host.run(f"cd {PULLED} && git checkout -- .").rc == 0
    assert missing not in host.file(f"{PULLED}/{relative}").content_string, f"{relative} was not put back"


# `Image=` may name another Quadlet unit instead of a registry reference -- `<stem>.build`
# for an image this host builds, `<stem>.image` for a `.image` unit. The role's pre-pull
# skips both, so a service backed by one is no use to the two cases below: each of them
# rewrites the tag of the single `Image=` and expects the role to try to fetch it.
UNIT_BACKED = re.compile(r"^Image=\S+\.(build|image)$", flags=re.M)


def single_container_service(host):
    """(name, unit stem, unit, container, template) of the first placed one-container service.

    Derived like `other_placed_service`, and for the same reason. A one-container
    service is where the host-gateway drop-in sits on the container itself, so "exactly
    this unit came back" is a single timestamp; on a pod service the drop-in is the
    pod's and a restart moves every member. traefik is excluded because it is the unit
    the other half of the claim -- "and nothing else restarted" -- watches.

    The template is returned rather than looked up again by service name: two of the
    cases below edit it, and a lookup by name alone would take the *first* spec folder
    with that name, which for a service placed on a test host is the storagebaby one and
    is only the same file because the placements are symlinks. A real per-host copy would
    have them editing a template the host does not read and then asserting about a
    converge that changed nothing. Selected here, it is the placed service's own by
    construction.

    A `.build`- or `.image`-backed service is skipped for the reason above `UNIT_BACKED`.
    Today the filter changes nothing -- test-a lands on jellyfin and test-ci on kopia --
    but with neither placed it would fall through to `paperless-upload`.
    """
    hostname = host.check_output("uname -n")
    for owner, spec_path in SPECS:
        name = spec_path.parent.name
        if name == "traefik" or owner not in ("shared", hostname) or pod_unit(spec_path):
            continue
        containers = quadlets(spec_path, "container")
        if len(containers) != 1 or UNIT_BACKED.search(containers[0].read_text()):
            continue
        stem = unit_stem(containers[0])
        return name, stem, f"{stem}.service", container_name(containers[0]), containers[0]
    return None


@pytest.mark.order(-1)
def test_deploy_restores_a_drifted_host_gateway_dropin(host):
    """Drift in a rendered drop-in is repaired by a deploy, and only its own unit moves.

    The drop-in is the one rendered file whose content comes from the host's placement
    rather than from the service's folder, so no git change can produce a *narrow* one:
    editing a service's `domain` rewrites the drop-in of every service on the host. The
    drift is therefore made on the VM -- an `AddHost=` line removed from the file on
    disk -- which is also the real failure mode, a host that has been edited by hand or
    converged from an older commit.

    The commit pushed here is empty on purpose: `ansible-pull --only-if-changed` runs
    the playbook only when the checkout moved, so the deploy needs a new sha, and an
    empty one leaves the tree identical. Everything the converge then renders matches
    what is already on the host except the file this test broke, which makes the pair
    of assertions below -- this unit restarted, traefik did not -- say exactly that a
    changed drop-in is what restarts a unit.
    """
    found = single_container_service(host)
    if not found:
        pytest.skip("no single-container service is placed on this host")
    name, stem, unit, container, _ = found
    user = f"svc-{name}"
    path = f"/etc/containers/systemd/users/{host.user(user).uid}/{stem}.container.d/10-storagebaby-hosts.conf"
    before = host.file(path).content_string
    mapped = [ln.split("=", 1)[1].rsplit(":", 1)[0] for ln in before.splitlines() if ln.startswith("AddHost=")]
    assert len(mapped) > 1, f"{path} maps {mapped}: too few names to drop one and still prove anything"
    dropped = mapped[-1]

    unit_before = unit_marks(host, user, unit)
    traefik_before = unit_marks(host, "svc-traefik", "traefik.service")
    assert unit_before.active_since != "0", f"{unit} has no ActiveEnterTimestamp: is that the right unit name?"

    r = host.run(f"sed -i '/^AddHost={dropped}:/d' {path}")
    assert r.rc == 0, r.stderr
    assert host.file(path).content_string != before, f"{dropped} was not removed from {path}"
    r = host.run(
        f"cd {SEEDED} && git -c user.name=t -c user.email=t@t commit -q --allow-empty "
        "-m 'empty: a deploy cycle for the drop-in check' && git push -q origin stable"
    )
    assert r.rc == 0, r.stderr
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout

    assert host.file(path).content_string == before, f"the deploy did not restore {path}"
    assert unit_marks(host, user, unit).active_since != unit_before.active_since, (
        f"{unit} was not restarted by its changed drop-in"
    )
    assert_the_role_did_not_restart(host, "svc-traefik", "traefik.service", traefik_before, "traefik restarted too")
    # The file on disk is only half of it: Quadlet merges the drop-in into the unit at
    # generation time, so the mapping reaches a running container through the
    # daemon-reload and the restart, and this is where that is visible.
    mapping = hosts_file_map(host, user, container)
    gateway = mapping.get("host.containers.internal")
    assert gateway, f"{container}: podman wrote no host.containers.internal entry"
    assert mapping.get(dropped) == gateway, (
        f"{container} does not resolve {dropped} to the gateway again: {mapping.get(dropped)!r}"
    )


@pytest.mark.order(-1)
def test_an_unpullable_image_fails_the_deploy_without_stopping_anything(host):
    """A tag that does not exist stops the converge at the pull, with the service still up.

    This is the ordering claim of "Images are pulled before anything of a service is
    written" (`ansible/roles/service/README.md`) made from outside: the role pulls every
    image a unit names before it copies the service's config, syncs a `podman secret`,
    writes a unit file, reloads the user manager or restarts anything -- so an image that
    cannot be fetched costs a red deploy and nothing else. Get the order wrong and the
    first symptom is the opposite: the unit is rewritten, the container is stopped, and
    then the pull fails, leaving a service that was healthy a minute ago down until
    someone notices. Get it half right -- the units pulled first but the config and the
    secrets written before that -- and the symptom is worse than a down service, because
    the *next* deploy finds them already on disk, restarts nothing, and reports green over
    a pod still running the old ones.

    A single-container service is used for the same reason the drop-in case uses one: its
    unit is the only thing that would move, so "it did not move" is one timestamp rather
    than a pod's worth of them.

    The seeded remote *is* restored here, with a revert and a second deploy, because
    every case declared after this one needs a tree that converges.
    """
    found = single_container_service(host)
    if not found:
        pytest.skip("no single-container service is placed on this host")
    name, stem, unit, container, template = found
    user = f"svc-{name}"

    # The reference the host is running now, read off the rendered unit: the template may
    # build it out of `service.config.version`, and the tag has to be replaced with one
    # that is certainly absent from both the registry and the local store.
    uid = host.user(user).uid
    rendered = host.file(f"/etc/containers/systemd/users/{uid}/{stem}.container").content_string
    image = next(ln.split("=", 1)[1].strip() for ln in rendered.splitlines() if ln.startswith("Image="))
    missing = f"{image.split('@')[0].rsplit(':', 1)[0]}:does-not-exist"
    assert run_as(host, user, f"podman image exists {missing}").rc != 0, f"{missing} is in the store already"

    unit_before = unit_marks(host, user, unit)
    assert unit_before.active_since != "0", f"{unit} has no ActiveEnterTimestamp: is that the right unit name?"

    relative = str(Path(template).relative_to("/repo"))
    r = host.run(
        f"cd {SEEDED} && sed -i 's|^Image=.*|Image={missing}|' {relative} "
        "&& git -c user.name=t -c user.email=t@t commit -qam 'an image tag that does not exist' "
        "&& git push -q origin stable"
    )
    assert r.rc == 0, r.stderr

    r = host.run(DEPLOY)
    journal = host.run("journalctl -u storagebaby-deploy.service --no-pager | tail -200").stdout
    assert r.rc != 0, f"the converge survived {missing}\n{journal}"
    assert missing in journal, f"the failure does not name the image it could not pull\n{journal}"
    # Both halves of "nothing was touched": the unit was never restarted, and the
    # container it owns is still the running one. A restart that failed would also leave
    # the timestamp alone -- by leaving the unit down.
    assert_the_role_did_not_restart(host, user, unit, unit_before, "the pull failed after a restart")
    assert host.run(f"systemctl --user -M {user}@ is-active {unit}").stdout.strip() == "active"
    running = run_as(host, user, f"podman ps --quiet --filter name={container}")
    assert running.stdout.strip(), f"{container} is not in `podman ps` any more: {running.stderr}"

    # `git revert` has no `-q`, unlike the `commit`s and `push`es elsewhere in this file:
    # it exits 129 on one, which is a usage error and not a failed revert.
    r = host.run(
        f"cd {SEEDED} && git -c user.name=t -c user.email=t@t revert --no-edit HEAD && git push -q origin stable"
    )
    assert r.rc == 0, r.stderr
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout
    assert_the_role_did_not_restart(host, user, unit, unit_before, "the revert restarted it")


@pytest.mark.order(-1)
def test_deploy_restarts_only_the_changed_service(host):
    """A change to traefik's `port` restarts traefik and leaves every other service alone.

    `port` of the shared traefik service reaches exactly one rendered file: its own unit,
    where it is the `traefik` entrypoint's address and the port the HealthCmd probes. Not
    even its route file -- that one declares `internal: api@internal`, so the template
    renders no `loadBalancer` block and no port. A restart of `traefik.service` is
    therefore the narrowest observable effect a git change can have here.

    `tz` used to be the change, and is not usable for this any more: every service's unit
    renders `Environment=TZ`, so changing it restarts all of them and the "only" in the
    test's name stops being checkable.

    The seeded remote is not restored here: `prepare` re-seeds it from the working
    tree on every run, and it is destroyed with the VM in any case.
    """
    other = other_placed_service(host)
    before = unit_marks(host, "svc-traefik", "traefik.service")
    # `other` is the *pod* unit for a pod service, and no `.pod.j2` in this repo renders a
    # `Restart=` -- so on a pod the NRestarts half of `assert_the_role_did_not_restart` is
    # inert and the claim stays as strict as a bare timestamp comparison. That is the right
    # way round: what health-cycles is a container, and a container cycling does not move
    # its pod unit. The counter earns its keep on the single-container services.
    other_before = unit_marks(host, f"svc-{other[0]}", other[1]) if other else None
    # `systemctl show` answers for a unit it does not know with a 0 timestamp, and 0 ==
    # 0 would make the "and nothing else restarted" assertion below pass without ever
    # looking at a running service. So the reading itself has to be a real one.
    if other:
        assert other_before.active_since != "0", f"{other[1]} has no ActiveEnterTimestamp: is that the right unit name?"
    r = host.run(
        "cd /srv/src && sed -i 's|^port: .*|port: 8081|' hosts/shared/services/traefik/service.yml "
        "&& git -c user.name=t -c user.email=t@t commit -qam 'change traefik port' && git push -q origin stable"
    )
    assert r.rc == 0, r.stderr
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout
    assert unit_marks(host, "svc-traefik", "traefik.service").active_since != before.active_since
    if other:
        assert_the_role_did_not_restart(host, f"svc-{other[0]}", other[1], other_before, f"{other[0]} restarted too")
    assert host.run("systemctl --user -M svc-traefik@ is-active traefik.service").stdout.strip() == "active"
    # Polled, not a single shot: the restart is synchronous but Traefik's own startup
    # is not, so the first request after it can still be refused.
    served = host.run(f'for _ in $(seq 30); do c=$({DASHBOARD}); [ "$c" = 200 ] && break; sleep 1; done; echo "$c"')
    assert served.stdout.strip() == "200", host.run(JOURNAL).stdout


# --- after_change hooks ------------------------------------------------------------
#
# A hook's command is arbitrary and runs inside a container, so from outside there is
# nothing generic to observe about it -- except a file it leaves behind. `touch <path>`
# is the one hook shape this test can check, and it is the shape the pods declare for
# exactly that reason: a harmless marker whose presence says "the hooks ran on this
# converge". A service whose hooks do something else skips rather than guessing.

TOUCH = re.compile(r"^\s*touch\s+(\S+)\s*$")


def touching_hook(host):
    """(spec_path, spec, hook, marker path in the container) for the first such service."""
    hostname = host.check_output("uname -n")
    for owner, spec_path in SPECS:
        if owner not in ("shared", hostname):
            continue
        spec = load_spec(spec_path)
        for hook in spec.get("hooks", {}).get("after_change", []):
            match = TOUCH.match(hook["command"])
            if match:
                return spec_path, spec, hook, match.group(1)
    return None


def marker_exists(host, user: str, container: str, target: str) -> bool:
    """Is the hook's marker there, inside the container it was touched in?

    Read with `podman exec`, not off the host, because the marker is deliberately
    container-local: a path under a volume would be a stray file in a tree the Kopia
    sidecar snapshots. `/tmp` in the container is gone when the container is recreated,
    which makes this a stronger claim than a persisted file could be -- a marker present
    after a deploy that restarted the container can only have been written after it.
    """
    return run_as(host, user, f"podman exec {container} test -f {target}").rc == 0


def app_unit_template(spec_path, container: str):
    """The `*.container.j2` of the service that declares `ContainerName=<container>`."""
    for template in sorted((spec_path.parent / "quadlet").glob("*.container.j2")):
        if container_name(template) == container:
            return template
    return None


@pytest.mark.order(-1)
def test_deploy_runs_after_change_hooks(host):
    """Delete the marker, change the unit, deploy -- the hook must have put it back.

    Deleting it is what makes this a test of *this* converge: the marker is also there
    after the first one, so asserting its presence alone would pass without a hook ever
    running again. And the change is a real one to the app container's unit, because
    that is the trigger the hooks are declared against -- `when: unit_changed` means a
    converge that restarts nothing must not run them.
    """
    found = touching_hook(host)
    if not found:
        pytest.skip("no placed service declares a `touch` after_change hook")
    spec_path, spec, hook, target = found
    template = app_unit_template(spec_path, hook["container"])
    assert template, f"{spec['name']}: no container unit declares ContainerName={hook['container']}"

    user = f"svc-{spec['name']}"
    container = hook["container"]
    r = run_as(host, user, f"podman exec {container} rm -f {target}")
    assert r.rc == 0, r.stderr
    assert not marker_exists(host, user, container, target), f"{target} survived its own removal"

    # Appended to the unit template, not to the spec: the role restarts on any rendered
    # difference, and a trailing comment is the smallest one that cannot change what the
    # unit does. Nothing is restored here: `prepare` re-seeds the remote from the
    # working tree on every run.
    relative = str(Path(template).relative_to("/repo"))
    r = host.run(
        f"cd {SEEDED} && printf '# harness: force a unit change\\n' >> {relative} "
        "&& git -c user.name=t -c user.email=t@t commit -qam 'touch the app unit' && git push -q origin stable"
    )
    assert r.rc == 0, r.stderr
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout
    assert marker_exists(host, user, container, target), (
        f"the hook `{hook['command']}` left no {target} in {container}\n{host.run(JOURNAL).stdout}"
    )


# --- the storage role, from the deploy path ----------------------------------------
#
# Both of these push to the seeded remote and disturb the host's filesystems, so they are
# declared last: `order(-1)` keeps the relative order of the cases that share it, and the
# pool remount in the second one drops every container's bind mount under the union.


def pool_unit(s: dict) -> str:
    return s["pool"]["mount"].lstrip("/").replace("/", "-") + ".mount"


def empty_commit(host, message: str):
    """A new sha on `stable` with an identical tree.

    `ansible-pull --only-if-changed` runs the playbook only when the checkout moved, so a
    deploy that is meant to converge needs one -- and an empty commit leaves everything the
    converge renders identical to what is already on the host, which is what makes the
    assertions about *one* difference meaningful.
    """
    r = host.run(
        f"cd {SEEDED} && git -c user.name=t -c user.email=t@t commit -q --allow-empty "
        f"-m {shlex.quote(message)} && git push -q origin stable"
    )
    assert r.rc == 0, r.stderr
    return r


@pytest.mark.order(-1)
def test_deploy_refuses_to_converge_with_a_declared_device_missing(host):
    """A device that is not there stops the converge before any service is touched.

    This is the property `mountpoints` used to carry and the reason the storage role runs
    first: with a branch gone, `/pool/apps` would be an empty directory on the root
    filesystem, and a converge that carried on would recreate every service volume under it
    -- empty, at 02:00, from the deploy timer. So the run has to fail, name the disk, and
    leave the services alone.

    The disk is taken away by removing the udev symlink the tracked `host.yml` declares,
    which is what a pulled, renamed or late-enumerating disk looks like to the role -- and it
    leaves the filesystem mounted, so nothing under the pool is disturbed and "no service was
    restarted" is a claim about the role's refusal and not about a remount. udev puts the
    symlink back on a re-trigger.
    """
    s = storage(host)
    disk = s["disks"][-1]
    device = disk["device"]
    assert host.file(device).exists, f"{device} is not there to begin with"
    traefik_before = unit_marks(host, "svc-traefik", "traefik.service")

    assert host.run(f"rm -f {device}").rc == 0
    assert not host.file(device).exists, f"{device} survived its own removal"
    empty_commit(host, "a deploy cycle with a disk taken away")
    r = host.run(DEPLOY)
    journal = host.run("journalctl -u storagebaby-deploy.service --no-pager | tail -200").stdout
    assert r.rc != 0, f"the converge did not fail with {disk['name']} gone\n{journal}"
    # The device path, not the disk's name: the declared names are `d1`, `d2`, `d3`, and
    # `"d3" in journal` over two hundred lines of Ansible output is true whatever the role
    # said. The path cannot collide, and it is what the role's failure message prints.
    assert device in journal, journal
    assert_the_role_did_not_restart(
        host, "svc-traefik", "traefik.service", traefik_before, "a service was restarted anyway"
    )

    assert host.run("udevadm trigger --subsystem-match=block --action=change && udevadm settle").rc == 0
    assert host.file(device).exists, f"udev did not put {device} back"
    empty_commit(host, "a deploy cycle with the disk back")
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout


@pytest.mark.order(-1)
def test_a_pool_option_change_remounts_the_pool_exactly_once(host):
    """A changed `pool.options` in git remounts the union, and a second converge does not.

    The remount is the one outage this role can cause, so both halves matter: it has to
    happen when the declaration changes, and it must *not* happen on the nightly converge
    that renders the same unit again -- a remount per deploy would drop every container's
    bind mount under the pool every night.

    Runs last of everything for exactly that reason: the services on this host come back
    reading a stale union until they are restarted, and nothing after this would be reliable.

    Not re-runnable inside one VM: the `sed` below matches `threads=2`, which the first run
    has already turned into `threads=3`, so a second run commits nothing, `git commit` exits
    non-zero and the case fails on its own `rc == 0`. That is fine for `molecule test`, which
    re-extracts `/srv/src` from the archive in `prepare.yml` every time -- but a second
    `verify` over a surviving VM fails here rather than in the role, and a `converge` in
    between does not help: molecule reports `Skipping, instances already prepared` and never
    runs the re-seed at all.
    (`test_deploy_restarts_only_the_changed_service` has the same shape, for the same reason.)
    """
    s = storage(host)
    unit = pool_unit(s)
    stamp = f"systemctl show {unit} -p ActiveEnterTimestampMonotonic --value"
    before = host.run(stamp).stdout.strip()
    assert before not in ("", "0"), f"{unit} has no ActiveEnterTimestamp: is the pool up?"

    r = host.run(
        f"cd {SEEDED} && sed -i 's/threads=2/threads=3/' hosts/$(uname -n)/host.yml "
        "&& git -c user.name=t -c user.email=t@t commit -qam 'change a pool option' && git push -q origin stable"
    )
    assert r.rc == 0, r.stderr
    assert host.run(DEPLOY).rc == 0, host.run(JOURNAL).stdout
    after = host.run(stamp).stdout.strip()
    assert after != before, "the pool was not remounted by its changed option"
    assert host.run(f"mountpoint -q -- {s['pool']['mount']}").rc == 0, "the pool is not mounted any more"
    assert "threads=3" in host.file(f"/etc/systemd/system/{unit}").content_string

    empty_commit(host, "a second deploy cycle over the same pool option")
    assert host.run(DEPLOY).rc == 0, host.run(JOURNAL).stdout
    assert host.run(stamp).stdout.strip() == after, "the pool was remounted again by an unchanged unit"
