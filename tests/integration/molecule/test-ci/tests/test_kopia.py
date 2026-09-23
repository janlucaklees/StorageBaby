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

Two things keep the password out of this file's output, and both are necessary:

1. **It is never materialised on the controller.** The credential is resolved by a
   command substitution *inside* the shell command that runs on the VM, so what
   crosses back into Python -- and what testinfra keeps in the `CommandResult` it
   records -- is the literal text `$(podman secret inspect ...)`, not the value. The
   value exists only in the VM's shell, for the length of one `curl`.
2. **Assertions are on primitives.** `rc` and `code` are bound to an int and a short
   string before any `assert`, so pytest's assertion rewriter has no `CommandResult`
   to print a repr of when a test fails. An assertion written as `assert r.rc == 0`
   would drag the whole object, command string included, into the failure output.

The secret is read from the service's own podman store rather than the repo, where it
is sops-encrypted and, on a test host, generated fresh per run.
"""

import pytest
from conftest import run_as
from test_service import SPECS, container_name, load_spec, placed, quadlets

KOPIA_SPECS = [(owner, p) for owner, p in SPECS if p.parent.name == "kopia"]
kopia_case = pytest.mark.parametrize(
    "owner,spec_path", KOPIA_SPECS, ids=[f"{owner}/{p.parent.name}" for owner, p in KOPIA_SPECS]
)

STATUS = 'curl -s -o /dev/null -w "%{http_code}"'
# Resolved on the VM, inside the command: see point 1 in the module docstring.
SECRET = "$(podman secret inspect --showsecret --format \"{{.SecretData}}\" server_password)"


def config_for(hostvars: dict, spec: dict) -> dict:
    """`service.config` as the role builds it: the host's `service_config` wins."""
    override = hostvars.get("service_config", {}).get(spec["name"], {})
    return {**spec.get("config", {}), **override}


@kopia_case
def test_anonymous_is_refused(host, owner, spec_path):
    placed(host, owner)
    spec = load_spec(spec_path)
    r = host.run(f"{STATUS} http://127.0.0.1:{spec['port']}/")
    rc, code = r.rc, r.stdout.strip()
    assert rc == 0, f"curl could not reach kopia on port {spec['port']}"
    assert code == "401", f"kopia answered {code} without credentials, expected 401"


@kopia_case
def test_declared_credentials_are_accepted(host, owner, spec_path):
    hostvars = placed(host, owner)
    spec = load_spec(spec_path)
    user = config_for(hostvars, spec)["server_username"]
    # The whole request is one shell command on the VM so that the password is
    # substituted there and never reaches this process.
    probe = f'{STATUS} -u "{user}:{SECRET}" http://127.0.0.1:{spec["port"]}/'
    r = run_as(host, f"svc-{spec['name']}", f"sh -c '{probe}'")
    rc, code = r.rc, r.stdout.strip()
    assert rc == 0, f"curl could not reach kopia on port {spec['port']}"
    assert code == "200", f"kopia answered {code} for user {user}, expected 200"


@kopia_case
def test_container_identity_is_stable(host, owner, spec_path):
    # Kopia names a client `user@hostname`, so the hostname is what its policies and
    # snapshot sources hang off. Podman would default it to the container id, which
    # changes on every recreate -- `HostName=` in the unit is what pins it.
    placed(host, owner)
    spec = load_spec(spec_path)
    # The container is addressed by its declared name; the hostname is asserted
    # against the service name, because `HostName=` is what has to hold here and a
    # renamed container should not be able to satisfy this by accident.
    name = container_name(quadlets(spec_path, "container")[0])
    r = run_as(host, f"svc-{spec['name']}", f"podman exec {name} hostname")
    rc, hostname = r.rc, r.stdout.strip()
    assert rc == 0, f"could not run hostname in the {name} container"
    assert hostname == spec["name"], f"container hostname is {hostname}, expected {spec['name']}"
