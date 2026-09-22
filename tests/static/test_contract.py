import re

import pytest

from conftest import placements

REQUIRED = {"name", "volumes", "secrets", "backup"}
CLASSES = {"pool", "fast"}
MODES = {"ro", "rw"}
GROUP_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


@pytest.mark.parametrize("p", placements(), ids=lambda p: f"{p.host}/{p.name}")
def test_service_contract(p):
    spec = p.spec
    missing = REQUIRED - set(spec)
    assert not missing, f"{p.name}: missing keys {missing}"
    assert spec["name"] == p.dir.name, "name must equal the folder name"
    if "domain" in spec:
        assert isinstance(spec.get("port"), int), f"{p.name}: a service with a domain needs a loopback port"
    if "port" in spec:
        assert isinstance(spec["port"], int)
    for vol, cfg in spec["volumes"].items():
        assert cfg.get("class") in CLASSES, f"volume {vol} has no valid class"
    for name, b in spec.get("binds", {}).items():
        assert b["host"].startswith("/"), f"bind {name}: host path must be absolute"
        assert b["container"].startswith("/"), f"bind {name}: container path must be absolute"
        assert b.get("mode", "ro") in MODES, f"bind {name}: mode must be ro or rw"
        assert GROUP_RE.match(b["group"]), f"bind {name}: invalid group name"
    for dev in spec.get("devices", []):
        assert dev.startswith("/dev/"), f"device {dev} must be under /dev"
    for g in spec.get("groups", []):
        assert GROUP_RE.match(g), f"invalid group name {g}"
    assert isinstance(spec.get("config", {}), dict)
    assert isinstance(spec["secrets"], list)
    assert spec["backup"] == "none" or {"paths", "schedule", "retention"} <= set(spec["backup"])


def test_at_least_one_placement():
    assert placements()
