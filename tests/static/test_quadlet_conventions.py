import re
from pathlib import Path

import pytest

from conftest import placements, route_ports


def _service_dirs() -> list[Path]:
    # Placements repeat a shared service once per host; the templates are the same file.
    return sorted({p.dir for p in placements()})


def _containers() -> list[Path]:
    return sorted(f for d in _service_dirs() for f in (d / "quadlet").glob("*.container.j2"))


@pytest.mark.parametrize("path", _containers(), ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_container_declares_health_and_restart(path):
    # A container that cannot report its own health is one systemd will happily keep
    # "running" while it serves errors, so every unit declares all three.
    lines = path.read_text().splitlines()
    for required in ["HealthCmd=", "HealthOnFailure=kill", "Restart=always"]:
        assert any(ln.startswith(required) for ln in lines), f"{path.name}: no line starting with {required!r}"


@pytest.mark.parametrize("path", _containers(), ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_published_ports_are_loopback_only(path):
    # Traefik is the only thing on these hosts that listens on a routable address, and a
    # `PublishPort=<port>:<port>` binds 0.0.0.0 -- which would put the service on the LAN
    # past Traefik, past its TLS and past whatever auth the route carries. The unit is the
    # only place this can be got wrong, so it is checked here and not left to a firewall.
    for ln in path.read_text().splitlines():
        if ln.startswith("PublishPort="):
            assert ln.startswith("PublishPort=127.0.0.1:"), f"{path.name}: {ln}"


@pytest.mark.parametrize(
    "p",
    [p for p in placements() if list((p.dir / "quadlet").glob("*.pod.j2"))],
    ids=lambda p: f"{p.host}/{p.name}",
)
def test_pod_publishes_exactly_the_route_ports(p):
    # In a pod the ports are the pod's, not the containers': a container that publishes
    # its own would fail to start, and a pod that publishes more or fewer than the
    # routes declare is either exposed past Traefik or unreachable through it.
    pod = next((p.dir / "quadlet").glob("*.pod.j2")).read_text()
    published = re.findall(r"^PublishPort=127\.0\.0\.1:(\d+):", pod, flags=re.M)
    assert sorted(int(x) for x in published) == sorted(route_ports(p.spec))
    assert not re.search(r"^PublishPort=(?!127\.0\.0\.1:)", pod, flags=re.M)
    for tpl in (p.dir / "quadlet").glob("*.container.j2"):
        text = tpl.read_text()
        assert re.search(rf"^Pod={p.name}\.pod$", text, flags=re.M), f"{tpl.name} must join {p.name}.pod"
        assert not re.search(r"^PublishPort=", text, flags=re.M), f"{tpl.name}: publish ports on the pod, not the container"
