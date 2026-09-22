"""Every placed service, checked on the VM against its spec in the repo.

testinfra's `host` fixture talks to the VM, but the test process itself runs on the
controller (the devtools container, repo at /repo), so the specs and host vars are
read here with plain Python. Which services are placed depends on the VM's hostname,
which is only known once `host` exists -- so every spec in the repo is parametrized
and the ones belonging to another host skip themselves.
"""

from pathlib import Path

import pytest
import yaml
from conftest import run_as

HOSTS = Path("/repo/hosts")


def _load(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


def _specs() -> list[tuple[str, Path]]:
    """(owner, spec path) for every service spec; owner is 'shared' or a host name."""
    found = [("shared", p) for p in sorted((HOSTS / "shared" / "services").glob("*/service.yml"))]
    for host_dir in sorted(p for p in HOSTS.iterdir() if p.is_dir() and p.name != "shared"):
        found += [(host_dir.name, p) for p in sorted((host_dir / "services").glob("*/service.yml"))]
    return found


SPECS = _specs()
service_case = pytest.mark.parametrize(
    "owner,spec_path", SPECS, ids=[f"{owner}/{p.parent.name}" for owner, p in SPECS]
)


def placed(host, owner: str) -> dict:
    """Host vars for this VM, or skip when the service belongs to a different host."""
    # `uname -n`, not `hostname`: the Arch cloud image ships coreutils but not inetutils.
    hostname = host.check_output("uname -n")
    if owner not in ("shared", hostname):
        pytest.skip(f"not placed on {hostname}")
    return _load(HOSTS / hostname / "host.yml")


@service_case
def test_service_user_lingers(host, owner, spec_path):
    placed(host, owner)
    user = f"svc-{_load(spec_path)['name']}"
    assert host.user(user).exists
    assert host.run(f"loginctl show-user {user} -p Linger").stdout.strip() == "Linger=yes"


@service_case
def test_declared_secrets_are_the_podman_secrets(host, owner, spec_path):
    placed(host, owner)
    spec = _load(spec_path)
    user = f"svc-{spec['name']}"
    r = run_as(host, user, "podman secret ls --format '{{.Name}}'")
    assert r.rc == 0, r.stderr
    assert set(r.stdout.split()) == set(spec["secrets"]), r.stdout


@service_case
def test_route_rendered(host, owner, spec_path):
    hostvars = placed(host, owner)
    spec = _load(spec_path)
    if "domain" not in spec:
        pytest.skip("no domain, no route")
    f = host.file(f"/etc/storagebaby/traefik/dynamic.d/{spec['name']}.yml")
    assert f.exists and f.mode == 0o644
    assert f"Host(`{spec['domain']}.{hostvars['domain']}`)" in f.content_string


@service_case
def test_volume_dirs_belong_to_the_service(host, owner, spec_path):
    hostvars = placed(host, owner)
    spec = _load(spec_path)
    user = f"svc-{spec['name']}"
    for volume, cfg in (spec["volumes"] or {}).items():
        override = hostvars.get("volume_overrides", {}).get(f"{spec['name']}/{volume}")
        path = override or f"{hostvars['storage_roots'][cfg['class']]}/{spec['name']}/{volume}"
        d = host.file(path)
        assert d.is_directory, path
        assert d.user == user, path


@service_case
def test_auto_update_timer_enabled(host, owner, spec_path):
    placed(host, owner)
    user = f"svc-{_load(spec_path)['name']}"
    out = host.run(f"systemctl --user -M {user}@ is-enabled podman-auto-update.timer")
    assert out.stdout.strip() == "enabled", out.stderr
