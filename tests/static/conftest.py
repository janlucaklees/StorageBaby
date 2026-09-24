from dataclasses import dataclass
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
HOSTS = REPO / "hosts"


def load_yaml(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


def host_names() -> list[str]:
    return sorted(p.name for p in HOSTS.iterdir() if p.is_dir() and p.name != "shared")


@dataclass(frozen=True)
class Placement:
    host: str
    name: str
    dir: Path
    spec: dict


def _services_in(dir_: Path) -> list[tuple[str, Path, dict]]:
    out = []
    for spec_path in sorted(dir_.glob("*/service.yml")):
        out.append((spec_path.parent.name, spec_path.parent.resolve(), load_yaml(spec_path)))
    return out


def placements() -> list[Placement]:
    shared = _services_in(HOSTS / "shared" / "services")
    result = []
    for host in host_names():
        for name, dir_, spec in shared + _services_in(HOSTS / host / "services"):
            result.append(Placement(host, name, dir_, spec))
    return result


def routes_of(spec: dict) -> list[dict]:
    """Every route a service declares: the `routes` list, or the domain+port shorthand."""
    if "routes" in spec:
        return spec["routes"]
    return [{"domain": spec["domain"], "port": spec["port"]}] if "domain" in spec else []


def placed_fqdns(host: str) -> list[str]:
    """Every route fqdn placed on a host -- the list the role maps to the host gateway."""
    domain = load_yaml(HOSTS / host / "host.yml")["domain"]
    domains = {r["domain"] for p in placements() if p.host == host for r in routes_of(p.spec)}
    return sorted(f"{d}.{domain}" for d in domains)


def route_ports(spec: dict) -> list[int]:
    """Every loopback port this service occupies -- including a port without a route."""
    if "routes" in spec:
        return [r["port"] for r in spec["routes"]]
    return [spec["port"]] if "port" in spec else []
