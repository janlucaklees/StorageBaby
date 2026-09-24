"""The production deploy path: `storagebaby-deploy.service` pulling the stable branch.

The unit is a oneshot that runs `ansible-pull` against the bare repo seeded in
prepare, so `systemctl start` blocks until the whole convergence is done -- a minute
or two on the first run, which clones the repo first. `timeout` keeps a wedged pull
from hanging the verifier.

All four run last (`order(-1)`, which keeps their relative order): every one but the
first pushes to the seeded remote and restarts a service, which every other test would
otherwise have to account for.
"""

import re
from pathlib import Path

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

DEPLOY = "timeout 900 systemctl start storagebaby-deploy.service"
# The working tree prepare seeded the bare remote from; pushing to it is what a real
# commit to `stable` looks like from a host's point of view.
SEEDED = "/srv/src"
JOURNAL = "journalctl -u storagebaby-deploy.service --no-pager | tail -80"
DASHBOARD = "curl -sk -o /dev/null -w '%{http_code}' -H 'Host: traefik.test.local' https://127.0.0.1/dashboard/"


def active_since(host, user, unit):
    cmd = f"systemctl --user -M {user}@ show {unit} -p ActiveEnterTimestampMonotonic --value"
    return host.run(cmd).stdout.strip()


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


@pytest.mark.order(-1)
def test_deploy_service_is_a_noop_when_nothing_changed(host):
    # The checkout renders byte for byte what the converge already put on the host, so
    # nothing is reported changed and no unit is restarted -- that is the whole claim.
    before = active_since(host, "svc-traefik", "traefik.service")
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout
    assert host.file("/var/lib/storagebaby/repo/ansible/playbook.yml").exists
    assert active_since(host, "svc-traefik", "traefik.service") == before


def single_container_service(host):
    """(name, unit file stem, unit, container) of the first placed one-container service.

    Derived like `other_placed_service`, and for the same reason. A one-container
    service is where the host-gateway drop-in sits on the container itself, so "exactly
    this unit came back" is a single timestamp; on a pod service the drop-in is the
    pod's and a restart moves every member. traefik is excluded because it is the unit
    the other half of the claim -- "and nothing else restarted" -- watches.
    """
    hostname = host.check_output("uname -n")
    for owner, spec_path in SPECS:
        name = spec_path.parent.name
        if name == "traefik" or owner not in ("shared", hostname) or pod_unit(spec_path):
            continue
        containers = quadlets(spec_path, "container")
        if len(containers) == 1:
            stem = unit_stem(containers[0])
            return name, stem, f"{stem}.service", container_name(containers[0])
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
    name, stem, unit, container = found
    user = f"svc-{name}"
    path = f"/etc/containers/systemd/users/{host.user(user).uid}/{stem}.container.d/10-storagebaby-hosts.conf"
    before = host.file(path).content_string
    mapped = [ln.split("=", 1)[1].rsplit(":", 1)[0] for ln in before.splitlines() if ln.startswith("AddHost=")]
    assert len(mapped) > 1, f"{path} maps {mapped}: too few names to drop one and still prove anything"
    dropped = mapped[-1]

    unit_before = active_since(host, user, unit)
    traefik_before = active_since(host, "svc-traefik", "traefik.service")
    assert unit_before != "0", f"{unit} has no ActiveEnterTimestamp: is that the right unit name?"

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
    assert active_since(host, user, unit) != unit_before, f"{unit} was not restarted by its changed drop-in"
    assert active_since(host, "svc-traefik", "traefik.service") == traefik_before, "traefik restarted too"
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
    before = active_since(host, "svc-traefik", "traefik.service")
    other_before = active_since(host, f"svc-{other[0]}", other[1]) if other else None
    # `systemctl show` answers for a unit it does not know with a 0 timestamp, and 0 ==
    # 0 would make the "and nothing else restarted" assertion below pass without ever
    # looking at a running service. So the reading itself has to be a real one.
    if other:
        assert other_before != "0", f"{other[1]} has no ActiveEnterTimestamp: is that the right unit name?"
    r = host.run(
        "cd /srv/src && sed -i 's|^port: .*|port: 8081|' hosts/shared/services/traefik/service.yml "
        "&& git -c user.name=t -c user.email=t@t commit -qam 'change traefik port' && git push -q origin stable"
    )
    assert r.rc == 0, r.stderr
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout
    assert active_since(host, "svc-traefik", "traefik.service") != before
    if other:
        assert active_since(host, f"svc-{other[0]}", other[1]) == other_before, f"{other[0]} restarted too"
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
