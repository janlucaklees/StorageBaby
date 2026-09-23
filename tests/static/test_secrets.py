import re

import pytest

from conftest import HOSTS, REPO, load_yaml, placements

SOPS_CONFIG = load_yaml(REPO / ".sops.yaml")


def sops_files() -> list[str]:
    # Walking hosts/ keeps the plaintext .sops.yaml at the repo root out of the
    # set, and needs no git, so this works in a worktree and in CI alike.
    out = []
    for path in sorted(HOSTS.rglob("*.sops.yaml")):
        rel = path.relative_to(REPO).as_posix()
        if re.match(r"^hosts/test-[^/]+/secrets/", rel):
            continue  # gitignored, test-key-encrypted overrides written by the integration scenario
        out.append(rel)
    return out


def expected_recipients(relpath: str) -> set[str]:
    for rule in SOPS_CONFIG["creation_rules"]:
        if re.search(rule["path_regex"], relpath):
            return {k for group in rule["key_groups"] for k in group["age"]}
    raise AssertionError(f"{relpath} matches no creation rule in .sops.yaml")


@pytest.mark.parametrize("relpath", sops_files())
def test_recipients_match_sops_config(relpath):
    doc = load_yaml(REPO / relpath)
    actual = {entry["recipient"] for entry in doc["sops"]["age"]}
    assert actual == expected_recipients(relpath)


@pytest.mark.parametrize("p", placements(), ids=lambda p: f"{p.host}/{p.name}")
def test_declared_secrets_exist_in_file(p):
    if not p.spec["secrets"]:
        return
    doc = load_yaml(p.dir / "secrets.sops.yaml")
    assert set(p.spec["secrets"]) <= set(doc) - {"sops"}


@pytest.mark.parametrize("p", placements(), ids=lambda p: f"{p.host}/{p.name}")
def test_template_secrets_are_declared(p):
    used = set()
    for tpl in (p.dir / "quadlet").glob("*.j2"):
        used |= set(re.findall(r"^Secret=([A-Za-z0-9_.-]+)", tpl.read_text(), flags=re.M))
    assert used <= set(p.spec["secrets"]), f"undeclared secrets in templates: {used - set(p.spec['secrets'])}"


@pytest.mark.parametrize("p", placements(), ids=lambda p: f"{p.host}/{p.name}")
def test_host_secret_references_resolve(p):
    # Key names are plaintext in a sops file, so a reference can be checked here -- a
    # typo in `<set>.<key>` would otherwise only surface on the host, under no_log.
    for secret, ref in p.spec.get("host_secrets", {}).items():
        set_name, key = ref.split(".", 1)
        set_file = HOSTS / p.host / "secrets" / f"{set_name}.sops.yaml"
        if p.host.startswith("test-"):
            pytest.skip("test hosts generate their secret sets")
        assert set_file.exists(), f"{p.host}: missing secret set {set_file.name} for {p.name}.{secret}"
        assert key in set(load_yaml(set_file)) - {"sops"}, f"{p.host}: {set_name} has no key {key}"
