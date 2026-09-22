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


def load_spec(path: Path) -> dict:
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
    return load_spec(HOSTS / hostname / "host.yml")


def quadlets(spec_path: Path, kind: str) -> list[Path]:
    """Every `quadlet/*.<kind>.j2` of a service, e.g. kind='container'."""
    return sorted((spec_path.parent / "quadlet").glob(f"*.{kind}.j2"))


def unit_stem(template: Path) -> str:
    """`traefik.container.j2` -> `traefik`, the stem Quadlet builds the unit name from."""
    return template.name.removesuffix(".j2").rsplit(".", 1)[0]


def container_name(template: Path) -> str:
    """The container's name: `ContainerName=` if the template sets one, else the stem.

    Reading the raw template is enough -- no unit here templates that line. Quadlet's
    own default would be `systemd-<stem>`, so a unit that does not name itself fails
    the health check below, which is the point: every container in this repo is named
    after its service, and `make logs/ps SERVICE=<name>` relies on it.
    """
    for line in template.read_text().splitlines():
        if line.startswith("ContainerName="):
            return line.split("=", 1)[1].strip()
    return unit_stem(template)


def image_tag(template: Path) -> str | None:
    """The `ImageTag=` a `.build` unit produces, or None when it declares none."""
    for line in template.read_text().splitlines():
        if line.startswith("ImageTag="):
            return line.split("=", 1)[1].strip()
    return None


def _quadlet_case(kind: str):
    cases = [(owner, p, t) for owner, p in SPECS for t in quadlets(p, kind)]
    return pytest.mark.parametrize(
        "owner,spec_path,template",
        cases,
        ids=[f"{owner}/{t.name}" for owner, _, t in cases],
    )


@service_case
def test_service_user_lingers(host, owner, spec_path):
    placed(host, owner)
    user = f"svc-{load_spec(spec_path)['name']}"
    assert host.user(user).exists
    assert host.run(f"loginctl show-user {user} -p Linger").stdout.strip() == "Linger=yes"


@service_case
def test_declared_secrets_are_the_podman_secrets(host, owner, spec_path):
    placed(host, owner)
    spec = load_spec(spec_path)
    user = f"svc-{spec['name']}"
    r = run_as(host, user, "podman secret ls --format '{{.Name}}'")
    assert r.rc == 0, r.stderr
    assert set(r.stdout.split()) == set(spec["secrets"]), r.stdout


@service_case
def test_route_rendered(host, owner, spec_path):
    hostvars = placed(host, owner)
    spec = load_spec(spec_path)
    if "domain" not in spec:
        pytest.skip("no domain, no route")
    f = host.file(f"/etc/storagebaby/traefik/dynamic.d/{spec['name']}.yml")
    assert f.exists and f.mode == 0o644
    assert f"Host(`{spec['domain']}.{hostvars['domain']}`)" in f.content_string


@service_case
def test_volume_dirs_belong_to_the_service(host, owner, spec_path):
    hostvars = placed(host, owner)
    spec = load_spec(spec_path)
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
    user = f"svc-{load_spec(spec_path)['name']}"
    out = host.run(f"systemctl --user -M {user}@ is-enabled podman-auto-update.timer")
    assert out.stdout.strip() == "enabled", out.stderr


@_quadlet_case("container")
def test_container_unit_active(host, owner, spec_path, template):
    placed(host, owner)
    user = f"svc-{load_spec(spec_path)['name']}"
    r = host.run(f"systemctl --user -M {user}@ is-active {unit_stem(template)}.service")
    assert r.stdout.strip() == "active", r.stderr


@_quadlet_case("container")
def test_container_healthy(host, owner, spec_path, template):
    placed(host, owner)
    user = f"svc-{load_spec(spec_path)['name']}"
    r = run_as(host, user, f"podman healthcheck run {container_name(template)}")
    assert r.rc == 0, r.stderr


@_quadlet_case("build")
def test_build_unit_succeeded(host, owner, spec_path, template):
    placed(host, owner)
    user = f"svc-{load_spec(spec_path)['name']}"
    unit = f"{unit_stem(template)}-build.service"
    # `Result=success` alone proves nothing -- it is also what systemd reports for a
    # unit that has never run. The image the build was supposed to produce is the
    # evidence that it actually ran, so both are required.
    result = host.run(f"systemctl --user -M {user}@ show {unit} -p Result --value").stdout.strip()
    assert result == "success", f"{unit}: Result={result}"
    tag = image_tag(template)
    assert tag, f"{template.name}: no ImageTag=, so the build has nothing to be checked against"
    r = run_as(host, user, f"podman image exists {tag}")
    assert r.rc == 0, f"{unit} reports success but image {tag} does not exist: {r.stderr}"


@service_case
def test_domain_answers_over_https(host, owner, spec_path):
    hostvars = placed(host, owner)
    spec = load_spec(spec_path)
    if "domain" not in spec:
        pytest.skip("no domain, no route")
    fqdn = f"{spec['domain']}.{hostvars['domain']}"
    r = host.run(f"curl -sk -o /dev/null -w '%{{http_code}}' -H 'Host: {fqdn}' https://127.0.0.1/")
    code = r.stdout.strip()
    # The rc is not optional: curl prints `000` and exits non-zero when it never got a
    # response at all, and a status test on its own would read that as a pass.
    assert r.rc == 0, f"{fqdn}: curl failed with {code}: {r.stderr}"
    assert code.isdigit(), r.stdout
    # 404 is Traefik matching no router at all, 5xx a backend that cannot answer.
    # Anything in between -- a redirect, a login page, a 401 -- proves the route arrives.
    assert 200 <= int(code) < 500 and code != "404", f"{fqdn} answered {code}"
