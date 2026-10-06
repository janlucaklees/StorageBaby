import pytest

from conftest import HOSTS, host_names, load_yaml, placements, route_fqdn, routes_of

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
    # The domains this host can obtain a certificate for -- one wildcard request each.
    # Defaults to `[domain]` in the playbook, so a host whose services all take names
    # under its own domain declares nothing.
    "cert_zones",
}
CLASSES = {"pool", "fast"}


@pytest.mark.parametrize("host", host_names())
def test_host_contract(host):
    cfg = load_yaml(HOSTS / host / "host.yml")
    missing = REQUIRED - set(cfg)
    assert not missing, f"{host}: missing keys {missing}"
    assert set(cfg) <= REQUIRED | OPTIONAL, f"{host}: unknown host keys {set(cfg) - REQUIRED - OPTIONAL}"
    assert isinstance(cfg.get("tcp_bind_address", ""), str)
    zones = cfg.get("cert_zones", [cfg["domain"]])
    assert isinstance(zones, list) and all(isinstance(z, str) and "." in z for z in zones), (
        f"{host}: cert_zones must be a list of domains"
    )
    assert cfg["domain"] in zones, f"{host}: cert_zones must contain the host's own domain"
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


@pytest.mark.parametrize("host", host_names())
def test_every_placed_route_is_under_a_domain_the_host_can_certify(host):
    """A service may only take a name under a domain this host says it can certify.

    `cert_zones` is the allowlist (`docs/ownership.md` § 4.2). Traefik asks Let's Encrypt
    for one wildcard per zone and every other router inherits what it obtained, so a
    route under some other zone is served a certificate that does not cover it -- which a
    browser refuses and which no integration test can catch, because the test hosts have
    `acme: false` and no issuance at all.
    """
    cfg = load_yaml(HOSTS / host / "host.yml")
    if not cfg["acme"]:
        pytest.skip("no issuance on this host, so nothing to be covered by")
    zones = cfg.get("cert_zones", [cfg["domain"]])
    for p in (p for p in placements() if p.host == host):
        for route in routes_of(p.spec):
            fqdn = route_fqdn(route, cfg["domain"])
            zone = fqdn.split(".", 1)[1]
            assert zone in zones, (
                f"{host}/{p.name}: {fqdn} is not under any zone this host can certify "
                f"({', '.join(zones)}). Add the zone to cert_zones, or take a name under one."
            )
