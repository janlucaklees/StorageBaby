"""Group-based access to bind paths from inside a rootless container.

Runs for every placed service that declares `binds`: the role must have created the
directory with the declared group and added the service user to it. The test writes a
group-readable file as root and reads it back through the service's first container.
Collects nothing while no placed service has binds.
"""

import pytest
from conftest import run_as
from test_service import SPECS, container_name, load_spec, placed, quadlets

BIND_SPECS = [(owner, p) for owner, p in SPECS if load_spec(p).get("binds")]
bind_case = pytest.mark.parametrize(
    "owner,spec_path", BIND_SPECS, ids=[f"{owner}/{p.parent.name}" for owner, p in BIND_SPECS]
)


@bind_case
def test_service_user_in_bind_groups(host, owner, spec_path):
    placed(host, owner)
    spec = load_spec(spec_path)
    user = f"svc-{spec['name']}"
    groups = set(host.check_output(f"id -nG {user}").split())
    for name, b in spec["binds"].items():
        assert b["group"] in groups, f"{user} not in group {b['group']} for bind {name}"
        d = host.file(b["host"])
        assert d.is_directory and d.group == b["group"], b["host"]


@bind_case
def test_container_can_read_group_file(host, owner, spec_path):
    placed(host, owner)
    spec = load_spec(spec_path)
    user = f"svc-{spec['name']}"
    templates = quadlets(spec_path, "container")
    if not templates:
        pytest.skip("no container to read through yet")
    _, b = next(iter(spec["binds"].items()))
    probe = f"{b['host']}/.storagebaby-probe"
    host.run(f"install -m 0640 -o root -g {b['group']} /dev/null {probe} && echo ok > {probe}")
    r = run_as(host, user, f"podman exec {container_name(templates[0])} cat {b['container']}/.storagebaby-probe")
    host.run(f"rm -f {probe}")
    assert r.rc == 0 and r.stdout.strip() == "ok", r.stderr
