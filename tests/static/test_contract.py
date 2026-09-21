import pytest

from conftest import placements

REQUIRED = {"name", "port", "volumes", "secrets", "backup"}
CLASSES = {"pool", "fast"}


@pytest.mark.parametrize("p", placements(), ids=lambda p: f"{p.host}/{p.name}")
def test_service_contract(p):
    missing = REQUIRED - set(p.spec)
    assert not missing, f"{p.name}: missing keys {missing}"
    assert p.spec["name"] == p.dir.name, "name must equal the folder name"
    assert isinstance(p.spec["port"], int)
    for vol, cfg in p.spec["volumes"].items():
        assert cfg.get("class") in CLASSES, f"volume {vol} has no valid class"
    assert isinstance(p.spec["secrets"], list)
    assert p.spec["backup"] == "none" or {"paths", "schedule", "retention"} <= set(p.spec["backup"])


def test_at_least_one_placement():
    assert placements()
