import pytest

from conftest import HOSTS, host_names, load_yaml, placements

REQUIRED = {
    "domain",
    "acme",
    "acme_email",
    "tz",
    # Required, not optional, although the playbook skips the role on a host that declares
    # none: every test in `test_storage.py` is parametrized over the hosts that have a
    # `storage` block, so a host that dropped it would take its whole storage suite with it
    # silently -- and the converge would then recreate `/pool/apps` on the root filesystem,
    # which is the one failure this contract exists to prevent.
    "storage",
    "storage_roots",
    "volume_overrides",
    "deploy_timer",
    "gpu",
    "packages",
    "service_config",
}
# Keys a host may carry and does not have to. The set exists for the same reason
# `SPEC_KEYS` does in `test_contract.py`: without an upper bound a misspelling is silent
# and the override simply never happens -- `tcp_bind_addres` would leave Traefik binding
# the default-route address on a host that was configured away from it.
OPTIONAL = {
    # The address Traefik's plain-TCP entrypoints bind, when the host's default-route
    # address is not the one clients arrive on. Defaults to `ansible_default_ipv4.address`
    # in the playbook; no host declares it today.
    "tcp_bind_address",
}
CLASSES = {"pool", "fast"}


@pytest.mark.parametrize("host", host_names())
def test_host_contract(host):
    cfg = load_yaml(HOSTS / host / "host.yml")
    missing = REQUIRED - set(cfg)
    assert not missing, f"{host}: missing keys {missing}"
    assert set(cfg) <= REQUIRED | OPTIONAL, f"{host}: unknown host keys {set(cfg) - REQUIRED - OPTIONAL}"
    assert isinstance(cfg.get("tcp_bind_address", ""), str)
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


@pytest.mark.parametrize("host", host_names())
def test_mountpoints_is_retired(host):
    cfg = load_yaml(HOSTS / host / "host.yml")
    assert "mountpoints" not in cfg, (
        f"{host}: `mountpoints` is Phase 3's hand-written list. The `storage` role "
        "asserts every mount it declares itself; declare `storage` instead."
    )
