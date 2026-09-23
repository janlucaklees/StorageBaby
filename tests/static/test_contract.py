import re

import pytest

from conftest import placements

REQUIRED = {"name", "volumes", "secrets", "backup"}
CLASSES = {"pool", "fast"}
MODES = {"ro", "rw"}
GROUP_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
# What a `routes:` entry may carry: the route itself, plus the two backend options a
# single route can override for itself. Everything else about a route lives in the
# service-wide `route:` block.
ROUTE_KEYS = {"domain", "port", "scheme", "insecure_skip_verify"}
# What the service-wide `route:` block may carry: traefik's own two options, plus the
# two backend ones an entry may then override for itself. Unchecked, a misspelling
# here is silent -- the template reads the keys it knows and ignores the rest.
BLOCK_KEYS = {"internal", "wildcard_cert", "scheme", "insecure_skip_verify"}
SCHEMES = {"http", "https"}


def check_backend(p, opts: dict) -> None:
    """`scheme` and `insecure_skip_verify`, wherever the two may be declared.

    Skipping verification only means anything for a TLS backend, and declaring it for a
    plain one is a statement that does nothing -- almost always a service that meant to
    say `scheme: https` as well. So the pair is checked together rather than each alone.
    """
    scheme = opts.get("scheme", "http")
    assert scheme in SCHEMES, f"{p.name}: route scheme must be one of {sorted(SCHEMES)}, not {scheme!r}"
    skip = opts.get("insecure_skip_verify", False)
    assert isinstance(skip, bool), f"{p.name}: insecure_skip_verify must be a boolean"
    assert not (skip and scheme != "https"), f"{p.name}: insecure_skip_verify needs scheme: https"


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
    block = spec.get("route", {})
    assert set(block) <= BLOCK_KEYS, f"{p.name}: unknown route block keys {set(block) - BLOCK_KEYS}"
    routes = spec.get("routes")
    if routes is not None:
        assert "domain" not in spec and "port" not in spec, f"{p.name}: use either routes or domain+port"
        assert isinstance(routes, list) and routes, f"{p.name}: routes must be a non-empty list"
        for r in routes:
            assert {"domain", "port"} <= set(r), f"{p.name}: route entries need domain and port"
            assert set(r) <= ROUTE_KEYS, f"{p.name}: unknown route keys {set(r) - ROUTE_KEYS}"
            assert isinstance(r["port"], int)
            # What the route really gets, which is what the template resolves: the
            # entry over the block. Checking the block on its own instead would call
            # `route: {insecure_skip_verify: true}` an error even when every entry
            # says `scheme: https` -- and would miss an entry that overrides the
            # scheme back to http while the block still skips verification.
            check_backend(p, block | r)
        assert len({r["domain"] for r in routes}) == len(routes), f"{p.name}: duplicate route domains"
    else:
        # The one-route shorthand: the block is the whole of what the route resolves to.
        check_backend(p, block)
    for secret, ref in spec.get("host_secrets", {}).items():
        assert GROUP_RE.match(secret), f"{p.name}: invalid host secret name {secret}"
        assert re.match(r"^[a-z0-9-]+\.[A-Za-z0-9_-]+$", ref), f"{p.name}: host secret {secret} must reference <set>.<key>"
        assert secret not in spec["secrets"], f"{p.name}: {secret} declared in both secrets and host_secrets"
    for hook in spec.get("hooks", {}).get("after_change", []):
        assert {"container", "command"} <= set(hook), f"{p.name}: hook needs container and command"
        assert hook.get("when", "unit_changed") in {"unit_changed", "always"}
    if spec["backup"] != "none":
        # The sidecar the role generates joins the service's pod, so there has to be one.
        pods = list((p.dir / "quadlet").glob("*.pod.j2"))
        assert [q.name for q in pods] == [f"{p.name}.pod.j2"], f"{p.name}: backup requires exactly {p.name}.pod.j2"
        unknown = set(spec["backup"]["paths"]) - set(spec["volumes"])
        assert not unknown, f"{p.name}: backup paths not in volumes: {unknown}"
        assert "kopia_password" in spec.get("host_secrets", {}), f"{p.name}: backup requires host_secrets.kopia_password"
    # A timer without its service never fires; a service without its timer never runs.
    timers = {q.name[:-9] for q in (p.dir / "quadlet").glob("*.timer.j2")}
    services = {q.name[:-11] for q in (p.dir / "quadlet").glob("*.service.j2")}
    assert timers == services, f"{p.name}: every .timer.j2 needs a matching .service.j2 and vice versa"


def test_at_least_one_placement():
    assert placements()
