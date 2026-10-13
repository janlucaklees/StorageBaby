import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
HOSTS = REPO / "hosts"

# The address Traefik's TCP entrypoints bind, for a render on the controller. On a host the
# playbook's default is `ansible_default_ipv4.address`, a fact no render can have -- and the
# devtools container's own address would be a number that means nothing. So the render is
# given a documentation-range placeholder (RFC 5737) and the tests assert the flag carries
# `<address>:<port>`, which is the one thing about it that is knowable from the repo.
# Passed as an extra var, so it also overrides a `host.yml` that sets `tcp_bind_address`:
# a static render cannot show what that host binds, only that the address reaches the flag.
RENDER_TCP_BIND_ADDRESS = "203.0.113.1"


def load_yaml(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


def host_names() -> list[str]:
    return sorted(p.name for p in HOSTS.iterdir() if p.is_dir() and p.name != "shared")


# The external repositories the static suite can see: one folder per pointer name holding
# that repository's working tree, exactly as the host fetches it. On a host the same tree
# is /var/lib/storagebaby/sources/<name>; for Molecule it is this folder under /repo.
FIXTURE_SOURCES = REPO / "tests" / "fixtures" / "sources"


@dataclass(frozen=True)
class Placement:
    host: str
    name: str
    dir: Path
    spec: dict
    # The pointer folder under hosts/ for an external service, None for an in-tree one.
    pointer: Path | None = None


def external_source_dir(name: str, source: dict) -> Path | None:
    """Where a pointer's contract folder is in the fixture tree, or None when absent.

    A pointer to a private repository on a real host has no local tree and yields no
    placement: everything about such a service is checked on the host at converge time.
    """
    folder = FIXTURE_SOURCES / name / source["path"]
    return folder if (folder / "service.yml").exists() else None


def _services_in(dir_: Path) -> list[tuple[str, Path, dict, Path | None]]:
    if not dir_.is_dir():
        return []
    out = []
    for folder in sorted(p for p in dir_.iterdir() if p.is_dir()):
        if (folder / "service.yml").exists():
            out.append((folder.name, folder.resolve(), load_yaml(folder / "service.yml"), None))
        elif (folder / "source.yml").exists():
            resolved = external_source_dir(folder.name, load_yaml(folder / "source.yml"))
            if resolved is not None:
                out.append((folder.name, resolved, load_yaml(resolved / "service.yml"), folder))
    return out


def placements() -> list[Placement]:
    shared = _services_in(HOSTS / "shared" / "services")
    result = []
    for host in host_names():
        for name, dir_, spec, pointer in shared + _services_in(HOSTS / host / "services"):
            result.append(Placement(host, name, dir_, spec, pointer))
    return result


def pointers() -> list[tuple[str, str, Path, dict]]:
    """(owner, name, pointer folder, source.yml) for every pointer; owner is a host or 'shared'."""
    out = []
    for owner_dir in [HOSTS / "shared"] + [HOSTS / h for h in host_names()]:
        for pointer in sorted((owner_dir / "services").glob("*/source.yml")):
            out.append((owner_dir.name, pointer.parent.name, pointer.parent, load_yaml(pointer)))
    return out


def routes_of(spec: dict) -> list[dict]:
    """Every route a service declares: the `routes` list, or the domain+port shorthand."""
    if "routes" in spec:
        return spec["routes"]
    return [{"domain": spec["domain"], "port": spec["port"]}] if "domain" in spec else []


def route_fqdn(route: dict, host_domain: str) -> str:
    """The public name of one route.

    A route's `domain` is a full hostname when it carries a dot and a bare label the
    host's own domain completes otherwise -- the rule in `roles/service/tasks/main.yml`,
    and the dot is all of it, because a DNS label cannot contain one.
    """
    name = route["domain"]
    return name if "." in name else f"{name}.{host_domain}"


def placed_fqdns(host: str) -> list[str]:
    """Every route fqdn placed on a host -- the list the role maps to the host gateway."""
    domain = load_yaml(HOSTS / host / "host.yml")["domain"]
    return sorted(
        {route_fqdn(r, domain) for p in placements() if p.host == host for r in routes_of(p.spec)}
    )


def route_ports(spec: dict) -> list[int]:
    """Every loopback port this service occupies -- including a port without a route."""
    if "routes" in spec:
        return [r["port"] for r in spec["routes"]]
    return [spec["port"]] if "port" in spec else []


def tcp_ports(spec: dict) -> list[dict]:
    """Every plain-TCP port a service claims, normalised to `{port, target}`.

    `{port, target}` is the explicit form -- Traefik listens on `port` and forwards to
    `127.0.0.1:target`. `{range: [lo, hi]}` expands to one entry per port, each
    forwarded to the same number, because the only thing that needs a range is an FTP
    passive port range, where the server advertises the port it is listening on and the
    two sides have to agree.
    """
    out = []
    for entry in spec.get("tcp_ports", []):
        if "range" in entry:
            lo, hi = entry["range"]
            out += [{"port": p, "target": p} for p in range(lo, hi + 1)]
        else:
            out.append({"port": entry["port"], "target": entry.get("target", entry["port"])})
    return out


def placed_tcp_ports(host: str) -> list[int]:
    """Every TCP entrypoint port placed on a host -- the list traefik's unit renders."""
    return sorted({e["port"] for p in placements() if p.host == host for e in tcp_ports(p.spec)})


def host_cfg(host: str) -> dict:
    return load_yaml(HOSTS / host / "host.yml")


def storage_of(host: str) -> dict | None:
    """The host's `storage` block, or None on a host that declares none."""
    return host_cfg(host).get("storage")


def mount_unit(path: str) -> str:
    """The systemd unit name of a mount point, the way systemd-escape spells it.

    The naive escape is exact only because `test_storage` restricts a declared mount
    path to lowercase letters, digits and slashes: a `-`, `.` or `_` in the path would
    need `\\x2d`-style escaping and this would silently produce the wrong unit name.
    """
    return path.lstrip("/").replace("/", "-") + ".mount"


@pytest.fixture(scope="session")
def rendered(tmp_path_factory) -> Path:
    """Every host's playbook output, rendered once per session.

    Lives here rather than beside its first consumer: `from conftest import ...` does not
    carry fixtures, and both `test_render.py` and `test_storage.py` read this tree.
    """
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
                "-e",
                f"tcp_bind_address={RENDER_TCP_BIND_ADDRESS}",
                "-e",
                f"sources_root={FIXTURE_SOURCES}",
            ],
            check=True,
            cwd=str(REPO),
        )
    return out
