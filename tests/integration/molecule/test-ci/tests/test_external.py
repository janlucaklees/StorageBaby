"""The external-service path, on the VM: a service placed by a pointer and fetched by the host.

The generic claims (user, secret, route, unit active, healthy, answers over HTTPS) land on
`ext-echo` through test_service.py like on any service. What is specific to a pointer
service is *where its definition came from*, and that is what this file asserts. Everything
here runs after the first deploy, because the fetched tree only exists once a tick ran.
"""

from pathlib import Path

import pytest
from test_service import HOSTS, load_spec, placed

SOURCES = "/var/lib/storagebaby/sources"
FIXTURE = Path("/repo/tests/fixtures/sources")


def pointer_cases() -> list[tuple[str, str, dict]]:
    """(owner, name, source.yml) for every pointer in the repo."""
    out = []
    for owner_dir in [HOSTS / "shared"] + sorted(p for p in HOSTS.iterdir() if p.is_dir() and p.name != "shared"):
        for pointer in sorted((owner_dir / "services").glob("*/source.yml")):
            out.append((owner_dir.name, pointer.parent.name, load_spec(pointer)))
    return out


CASES = pointer_cases()
pointer_case = pytest.mark.parametrize("owner,name,source", CASES, ids=[f"{o}/{n}" for o, n, _ in CASES])


@pytest.mark.order(-1)
@pointer_case
def test_the_host_fetched_the_pointed_repository(host, owner, name, source):
    placed(host, owner)
    checkout = host.file(f"{SOURCES}/{name}")
    # The parent is what keeps the fetched trees root-only (0700); a clone itself is 0755.
    assert checkout.is_directory and checkout.user == "root", f"{SOURCES}/{name}"
    head = host.check_output(f"git -C {SOURCES}/{name} rev-parse HEAD")
    remote = host.check_output(f"git -C /srv/git/{name}.git rev-parse {source['ref']}")
    assert head == remote, f"{name}: fetched {head}, the remote's {source['ref']} is {remote}"
    assert host.file(f"{SOURCES}/{name}/{source['path']}/service.yml").exists
    assert host.file("/var/lib/storagebaby/sources").mode == 0o700


@pytest.mark.order(-1)
@pointer_case
def test_the_secrets_came_from_the_external_tree_and_not_from_an_override(host, owner, name, source):
    """The file the host decrypted is the external repository's, byte for byte.

    A host override under hosts/<host>/secrets/ would also make the generic secret test
    pass; the point of this case is that none exists for this service and the copy on the
    host is the fixture tree's own. The two can be byte-equal on either route -- the
    Molecule converge copying from the controller's fixture tree, the deploy tick copying
    from the fetched checkout -- because prepare generated the file *into* the fixture tree
    and seeded the external remote from that same tree.
    """
    hostname = host.check_output("uname -n")
    placed(host, owner)
    assert not (HOSTS / hostname / "secrets" / f"{name}.sops.yaml").exists(), "an override would hide the external path"
    external = (FIXTURE / name / source["path"] / "secrets.sops.yaml").read_bytes()
    on_host = host.file(f"/etc/storagebaby/{name}.secrets.sops.yaml")
    assert on_host.exists and on_host.mode == 0o600
    assert on_host.content == external


@pytest.mark.order(-1)
@pointer_case
def test_the_page_served_is_the_external_repositorys_config(host, owner, name, source):
    """config/ reached /etc/storagebaby/<name>/ from the fetched tree: the page says so."""
    placed(host, owner)
    hostname = host.check_output("uname -n")
    domain = load_spec(HOSTS / hostname / "host.yml")["domain"]
    spec = load_spec(FIXTURE / name / source["path"] / "service.yml")
    r = host.run(f"curl -sk -H 'Host: {spec['domain']}.{domain}' https://127.0.0.1/")
    assert r.rc == 0, r.stderr
    assert "served from the fixture's own repository" in r.stdout, r.stdout
