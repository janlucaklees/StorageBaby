import subprocess
from pathlib import Path

import pytest

from conftest import REPO, host_names, placements


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
    templates = list((p.dir / "quadlet").glob("*.j2"))
    if not templates:
        pytest.skip("no quadlet templates yet")
    assert sorted(f.name for f in unit_dir.iterdir()) == sorted(t.name[:-3] for t in templates)
    r = subprocess.run(
        ["/usr/lib/podman/quadlet", "-dryrun", "-user"],
        env={"QUADLET_UNIT_DIRS": str(unit_dir), "PATH": "/usr/bin"},
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("p", [p for p in placements() if "domain" in p.spec], ids=lambda p: f"{p.host}/{p.name}")
def test_route_rendered(rendered, p):
    route = rendered / p.host / "traefik-dynamic.d" / f"{p.name}.yml"
    assert route.exists()
    assert f"Host(`{p.spec['domain']}." in route.read_text()
