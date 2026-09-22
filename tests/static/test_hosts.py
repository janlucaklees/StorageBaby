import pytest

from conftest import HOSTS, host_names, load_yaml, placements

REQUIRED = {"domain", "acme", "acme_email", "tz", "storage_roots", "volume_overrides", "deploy_timer", "gpu", "packages", "service_config"}
CLASSES = {"pool", "fast"}


@pytest.mark.parametrize("host", host_names())
def test_host_contract(host):
    cfg = load_yaml(HOSTS / host / "host.yml")
    missing = REQUIRED - set(cfg)
    assert not missing, f"{host}: missing keys {missing}"
    assert isinstance(cfg["acme"], bool)
    assert isinstance(cfg["deploy_timer"], bool)
    assert isinstance(cfg["gpu"], bool)
    assert set(cfg["storage_roots"]) == CLASSES
    assert all(isinstance(v, str) and v.startswith("/") for v in cfg["storage_roots"].values())
    assert isinstance(cfg["volume_overrides"], dict)
    assert all(isinstance(v, str) and v.startswith("/") for v in cfg["volume_overrides"].values())
    assert isinstance(cfg["packages"], list) and all(isinstance(p, str) for p in cfg["packages"])
    assert isinstance(cfg["service_config"], dict)
    placed = {p.name for p in placements() if p.host == host}
    unknown = set(cfg["service_config"]) - placed
    assert not unknown, f"{host}: service_config for services not placed here: {unknown}"
