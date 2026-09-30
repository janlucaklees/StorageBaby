"""Traefik itself: the secrets it needs, the dashboard, the HTTP redirect.

The generic bits of the contract (user, secrets, route file, volume, auto-update
timer, unit active, container healthy, the FQDN answering over HTTPS) are covered for
every service by test_service.py -- this file only asserts what is specific to Traefik.
"""

import pytest
from conftest import run_as
from test_service import placed_tcp_entries


def test_secrets_mounted(host):
    r = run_as(host, "svc-traefik", "podman exec traefik ls /run/secrets")
    assert r.rc == 0, r.stderr
    assert {"porkbun_api_key", "porkbun_secret_api_key"} <= set(r.stdout.split())


def test_dashboard_via_https(host):
    # Not covered by the generic HTTPS check: that one only asks for a sane status on
    # `/`, this one pins the dashboard path and a real 200 from api@internal.
    r = host.run("curl -sk -o /dev/null -w '%{http_code}' -H 'Host: traefik.test.local' https://127.0.0.1/dashboard/")
    assert r.stdout.strip() == "200", r.stderr


def test_http_redirects_to_https(host):
    r = host.run("curl -s -o /dev/null -w '%{http_code}' -H 'Host: traefik.test.local' http://127.0.0.1/")
    assert r.stdout.strip() in {"301", "302", "308"}, r.stderr


def test_no_root_containers(host):
    r = host.run("podman ps -q")
    assert r.rc == 0, r.stderr
    assert r.stdout.strip() == ""


def test_traefik_is_the_only_listener_on_every_placed_tcp_port(host):
    """Traefik listens on each declared port, on every address, and the pod only on loopback.

    Both halves matter: a port Traefik does not listen on is a scanner that cannot
    connect, and a service that published the same port on 0.0.0.0 would be reachable
    past Traefik -- which is the rule the whole platform is built on.
    """
    entries = placed_tcp_entries(host)
    if not entries:
        pytest.skip("no service on this host claims a TCP port")
    listeners = host.check_output("ss -H -lntp")
    for _, port, target in entries:
        public = [ln for ln in listeners.splitlines() if f":{port} " in ln]
        assert public, f"nothing listens on {port}:\n{listeners}"
        assert all("traefik" in ln for ln in public), f"something other than traefik holds {port}:\n{public}"
        loopback = [ln for ln in listeners.splitlines() if f"127.0.0.1:{target} " in ln]
        assert loopback, f"nothing listens on 127.0.0.1:{target}:\n{listeners}"

