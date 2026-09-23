"""The production deploy path: `storagebaby-deploy.service` pulling the stable branch.

The unit is a oneshot that runs `ansible-pull` against the bare repo seeded in
prepare, so `systemctl start` blocks until the whole convergence is done -- a minute
or two on the first run, which clones the repo first. `timeout` keeps a wedged pull
from hanging the verifier.

All three run last (`order(-1)`, which keeps their relative order): the second and the
third push a change to the seeded remote and restart a service, which every other test
would otherwise have to account for.
"""

import re
from pathlib import Path

import pytest
from test_service import HOSTS, SPECS, container_name, load_spec, volume_path

DEPLOY = "timeout 900 systemctl start storagebaby-deploy.service"
JOURNAL = "journalctl -u storagebaby-deploy.service --no-pager | tail -80"
DASHBOARD = "curl -sk -o /dev/null -w '%{http_code}' -H 'Host: traefik.test.local' https://127.0.0.1/dashboard/"


def active_since(host, user, unit):
    cmd = f"systemctl --user -M {user}@ show {unit} -p ActiveEnterTimestampMonotonic --value"
    return host.run(cmd).stdout.strip()


def other_placed_service(host) -> str | None:
    """The first service placed on this VM that is not traefik, or None if there is none.

    Derived from the repo rather than hard-coded: the second half of the claim below is
    "and nothing else restarted", and that is only worth asserting against a service the
    host actually runs. `SPECS` is every spec in the repo; `owner` is 'shared' or the
    host folder it lives under, which is what decides placement.
    """
    hostname = host.check_output("uname -n")
    for owner, spec_path in SPECS:
        name = spec_path.parent.name
        if name != "traefik" and owner in ("shared", hostname):
            return name
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

    The seeded remote is throwaway (destroyed with the VM), so nothing is restored.
    """
    other = other_placed_service(host)
    before = active_since(host, "svc-traefik", "traefik.service")
    other_before = active_since(host, f"svc-{other}", f"{other}.service") if other else None
    r = host.run(
        "cd /srv/src && sed -i 's|^port: .*|port: 8081|' hosts/shared/services/traefik/service.yml "
        "&& git -c user.name=t -c user.email=t@t commit -qam 'change traefik port' && git push -q origin stable"
    )
    assert r.rc == 0, r.stderr
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout
    assert active_since(host, "svc-traefik", "traefik.service") != before
    if other:
        assert active_since(host, f"svc-{other}", f"{other}.service") == other_before, f"{other} restarted too"
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

# The working tree prepare seeded the bare remote from; pushing to it is what a real
# commit to `stable` looks like from a host's point of view.
SEEDED = "/srv/src"
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


def marker_candidates(hostvars, spec, target: str) -> list[str]:
    """Where a container path such as `/usr/src/paperless/data/.hook-ran` lands on the host.

    Which volume the hook's path sits in is known only to the unit that mounts it, so
    every volume of the service is a candidate and the marker is looked for in all of
    them. That carries the claim -- gone before the deploy, back after it -- without a
    second copy of the mount table living in this test.
    """
    name = target.rsplit("/", 1)[-1]
    return [f"{volume_path(hostvars, spec, v)}/{name}" for v in spec["volumes"] or {}]


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
    hostname = host.check_output("uname -n")
    hostvars = load_spec(HOSTS / hostname / "host.yml")
    template = app_unit_template(spec_path, hook["container"])
    assert template, f"{spec['name']}: no container unit declares ContainerName={hook['container']}"

    markers = marker_candidates(hostvars, spec, target)
    for marker in markers:
        host.run(f"rm -f {marker}")
    assert not any(host.file(m).exists for m in markers), f"{target} survived its own removal"

    # Appended to the unit template, not to the spec: the role restarts on any rendered
    # difference, and a trailing comment is the smallest one that cannot change what the
    # unit does. The seeded remote is thrown away with the VM, so nothing is restored.
    relative = str(Path(template).relative_to("/repo"))
    r = host.run(
        f"cd {SEEDED} && printf '# harness: force a unit change\\n' >> {relative} "
        "&& git -c user.name=t -c user.email=t@t commit -qam 'touch the app unit' && git push -q origin stable"
    )
    assert r.rc == 0, r.stderr
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout
    assert any(host.file(m).exists for m in markers), (
        f"the hook `{hook['command']}` left no marker in any of {markers}\n{host.run(JOURNAL).stdout}"
    )
