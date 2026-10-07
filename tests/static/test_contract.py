import re
from collections import defaultdict

import pytest

from conftest import placements, routes_of, tcp_ports

REQUIRED = {"name", "volumes", "secrets", "backup"}
# Every key a `service.yml` may carry. Without this a misspelling is silent and the
# feature simply never runs: `hook:`, `host_secret:`, `route_s:`, `backups:` would all
# be accepted and ignored, by the role as much as by this file. The route block and the
# `routes[]` entries are key-checked below for the same reason, which is the argument
# for doing it at the top level too.
SPEC_KEYS = {
    "name",
    "port",
    "domain",
    "routes",
    "route",
    "volumes",
    "binds",
    "devices",
    "groups",
    "config",
    "secrets",
    "host_secrets",
    "hooks",
    "backup",
    "tcp_ports",
}
CLASSES = {"pool", "fast"}
MODES = {"ro", "rw"}
# What a `backup` block may carry. `exclude` is the one optional key: `{<volume>: [rule]}`,
# gitignore-style rules relative to that volume's snapshot root, for a tree the
# application itself writes and this platform has no reason to carry a second copy of.
BACKUP_KEYS = {"paths", "schedule", "retention", "exclude"}
# What a `volumes` and a `binds` entry may carry. Same argument as SPEC_KEYS, and
# sharper for `owner`: a misspelled `ownner:` would be accepted, ignored, and the
# operator would be back to chowning the tree by hand wondering why the role did not.
VOLUME_KEYS = {"class", "owner"}
BIND_KEYS = {"host", "container", "mode", "group", "owner"}
GROUP_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
# A route's `domain` is either a bare DNS label, which the host's own domain completes,
# or a full hostname, which is used as it stands -- `roles/service/tasks/main.yml`
# tells them apart by the dot. Both forms are checked here because a typo in either is
# otherwise a silently wrong public name: a trailing dot, an underscore or a capital
# would all reach a Traefik `Host()` rule unchallenged.
DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
# What a `routes:` entry may carry: the route itself, plus the two backend options a
# single route can override for itself. Everything else about a route lives in the
# service-wide `route:` block.
ROUTE_KEYS = {"domain", "port", "scheme", "insecure_skip_verify", "basic_auth"}
# What the service-wide `route:` block may carry: traefik's own two options, plus the
# two backend ones an entry may then override for itself. Unchecked, a misspelling
# here is silent -- the template reads the keys it knows and ignores the rest.
BLOCK_KEYS = {"internal", "wildcard_cert", "scheme", "insecure_skip_verify", "basic_auth"}
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


def check_backup(p, spec: dict) -> None:
    """`backup`: either the string `none` or the block, whose keys are closed.

    Closed for the same reason `SPEC_KEYS` is: `excludes:` or `exclude: {upload: ...}`
    against a volume that is not snapshotted would both be accepted and silently never
    reach a kopia policy, and the operator would be left reading a snapshot listing
    wondering why the tree is still in it. A rule may not contain whitespace -- it
    travels to the sidecar in a space-separated environment variable.
    """
    backup = spec["backup"]
    if backup == "none":
        return
    assert {"paths", "schedule", "retention"} <= set(backup), f"{p.name}: backup needs paths, schedule, retention"
    assert set(backup) <= BACKUP_KEYS, f"{p.name}: unknown backup keys {set(backup) - BACKUP_KEYS}"
    for path in backup["paths"]:
        assert path in spec["volumes"], f"{p.name}: backup path {path} is not a volume"
    for vol, rules in backup.get("exclude", {}).items():
        assert vol in backup["paths"], f"{p.name}: exclude names {vol}, which is not in backup.paths"
        assert isinstance(rules, list) and rules, f"{p.name}: exclude {vol} must be a non-empty list"
        for rule in rules:
            assert isinstance(rule, str) and rule, f"{p.name}: exclude {vol} has a non-string rule {rule!r}"
            assert not re.search(r"\s", rule), f"{p.name}: exclude rule {rule!r} contains whitespace"


def check_owner(p, what: str, cfg: dict) -> None:
    """`owner`: the uid the declaring process has *inside* the container, so a bare uid.

    The role maps it onto a host uid once and then chowns a whole tree to whatever it
    says, so this is the cheapest place to catch the two mistakes that cost something:
    the string `'1000'`, which `| int` would quietly turn into 1000 but which no other
    spec value is written as, and a host subuid pasted in by mistake, which is always
    far above the container uid range an image actually uses.
    """
    if "owner" not in cfg:
        return
    uid = cfg["owner"]
    assert isinstance(uid, int) and not isinstance(uid, bool), f"{p.name}: {what} owner must be an int, not {uid!r}"
    assert 0 <= uid <= 65535, f"{p.name}: {what} owner {uid} is outside 0..65535"


@pytest.mark.parametrize("p", placements(), ids=lambda p: f"{p.host}/{p.name}")
def test_service_contract(p):
    spec = p.spec
    missing = REQUIRED - set(spec)
    assert not missing, f"{p.name}: missing keys {missing}"
    assert set(spec) <= SPEC_KEYS, f"{p.name}: unknown service keys {set(spec) - SPEC_KEYS}"
    assert spec["name"] == p.dir.name, "name must equal the folder name"
    if "domain" in spec:
        assert isinstance(spec.get("port"), int), f"{p.name}: a service with a domain needs a loopback port"
    for route in routes_of(spec):
        assert DOMAIN_RE.match(route["domain"]), (
            f"{p.name}: {route['domain']!r} is neither a DNS label nor a hostname"
        )
    if "port" in spec:
        assert isinstance(spec["port"], int)
    for vol, cfg in spec["volumes"].items():
        assert cfg.get("class") in CLASSES, f"volume {vol} has no valid class"
        assert set(cfg) <= VOLUME_KEYS, f"{p.name}: volume {vol} has unknown keys {set(cfg) - VOLUME_KEYS}"
        check_owner(p, f"volume {vol}", cfg)
    for name, b in spec.get("binds", {}).items():
        assert set(b) <= BIND_KEYS, f"{p.name}: bind {name} has unknown keys {set(b) - BIND_KEYS}"
        assert b["host"].startswith("/"), f"bind {name}: host path must be absolute"
        assert b["container"].startswith("/"), f"bind {name}: container path must be absolute"
        assert b.get("mode", "ro") in MODES, f"bind {name}: mode must be ro or rw"
        assert GROUP_RE.match(b["group"]), f"bind {name}: invalid group name"
        check_owner(p, f"bind {name}", b)
    for dev in spec.get("devices", []):
        assert dev.startswith("/dev/"), f"device {dev} must be under /dev"
    for g in spec.get("groups", []):
        assert GROUP_RE.match(g), f"invalid group name {g}"
    assert isinstance(spec.get("config", {}), dict)
    assert isinstance(spec["secrets"], list)
    check_backup(p, spec)
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
    for entry in spec.get("tcp_ports", []):
        assert set(entry) <= {"port", "target", "range"}, f"{p.name}: unknown tcp_ports keys"
        if "range" in entry:
            assert set(entry) == {"range"}, f"{p.name}: a tcp range takes neither port nor target"
            lo, hi = entry["range"]
            assert isinstance(lo, int) and isinstance(hi, int), f"{p.name}: a tcp range is two integers"
            # 1024 and not 1: a single `port` may be privileged because 21 has to be
            # expressible (`host_base` lowers the sysctl for it), but a *range* below 1024
            # would drag the whole host's unprivileged-port floor down with its lowest port.
            assert 1024 <= lo <= hi <= 65535, f"{p.name}: tcp range {lo}-{hi} out of bounds"
            # One entrypoint per port is one listener in Traefik and one PublishPort on
            # the pod; a range of hundreds is a configuration mistake, not a feature.
            assert hi - lo < 64, f"{p.name}: a tcp range of {hi - lo + 1} ports is too many entrypoints"
        else:
            assert isinstance(entry["port"], int) and 1 <= entry["port"] <= 65535
            assert isinstance(entry.get("target", entry["port"]), int)
    # The forwarding side is a loopback port like any other, so two of this service's own
    # entrypoints pointing at one backend port is the same mistake as two services sharing
    # a route port -- and `test_ports` only sees a `target` that differs from its `port`.
    targets = [e["target"] for e in tcp_ports(spec)]
    assert len(set(targets)) == len(targets), f"{p.name}: two tcp ports forward to the same loopback port"
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
        # Its own `secrets` since the shared kopia-clients set was dropped; `host_secrets` is still a valid home for it.
        has_password = "kopia_password" in spec["secrets"] or "kopia_password" in spec.get("host_secrets", {})
        assert has_password, f"{p.name}: backup requires a kopia_password secret"
    # A timer without its service never fires; a service without its timer never runs.
    timers = {q.name[:-9] for q in (p.dir / "quadlet").glob("*.timer.j2")}
    services = {q.name[:-11] for q in (p.dir / "quadlet").glob("*.service.j2")}
    assert timers == services, f"{p.name}: every .timer.j2 needs a matching .service.j2 and vice versa"


def test_at_least_one_placement():
    assert placements()


def test_a_service_placed_on_several_hosts_is_one_folder_or_identical_copies():
    """The convention is a symlink; a copy is allowed but may not drift.

    A service that runs on more than one host normally lives once and is symlinked from
    every other host, which cannot drift at all -- `placements()` resolves the link, so
    those come out as one directory. The exceptions are the test-only fixtures, `tcp-echo`
    and `build-echo`: each belongs to the test hosts and to no real one, so there is nothing
    to link to and each lives as a real folder under both. Two copies that drifted would
    make the two test hosts quietly test two different things.
    """
    by_name = defaultdict(list)
    for p in placements():
        by_name[p.name].append(p.dir)
    for name, dirs in by_name.items():
        trees = {}
        for d in set(dirs):
            trees[d] = {f.relative_to(d).as_posix(): f.read_bytes() for f in sorted(d.rglob("*")) if f.is_file()}
        first = next(iter(trees.values()))
        for d, tree in trees.items():
            assert tree == first, f"{name}: {d} differs from another copy of this service folder"


def test_no_service_name_shadows_another():
    """No service name may be another service name followed by a dash.

    The route-file cleanup in `roles/service/tasks/units.yml` finds the files one service
    owns by globbing `dynamic.d/<name>-*.yml`, and removes the ones that converge did not
    write -- which is what retires a public name a service no longer serves. The glob is
    only unambiguous while no name is a prefix of another in that shape: a service called
    `stirling` would match `stirling-pdf`'s route files and delete them.
    """
    names = sorted({p.name for p in placements()})
    for shorter in names:
        for longer in names:
            assert shorter == longer or not longer.startswith(f"{shorter}-"), (
                f"{longer} shadows {shorter}: the route-file cleanup globs "
                f"`{shorter}-*.yml` and would delete {longer}'s route files"
            )
