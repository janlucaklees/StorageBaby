import re
from pathlib import Path

import pytest

from conftest import REPO, placements, route_ports

ROLE_TEMPLATES = REPO / "ansible/roles/service/templates"


def _service_dirs() -> list[Path]:
    # Placements repeat a shared service once per host; the templates are the same file.
    return sorted({p.dir for p in placements()})


def _containers() -> list[Path]:
    return sorted(f for d in _service_dirs() for f in (d / "quadlet").glob("*.container.j2"))


def _unit_templates() -> list[Path]:
    """Every Quadlet unit template: the service folders' own, and the role's generated ones."""
    return sorted([f for d in _service_dirs() for f in (d / "quadlet").glob("*.j2")] + list(ROLE_TEMPLATES.glob("*.container.j2")))


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


JINJA = re.compile(r"\{\{.*?\}\}", flags=re.S)
# The one expression that renders as several words: a list glued together with a space.
JOINS_ON_WHITESPACE = re.compile(r"""join\(\s*['"][ \t]['"]\s*\)""")


def rendered_shape(assignment: str) -> str:
    """The assignment with every Jinja expression reduced to what it renders as: a word.

    The spaces inside `{{ tz }}` say nothing about the rendered line -- one expression is
    one value, however it is spelled. `{{ x | join(' ') }}` is the exception, and it is
    the whole point of this check.
    """
    return JINJA.sub(lambda m: "word word" if JOINS_ON_WHITESPACE.search(m.group()) else "word", assignment)


@pytest.mark.parametrize("path", _unit_templates(), ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_environment_values_with_whitespace_are_quoted(path):
    # systemd splits an unquoted `Environment=` on whitespace and keeps the first word,
    # logging "Invalid environment assignment, ignoring" for the rest and nothing else.
    # bootstrap.sh's GIT_SSH_COMMAND was the first casualty of that; KOPIA_PATHS was the
    # second, where it silently reduced a multi-volume backup to its first volume.
    # Quoting wraps the whole assignment: Environment="NAME=one two".
    for ln in path.read_text().splitlines():
        if not ln.startswith("Environment="):
            continue
        assignment = ln.split("=", 1)[1]
        if re.search(r"\s", rendered_shape(assignment)):
            assert assignment.startswith('"') and assignment.endswith('"'), f"{path.name}: {ln}"


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


@pytest.mark.parametrize("path", _unit_templates(), ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_no_template_writes_a_host_gateway_addhost(path):
    # The role owns this line now: it renders `AddHost=<fqdn>:host-gateway` for *every*
    # route name placed on the host into a Quadlet drop-in, so every service can reach
    # every other one through Traefik without a template knowing which names exist.
    # A hand-written one would be redundant where the role already wrote it and, on a
    # pod member, fatal -- podman refuses `--add-host` on a container that joins a pod.
    # `AddHost=<name>:127.0.0.1` stays allowed: immich aliases a compose service name
    # to the pod's loopback, which is a different thing entirely.
    # The other spellings of the same shortcut are rejected with it: podman writes
    # `host.containers.internal` and `host.docker.internal` into every container's hosts
    # file, and 169.254.1.2 is the address they resolve to under pasta. A template
    # reaching the host through one of those would bypass the route name -- and with it
    # Traefik's routing, its TLS and whatever the route declares -- just as surely, and
    # would break the day podman changes that address. Comments may name them; lines
    # that do something may not.
    for ln in path.read_text().splitlines():
        assert not ln.startswith("AddHost=") or not ln.endswith(":host-gateway"), (
            f"{path.name}: {ln} -- the service role renders the host-gateway map, templates do not"
        )
        if ln.lstrip().startswith("#"):
            continue
        for shortcut in ("host.containers.internal", "host.docker.internal", "169.254.1.2"):
            assert shortcut not in ln, (
                f"{path.name}: {ln} -- reach another service by its route name, not by {shortcut}"
            )
