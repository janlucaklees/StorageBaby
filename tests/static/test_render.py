import subprocess
from pathlib import Path

import pytest

from conftest import REPO, host_names, placements, routes_of

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
