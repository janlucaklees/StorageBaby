import subprocess
from pathlib import Path

import pytest

from conftest import REPO, host_names, placed_fqdns, placements, routes_of

# A timer and its service unit are plain systemd user units, not Quadlet ones: the role
# renders them beside the Quadlet units, into `timers/` of the render output.
TIMER_SUFFIXES = (".timer.j2", ".service.j2")


def quadlet_templates(p) -> list[Path]:
    return [t for t in (p.dir / "quadlet").glob("*.j2") if not t.name.endswith(TIMER_SUFFIXES)]


def expected_units(p) -> list[str]:
    units = [t.name[:-3] for t in quadlet_templates(p)]
    if p.spec["backup"] != "none":
        units.append(f"{p.name}-backup.container")
    return sorted(units)


@pytest.fixture(scope="session")
def rendered(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("render")
    for host in host_names():
        subprocess.run(
            [
                "ansible-playbook",
                "-i",
                f"{host},",
                "-c",
                "local",
                "ansible/playbook.yml",
                "-e",
                "render_only=true",
                "-e",
                f"render_output={out / host}",
                "-e",
                "ansible_become=false",
            ],
            check=True,
            cwd=str(REPO),
        )
    return out


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
