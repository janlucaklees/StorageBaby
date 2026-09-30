import re
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import (
    RENDER_TCP_BIND_ADDRESS,
    host_names,
    placed_fqdns,
    placed_tcp_ports,
    placements,
    routes_of,
    tcp_ports,
)

# A timer and its service unit are plain systemd user units, not Quadlet ones: the role
# renders them beside the Quadlet units, into `timers/` of the render output.
TIMER_SUFFIXES = (".timer.j2", ".service.j2")


def quadlet_templates(p) -> list[Path]:
    return [t for t in (p.dir / "quadlet").glob("*.j2") if not t.name.endswith(TIMER_SUFFIXES)]


def own_add_hosts(p) -> list[str]:
    """The literal `AddHost=` values a service's own templates declare.

    Only immich has one today (`immich-machine-learning:127.0.0.1`, the compose service
    name its ML setting points at, aliased to the pod's loopback). A templated value is
    skipped: what is rendered is not knowable from the source line, and the claim here
    is about Quadlet's merge, not about the value.
    """
    found = []
    for template in sorted((p.dir / "quadlet").glob("*.j2")):
        for line in template.read_text().splitlines():
            if line.startswith("AddHost=") and "{{" not in line:
                found.append(line.split("=", 1)[1])
    return found


def expected_units(p) -> list[str]:
    units = [t.name[:-3] for t in quadlet_templates(p)]
    if p.spec["backup"] != "none":
        units.append(f"{p.name}-backup.container")
    return sorted(units)


@pytest.mark.parametrize("p", placements(), ids=lambda p: f"{p.host}/{p.name}")
def test_rendered_units_pass_quadlet_dryrun(rendered, p):
    unit_dir = rendered / p.host / p.name
    if not quadlet_templates(p):
        pytest.skip("no quadlet templates yet")
    assert sorted(f.name for f in unit_dir.iterdir() if f.is_file()) == expected_units(p)
    r = subprocess.run(
        ["/usr/lib/podman/quadlet", "-dryrun", "-user"],
        env={"QUADLET_UNIT_DIRS": str(unit_dir), "PATH": "/usr/bin"},
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    # The drop-ins are the other half of what Quadlet reads out of this directory, and
    # they are the half that is invisible in the unit listing above. Asserting they
    # reach the generated `ExecStart` is what says "Quadlet merges `<unit>.d/*.conf`",
    # rather than leaving it assumed: a Quadlet that ignored them would still exit 0.
    for fqdn in placed_fqdns(p.host):
        assert f"--add-host {fqdn}:host-gateway" in r.stdout, (
            f"{p.host}/{p.name}: quadlet did not merge the host-gateway drop-in\n{r.stdout}"
        )
    # And that merging *appends* rather than replaces, which is the whole question for
    # the one unit that writes an `AddHost=` of its own: immich's pod aliases the
    # compose service name `immich-machine-learning` to the pod's loopback, and the
    # role's drop-in adds the host's route names to the same directive. If a drop-in
    # replaced the unit file's value, the alias would be gone, Immich's ML setting
    # would name something nothing resolves, and machine learning would be quietly
    # dead -- with every assertion above still passing.
    for alias in own_add_hosts(p):
        assert f"--add-host {alias}" in r.stdout, (
            f"{p.host}/{p.name}: quadlet dropped the unit's own AddHost={alias}\n{r.stdout}"
        )


@pytest.mark.parametrize("p", placements(), ids=lambda p: f"{p.host}/{p.name}")
def test_host_gateway_dropin_rendered(rendered, p):
    """Every placed service carries the host's whole route map -- on the pod, or per container.

    The role puts it on the pod when there is one, because podman refuses `--add-host`
    on a container that joins a pod, and on each container otherwise. Which section
    heading the file carries is therefore part of the claim.
    """
    if not quadlet_templates(p):
        pytest.skip("no quadlet templates yet")
    pods = sorted(t.name[:-3] for t in quadlet_templates(p) if t.name.endswith(".pod.j2"))
    containers = sorted(t.name[:-3] for t in quadlet_templates(p) if t.name.endswith(".container.j2"))
    expected = [f"AddHost={fqdn}:host-gateway" for fqdn in placed_fqdns(p.host)]
    for unit in pods or containers:
        conf = rendered / p.host / p.name / f"{unit}.d" / "10-storagebaby-hosts.conf"
        assert conf.exists(), conf
        lines = conf.read_text().splitlines()
        assert lines[0] == ("[Pod]" if unit.endswith(".pod") else "[Container]"), lines[0]
        assert [ln for ln in lines if ln.startswith("AddHost=")] == expected


# `Image=` may name another Quadlet unit instead of a registry reference: `<stem>.build`
# for an image the host builds, `<stem>.image` for a `.image` unit. Those are not pulled
# and are not checked here.
UNIT_REF = re.compile(r"^[A-Za-z0-9._-]+\.(build|image)$")

# registry/namespace…/name, then a tag, a digest, or both. The registry half has to be
# recognisable as one -- a dotted name, optionally with a port, or literally `localhost`
# -- because that is exactly what podman uses to decide whether a reference is qualified
# at all: a name without it is completed from `unqualified-search-registries`, which is
# host configuration this repo does not set and does not want to depend on.
IMAGE_REF = re.compile(
    r"^(localhost|[a-z0-9-]+(\.[a-z0-9-]+)+)(:\d+)?"
    r"/[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*"
    r"(?P<tag>:[\w][\w.-]*)?(?P<digest>@sha256:[0-9a-f]{64})?$"
)


@pytest.mark.parametrize("p", placements(), ids=lambda p: f"{p.host}/{p.name}")
def test_rendered_images_are_pullable_references(rendered, p):
    """Every rendered `Image=` is a fully qualified reference with a tag or a digest.

    The role pulls these before it touches anything of the service
    (`ansible/roles/service/README.md`, "Images are pulled before anything of a service is
    written"), so an ambiguous one is not a style complaint: `podman pull nginx:alpine`
    resolves through the host's `unqualified-search-registries`, which means the image a
    converge fetches would depend on a file outside this repository -- and an untagged
    reference silently means `:latest`, which is a different image on the NAS than on the
    test VM.

    Checked on the *rendered* unit and not on the template, because two of them build the
    reference out of `service.config.version`.
    """
    unit_dir = rendered / p.host / p.name
    if not quadlet_templates(p):
        pytest.skip("no quadlet templates yet")
    units = sorted(unit_dir.glob("*.container"))
    assert units, f"{unit_dir} rendered no container unit"
    for unit in units:
        images = [ln.split("=", 1)[1].strip() for ln in unit.read_text().splitlines() if ln.startswith("Image=")]
        # Exactly one: Quadlet takes the last `Image=` it reads, so a second one is a
        # unit that runs something other than what it appears to, and it would also make
        # "the image the role pulled" and "the image the container starts" two things.
        assert len(images) == 1, f"{p.host}/{unit.name}: Image= lines {images}"
        image = images[0]
        if UNIT_REF.match(image):
            continue
        match = IMAGE_REF.match(image)
        assert match, f"{p.host}/{unit.name}: Image={image} is not a fully qualified reference"
        assert match["tag"] or match["digest"], f"{p.host}/{unit.name}: Image={image} names no tag and no digest"


@pytest.mark.parametrize("p", [p for p in placements() if routes_of(p.spec)], ids=lambda p: f"{p.host}/{p.name}")
def test_route_rendered(rendered, p):
    # One file and one router per route, named `<service>-<domain>`: two routes of the
    # same service would otherwise overwrite each other's file and its router.
    for r in routes_of(p.spec):
        route = rendered / p.host / "traefik-dynamic.d" / f"{p.name}-{r['domain']}.yml"
        assert route.exists(), route
        assert f"Host(`{r['domain']}." in route.read_text()


@pytest.mark.parametrize(
    "p",
    [p for p in placements() if list((p.dir / "quadlet").glob("*.timer.j2"))],
    ids=lambda p: f"{p.host}/{p.name}",
)
def test_timers_rendered(rendered, p):
    timer_dir = rendered / p.host / p.name / "timers"
    expected = sorted(t.name[:-3] for t in (p.dir / "quadlet").glob("*.j2") if t.name.endswith(TIMER_SUFFIXES))
    assert sorted(f.name for f in timer_dir.iterdir()) == expected


# The entrypoint the traefik template renders per placed TCP port: name, bind address and
# port. The name and the port are captured so the test can say they are the same number --
# an `--entrypoints.tcp-21.address=<addr>:2121` would route the wrong port and read fine.
# The address half is captured because it must be an address at all: the wildcard `:<port>`
# this used to render cannot coexist with a pod publishing `127.0.0.1:<port>`.
TCP_ENTRYPOINT = re.compile(r"--entrypoints\.tcp-(\d+)\.address=([^:\s\\]+):(\d+)")


@pytest.mark.parametrize("host", host_names())
def test_traefik_entrypoints_are_exactly_the_hosts_tcp_ports(rendered, host):
    """Traefik's unit carries one TCP entrypoint per placed port, and not one more.

    Asserted as a set per host rather than per service, because the two failures worth
    catching are both invisible to a per-service check: an entrypoint left over from a
    port nothing claims any more, and a port leaking onto a host that does not place the
    service. No host's list is empty today -- paperless's 21 and its passive range wherever
    paperless is placed, the `tcp-echo` fixture's 7777 on the two test hosts -- so it is the
    set comparison that carries the claim, on every one of them.

    Each entrypoint also has to carry a real bind address, which is the half a render can
    only see through the placeholder it was given: on a host the value is the primary
    address fact, and what a static check can hold is that it reaches the flag rather than
    being dropped for the wildcard `:<port>` -- which cannot bind beside the pod's
    `127.0.0.1:<port>`, and is therefore the one spelling that must never come back.
    """
    unit_dir = rendered / host / "traefik"
    unit = (unit_dir / "traefik.container").read_text()
    found = TCP_ENTRYPOINT.findall(unit)
    assert all(name == port for name, _, port in found), f"{host}: {found}"
    assert all(address == RENDER_TCP_BIND_ADDRESS for _, address, _ in found), f"{host}: {found}"
    assert sorted(int(name) for name, _, _ in found) == placed_tcp_ports(host), unit
    # And that the lines really land in the command Quadlet generates. `Exec=` is one
    # logical line held together by backslashes and the ports are rendered into the middle
    # of it, so a loop that emitted a blank line or lost a trailing `\` would leave a unit
    # file that still matches the regex above and a Traefik that never binds the port.
    r = subprocess.run(
        ["/usr/lib/podman/quadlet", "-dryrun", "-user"],
        env={"QUADLET_UNIT_DIRS": str(unit_dir), "PATH": "/usr/bin"},
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    for name, address, _ in found:
        assert f"--entrypoints.tcp-{name}.address={address}:{name}" in r.stdout, r.stdout


@pytest.mark.parametrize(
    "p", [p for p in placements() if p.spec.get("tcp_ports")], ids=lambda p: f"{p.host}/{p.name}"
)
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
        flag = f"--entrypoints.tcp-{entry['port']}.address={RENDER_TCP_BIND_ADDRESS}:{entry['port']}"
        assert flag in unit, unit


@pytest.mark.parametrize(
    "p", [p for p in placements() if not p.spec.get("tcp_ports")], ids=lambda p: f"{p.host}/{p.name}"
)
def test_no_tcp_file_for_a_service_that_claims_none(rendered, p):
    """The other half of the cleanup task: nothing is written for a service without ports.

    Traefik reads the whole directory, so a file rendered for a service that claims no
    TCP port would be a router on an entrypoint that does not exist -- and on the host it
    is the file the role has to remove, which is the same claim from the other side.

    The directory is asserted first, or the whole case would pass on a render that produced
    nothing at all: `not (...).exists()` cannot tell an absent file from an absent tree.
    """
    dynamic_d = rendered / p.host / "traefik-dynamic.d"
    assert dynamic_d.is_dir(), f"{dynamic_d} was never rendered"
    assert not (dynamic_d / f"{p.name}-tcp.yml").exists()
