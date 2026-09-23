"""Kopia's basic auth and its container identity.

The generic checks (unit active, container healthy, route file, the FQDN answering a
non-404 over HTTPS) are test_service.py's. They deliberately accept any status from
200 to 499, which is right for a suite that knows nothing about a service -- and it is
exactly why kopia needs its own file: an unauthenticated **200** would pass all of
them while meaning the server had been started with no password at all. That is a
real failure mode here, because the password reaches kopia through
`KOPIA_SERVER_PASSWORD` rather than a flag, so a typo in the variable name silently
opens the UI instead of breaking the start.

So both halves are pinned: anonymous must be refused, and the declared credentials
must be accepted.

The password is read from the service's own podman secret, which is the same value
the container got -- not from the repo, where it is sops-encrypted and, on a test
host, generated per run. It is never printed: no assertion carries it, and no failure
message interpolates the command that used it.
"""

import pytest
from conftest import run_as
from test_service import SPECS, load_spec, placed

KOPIA_SPECS = [(owner, p) for owner, p in SPECS if p.parent.name == "kopia"]
kopia_case = pytest.mark.parametrize(
    "owner,spec_path", KOPIA_SPECS, ids=[f"{owner}/{p.parent.name}" for owner, p in KOPIA_SPECS]
)

STATUS = "curl -s -o /dev/null -w '%{http_code}'"


def config_for(hostvars: dict, spec: dict) -> dict:
    """`service.config` as the role builds it: the host's `service_config` wins."""
    override = hostvars.get("service_config", {}).get(spec["name"], {})
    return {**spec.get("config", {}), **override}


def server_password(host) -> str:
    """The value the container itself was given, straight out of its podman secret."""
    r = run_as(host, "svc-kopia", "podman secret inspect --showsecret --format '{{.SecretData}}' server_password")
    assert r.rc == 0, "could not read the server_password secret"
    secret = r.stdout.strip()
    assert secret, "server_password secret is empty"
    return secret


@kopia_case
def test_anonymous_is_refused(host, owner, spec_path):
    placed(host, owner)
    spec = load_spec(spec_path)
    r = host.run(f"{STATUS} http://127.0.0.1:{spec['port']}/")
    assert r.rc == 0, f"curl failed against kopia: {r.stderr}"
    assert r.stdout.strip() == "401", f"kopia answered {r.stdout.strip()} without credentials, expected 401"


@kopia_case
def test_declared_credentials_are_accepted(host, owner, spec_path):
    hostvars = placed(host, owner)
    spec = load_spec(spec_path)
    user = config_for(hostvars, spec)["server_username"]
    # The password is interpolated here and nowhere else: `-u` keeps it out of the
    # response, and neither assertion below repeats the command or the value.
    r = host.run(f"{STATUS} -u '{user}:{server_password(host)}' http://127.0.0.1:{spec['port']}/")
    assert r.rc == 0, "curl failed against kopia with credentials"
    assert r.stdout.strip() == "200", f"kopia answered {r.stdout.strip()} for user {user}, expected 200"


@kopia_case
def test_container_identity_is_stable(host, owner, spec_path):
    # Kopia names a client `user@hostname`, so the hostname is what its policies and
    # snapshot sources hang off. Podman would default it to the container id, which
    # changes on every recreate -- `HostName=` in the unit is what pins it.
    placed(host, owner)
    r = run_as(host, "svc-kopia", "podman exec kopia hostname")
    assert r.rc == 0, r.stderr
    assert r.stdout.strip() == "kopia", r.stdout
