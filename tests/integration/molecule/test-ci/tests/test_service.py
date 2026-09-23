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


def unit_name(template: Path) -> str:
    """`paperless-dump.timer.j2` -> `paperless-dump.timer`, for units systemd reads as they are.

    Timers and their services are plain user units, so the file name *is* the unit
    name -- unlike a Quadlet source, where `.container` becomes `.service`.
    """
    return template.name.removesuffix(".j2")


def pod_unit(spec_path: Path) -> str | None:
    """`<name>-pod.service` when the service is a pod, None when it is one container.

    Quadlet turns `<stem>.pod` into `<stem>-pod.service`. The static contract allows
    exactly one pod per service, named after it, so the first template is the only one.
    """
    pods = quadlets(spec_path, "pod")
    return f"{unit_stem(pods[0])}-pod.service" if pods else None


def backup_paths(spec: dict) -> list[str]:
    """The volumes the generated sidecar snapshots, or [] when the service declares none."""
    return [] if spec["backup"] == "none" else spec["backup"]["paths"]


def volume_path(hostvars: dict, spec: dict, volume: str) -> str:
    """Where one of a service's volumes lives on the host: class root, or an override."""
    override = hostvars.get("volume_overrides", {}).get(f"{spec['name']}/{volume}")
    root = hostvars["storage_roots"][spec["volumes"][volume]["class"]]
    return override or f"{root}/{spec['name']}/{volume}"


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


def kopia_server(host) -> tuple[str, str] | None:
    """(service user, container name) of the kopia server on this VM, or None.

    Looked up rather than spelled out, for the same reason every other check here is
    derived from the repo: a host that places a backup client has to place the server
    too, and this is what says so.
    """
    hostname = host.check_output("uname -n")
    for owner, spec_path in SPECS:
        if spec_path.parent.name == "kopia" and owner in ("shared", hostname):
            return "svc-kopia", container_name(quadlets(spec_path, "container")[0])
    return None


def _template_case(pattern: str):
    """Parametrize over every `quadlet/<pattern>` of every spec, e.g. `*.container.j2`.

    An empty match set is a skipped test rather than a missing one, which is what lets
    a check for a feature nothing uses yet be written before the first service uses it.
    """
    cases = [(owner, p, t) for owner, p in SPECS for t in sorted((p.parent / "quadlet").glob(pattern))]
    return pytest.mark.parametrize(
        "owner,spec_path,template",
        cases,
        ids=[f"{owner}/{t.name}" for owner, _, t in cases],
    )


def _quadlet_case(kind: str):
    return _template_case(f"*.{kind}.j2")


@service_case
def test_service_user_lingers(host, owner, spec_path):
    placed(host, owner)
    user = f"svc-{load_spec(spec_path)['name']}"
    assert host.user(user).exists
    assert host.run(f"loginctl show-user {user} -p Linger").stdout.strip() == "Linger=yes"


@service_case
def test_declared_secrets_are_the_podman_secrets(host, owner, spec_path):
    """Exactly the declared names, from both sources, and nothing left over.

    `secrets` and `host_secrets` land in the same place -- podman secrets of
    `svc-<name>`, synced by the same helper -- and only differ in which file the value
    came from. So the store has to hold the union of the two: checking `secrets` alone
    would call a service with a `kopia_password` wrong, and dropping the equality would
    stop noticing a secret that was removed from the spec but not from the host.
    """
    placed(host, owner)
    spec = load_spec(spec_path)
    user = f"svc-{spec['name']}"
    r = run_as(host, user, "podman secret ls --format '{{.Name}}'")
    assert r.rc == 0, r.stderr
    declared = set(spec["secrets"]) | set(spec.get("host_secrets", {}))
    assert set(r.stdout.split()) == declared, r.stdout


def routes_of(spec: dict) -> list[dict]:
    """Every route a service declares: the `routes` list, or the domain+port shorthand."""
    if "routes" in spec:
        return spec["routes"]
    return [{"domain": spec["domain"], "port": spec["port"]}] if "domain" in spec else []


@service_case
def test_route_rendered(host, owner, spec_path):
    hostvars = placed(host, owner)
    spec = load_spec(spec_path)
    routes = routes_of(spec)
    if not routes:
        pytest.skip("no domain, no route")
    for route in routes:
        f = host.file(f"/etc/storagebaby/traefik/dynamic.d/{spec['name']}-{route['domain']}.yml")
        assert f.exists and f.mode == 0o644
        assert f"Host(`{route['domain']}.{hostvars['domain']}`)" in f.content_string
    # One file per route replaced the single `<name>.yml`; a leftover would keep serving
    # the old router next to the new ones, because Traefik reads the whole directory.
    assert not host.file(f"/etc/storagebaby/traefik/dynamic.d/{spec['name']}.yml").exists


def subuid_range(host, user: str) -> tuple[int, int]:
    """`(first, count)` from /etc/subuid: the host uids this user's containers map into."""
    line = host.run(f"grep '^{user}:' /etc/subuid").stdout.strip()
    assert line, f"{user} has no /etc/subuid allocation"
    first, count = line.split(":")[1:3]
    return int(first), int(count)


@service_case
def test_volume_dirs_belong_to_the_service(host, owner, spec_path):
    hostvars = placed(host, owner)
    spec = load_spec(spec_path)
    user = f"svc-{spec['name']}"
    svc_uid = host.user(user).uid
    first, count = subuid_range(host, user)
    for volume in spec["volumes"] or {}:
        path = volume_path(hostvars, spec, volume)
        d = host.file(path)
        assert d.is_directory, path
        # The role creates the directory svc-owned and then leaves it alone, so an image
        # that chowns its own data tree is expected to show up here: a chown to a non-root
        # uid inside the container lands on a host uid inside the service user's subuid
        # range. Either is the service and nothing else -- another service's user, its
        # subuids or root would all still fail.
        assert d.uid == svc_uid or first <= d.uid < first + count, f"{path}: owned by uid {d.uid}"


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
    routes = routes_of(spec)
    if not routes:
        pytest.skip("no domain, no route")
    # Every route, not just the first: a pod that publishes two ports is exactly the
    # case where checking one of them proves nothing about the other.
    for route in routes:
        fqdn = f"{route['domain']}.{hostvars['domain']}"
        r = host.run(f"curl -sk -o /dev/null -w '%{{http_code}}' -H 'Host: {fqdn}' https://127.0.0.1/")
        code = r.stdout.strip()
        # The rc is not optional: curl prints `000` and exits non-zero when it never got
        # a response at all, and a status test on its own would read that as a pass.
        assert r.rc == 0, f"{fqdn}: curl failed with {code}: {r.stderr}"
        assert code.isdigit(), r.stdout
        # 404 is Traefik matching no router at all, 5xx a backend that cannot answer.
        # Anything in between -- a redirect, a login page, a 401 -- proves the route
        # arrives.
        assert 200 <= int(code) < 500 and code != "404", f"{fqdn} answered {code}"


@service_case
def test_pod_unit_active(host, owner, spec_path):
    """A multi-container service is one pod unit with its containers pulled in behind it.

    The pod is what owns the network namespace the parts share and the ports the route
    points at, so a pod that is not up is a service that is not reachable even when
    every container happens to be running.
    """
    placed(host, owner)
    unit = pod_unit(spec_path)
    if not unit:
        pytest.skip("single-container service, no pod")
    user = f"svc-{load_spec(spec_path)['name']}"
    r = host.run(f"systemctl --user -M {user}@ is-active {unit}")
    assert r.stdout.strip() == "active", r.stderr


@_template_case("*.timer.j2")
def test_timer_enabled_and_active(host, owner, spec_path, template):
    """Both halves: enabled survives a reboot, active is the timer waiting to elapse.

    `is-enabled` alone would pass for a timer that was never started, and `is-active`
    alone for one that runs now and is gone after the next boot.
    """
    placed(host, owner)
    user = f"svc-{load_spec(spec_path)['name']}"
    unit = unit_name(template)
    enabled = host.run(f"systemctl --user -M {user}@ is-enabled {unit}")
    assert enabled.stdout.strip() == "enabled", enabled.stderr
    active = host.run(f"systemctl --user -M {user}@ is-active {unit}")
    assert active.stdout.strip() == "active", active.stderr


@_template_case("*-dump.service.j2")
def test_dump_service_writes_a_dump(host, owner, spec_path, template):
    """A database is backed up as a dump, so the dump has to actually appear.

    Run once here rather than waited for: the timer's schedule is nightly, and the
    claim worth testing is that the command in the unit works against the running
    database container -- not that systemd can tell the time.
    """
    hostvars = placed(host, owner)
    spec = load_spec(spec_path)
    user = f"svc-{spec['name']}"
    unit = unit_name(template)
    assert "backups" in (spec["volumes"] or {}), f"{spec['name']}: a dump unit needs a `backups` volume"
    path = volume_path(hostvars, spec, "backups")
    # A oneshot's `start` blocks until the job is done, so the dump is complete when
    # this returns and a failing dump is a failing start -- no polling, no sleep.
    r = host.run(f"timeout 600 systemctl --user -M {user}@ start {unit}")
    journal = f"journalctl _SYSTEMD_USER_UNIT={unit} --no-pager | tail -40"
    assert r.rc == 0, host.run(journal).stdout
    listing = host.run(f"ls -1 {path}")
    assert any(f.endswith(".dump") for f in listing.stdout.split()), (
        f"{path} holds no *.dump after {unit}: {listing.stdout!r}\n{host.run(journal).stdout}"
    )


@service_case
def test_backup_sidecar_snapshots_to_the_server(host, owner, spec_path):
    """The whole backup path in one test: client connected, snapshot taken, server has it.

    Checking only `repository status` would pass for a client that can log in and write
    nothing, and checking only the sidecar's own view would pass for a snapshot that
    never left it. So the snapshot is triggered on the client and looked for on the
    server, under the `<service>@<host>` identity the server registered it as -- which
    is also what proves the two halves of the shared `kopia-clients` password match.
    """
    placed(host, owner)
    spec = load_spec(spec_path)
    paths = backup_paths(spec)
    if not paths:
        pytest.skip("no backup")
    name = spec["name"]
    user = f"svc-{name}"
    r = run_as(host, user, f"podman exec {name}-backup kopia repository status")
    assert r.rc == 0, f"{name}-backup is not connected to the repository: {r.stderr}"
    # One path is enough: the policy loop in the sidecar script is the same for each,
    # and the first one is the one the retention policy was set on first.
    first = paths[0]
    r = run_as(host, user, f"podman exec {name}-backup kopia snapshot create /data/{first}")
    assert r.rc == 0, f"{name}-backup could not snapshot /data/{first}: {r.stderr}"
    server = kopia_server(host)
    assert server, f"{name} declares a backup but no kopia server is placed on this host"
    kopia_user, kopia_container = server
    r = run_as(host, kopia_user, f"podman exec {kopia_container} kopia snapshot list --all")
    assert r.rc == 0, f"could not list snapshots on the kopia server: {r.stderr}"
    source = f"{name}@{host.check_output('uname -n')}:/data/{first}"
    assert source in r.stdout, f"{source} is not among the server's snapshot sources:\n{r.stdout}"
