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
