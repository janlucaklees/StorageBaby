"""Traefik itself: the secrets it needs, the dashboard, the HTTP redirect.

The generic bits of the contract (user, secrets, route file, volume, auto-update
timer, unit active, container healthy, the FQDN answering over HTTPS) are covered for
every service by test_service.py -- this file only asserts what is specific to Traefik.
"""

from conftest import run_as


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
