"""The pointer fixture data every external, order-sensitive case parametrizes on.

`pointer_cases()`, `pointer_case`, `FIXTURE`, `SOURCES` and `AUTH_JSON` live here and are
imported by `test_deploy.py`, where the actual `order(-1)` assertions about a pointer's
checkout, its secrets, the page it serves and its registry login now live. They used to be
declared in this file with their own `order(-1)` marks, but pytest-order piles every
`order(-1)` item at the end and breaks ties among them by *collection position*, and
collection is alphabetical by file -- so a case declared here always collected, and so
always ran, after every `order(-1)` case in `test_deploy.py`, including the one that
removes the pointer's fetched checkout and the one that remounts the pool. The fetch no
longer existed by the time `test_the_host_fetched_the_pointed_repository` ran.

The one test below carries no `order(-1)`: an in-tree service's absence of a registry
login needs no particular position, so it stays here rather than moving for no reason.
"""

from pathlib import Path

import pytest
from test_service import HOSTS, load_spec

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

AUTH_JSON = ".config/containers/auth.json"


def test_an_in_tree_service_has_no_registry_login(host):
    home = host.user("svc-traefik").home
    assert not host.file(f"{home}/{AUTH_JSON}").exists
