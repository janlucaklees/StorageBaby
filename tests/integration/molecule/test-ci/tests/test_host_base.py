import pytest
from test_service import HOSTS, load_spec, placed, placed_tcp_entries, pod_unit, service_case

CERTS = "/etc/storagebaby/traefik/certs"
DEFAULT_CERT = "/etc/storagebaby/traefik/dynamic.d/00-default-certificate.yml"
SVC_HELPER = "/usr/local/sbin/storagebaby-svc"


def hostvars(host) -> dict:
    return load_spec(HOSTS / host.check_output("uname -n") / "host.yml")


def without_acme(host) -> dict:
    """Host vars, or skip: everything below is the `acme: false` branch of host_base."""
    hv = hostvars(host)
    if hv["acme"]:
        pytest.skip("acme host: Traefik gets its certificate from Let's Encrypt")
    return hv


def fingerprint(out: str) -> str:
    """The digest out of `openssl x509 -fingerprint`: `sha256 Fingerprint=AB:CD:...`."""
    assert "=" in out, f"not a fingerprint line: {out!r}"
    return out.strip().split("=", 1)[1]


def served_fingerprint(host, fqdn: str) -> str:
    """What Traefik actually hands out on :443 for `fqdn`, as a sha256 fingerprint."""
    r = host.run(
        f"echo | openssl s_client -connect 127.0.0.1:443 -servername {fqdn} 2>/dev/null "
        "| openssl x509 -noout -fingerprint -sha256"
    )
    assert r.rc == 0, f"no certificate served for {fqdn}: {r.stderr}"
    return fingerprint(r.stdout)


@pytest.mark.parametrize("pkg", ["podman", "passt", "sops", "age", "ansible", "git"])
def test_packages(host, pkg):
    assert host.package(pkg).is_installed


def test_service_helper_installed(host):
    """`storagebaby-svc`, the host's own service control.

    A host carries no task runner: mise is a development tool, and a host is not a checkout
    anybody runs tasks from -- so the five service commands the Makefile used to wrap are a
    program the role installs. `make` left the package list with that Makefile; `aur_build.yml`
    pulls in `base-devel` when it actually has something to compile, which is the only reason
    a host ever needs one.
    """
    f = host.file(SVC_HELPER)
    assert f.exists and f.mode == 0o755
    assert f.user == "root" and f.group == "root"


@pytest.mark.parametrize("argv", ["nonsense traefik", "ps", ""])
def test_service_helper_rejects_a_bad_invocation(host, argv):
    """An unknown action or a missing service name is refused, not guessed at."""
    r = host.run(f"{SVC_HELPER} {argv}")
    assert r.rc == 2, f"rc {r.rc}\n{r.stdout}{r.stderr}"
    assert "usage:" in r.stderr


@service_case
def test_service_helper_resolves_the_right_unit(host, owner, spec_path):
    """The pod unit for a pod service, `<name>.service` for a single-container one.

    This resolution is the whole reason the helper is a program rather than two lines of
    systemctl in the README: which of the two a service is cannot be read off its name, so
    it has to be asked of the service user's manager. Getting it wrong is quiet -- `stop`
    on a `<name>.service` that a pod service does not have reports no such unit and leaves
    the containers running.
    """
    placed(host, owner)
    name = load_spec(spec_path)["name"]
    expected = pod_unit(spec_path) or f"{name}.service"
    # `systemctl status` answers 0 for a running unit and 3 for an inactive one -- both are
    # a resolved unit. A name no unit has is 4, which is the failure this asserts against.
    r = host.run(f"{SVC_HELPER} ps {name}")
    assert r.rc in (0, 3), f"{name}: rc {r.rc}\n{r.stdout}{r.stderr}"
    assert expected in r.stdout, f"{name}: no {expected} in\n{r.stdout}"


def test_unprivileged_ports(host):
    """80 unless a placed service claims a lower TCP port, and then that port.

    Derived rather than pinned at 80: rootless Traefik cannot bind below this line, so the
    day a service declares FTP's control port 21 the sysctl has to come down with it --
    and a constant here would pass on a host where the entrypoint silently failed to bind.
    """
    expected = min([80] + [port for _, port, _ in placed_tcp_entries(host)])
    assert host.run("sysctl -n net.ipv4.ip_unprivileged_port_start").stdout.strip() == str(expected)


def test_platform_dirs(host):
    d = host.file("/etc/storagebaby/traefik/dynamic.d")
    assert d.is_directory and d.mode == 0o755
    assert host.file("/etc/containers/systemd/users").is_directory


def test_deploy_units_installed_but_timer_off_for_test_host(host):
    assert host.file("/etc/systemd/system/storagebaby-deploy.service").exists
    assert host.service("storagebaby-deploy.timer").is_enabled is False


def test_age_key_present(host):
    f = host.file("/etc/storagebaby/age.key")
    assert f.exists and f.mode == 0o600 and f.user == "root"


def test_default_certificate_files(host):
    """The pair host_base generates once, and the dynamic file that installs it.

    0644 on the key is deliberate and documented in the role: Traefik is rootless, so
    `svc-traefik` -- not root -- has to be able to read it, and this branch only ever
    runs on a host with no ACME, whose certificate is self-signed and worth nothing.
    Asserting the mode is what keeps that a decision rather than a drift.
    """
    without_acme(host)
    for name in ("default.crt", "default.key"):
        f = host.file(f"{CERTS}/{name}")
        assert f.exists, f.path
        assert f.mode == 0o644, f"{f.path}: mode {oct(f.mode)}"
        assert f.user == "root" and f.group == "root"
    dynamic = host.file(DEFAULT_CERT)
    assert dynamic.exists and dynamic.mode == 0o644
    # The paths in it are the container's: the unit mounts the certs directory at
    # /etc/traefik/certs, and a host path there would make Traefik fall back to a
    # generated certificate without saying so.
    assert "/etc/traefik/certs/default.crt" in dynamic.content_string
    assert "/etc/traefik/certs/default.key" in dynamic.content_string


def test_traefik_serves_the_default_certificate_across_a_restart(host):
    """The property the stable certificate exists for: a Traefik restart does not change it.

    Traefik with no `tls.stores.default` generates a fresh self-signed certificate every
    time it starts. That is survivable for a browser and fatal for a Kopia backup client,
    which pins the fingerprint it saw at connect time -- one restart and the sidecar is
    locked out of the repository. So the file on disk has to be the certificate really
    served, and it has to still be after a restart.

    A name no router matches is used on purpose: the default certificate is exactly what
    Traefik falls back to, and pinning it to a service's own route would only test that
    route's certificate.
    """
    hv = without_acme(host)
    expected = fingerprint(host.run(f"openssl x509 -in {CERTS}/default.crt -noout -fingerprint -sha256").stdout)
    fqdn = f"no-such-router.{hv['domain']}"
    assert served_fingerprint(host, fqdn) == expected, "Traefik is not serving the certificate on disk"

    r = host.run("systemctl --user -M svc-traefik@ restart traefik.service")
    assert r.rc == 0, r.stderr
    # Polled: the restart is synchronous, Traefik's own startup is not, and the first
    # connection after it can still be refused.
    probe = f"echo | openssl s_client -connect 127.0.0.1:443 -servername {fqdn} 2>/dev/null | grep -q BEGIN"
    up = host.run(f"for _ in $(seq 60); do {probe} && break; sleep 1; done; {probe} && echo up")
    assert up.stdout.strip() == "up", "Traefik did not serve TLS again after its restart"
    assert served_fingerprint(host, fqdn) == expected, "the served certificate changed across a restart"
