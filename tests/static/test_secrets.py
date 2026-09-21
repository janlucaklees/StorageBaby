import re
import subprocess

import pytest

from conftest import REPO, load_yaml, placements

SOPS_CONFIG_NAME = ".sops.yaml"
SOPS_CONFIG = load_yaml(REPO / SOPS_CONFIG_NAME)


def tracked_sops_files() -> list[str]:
    # -c safe.directory: the devtools container runs as root over a repo owned
    # by the operator, which git otherwise refuses to read.
    out = subprocess.run(
        ["git", "-C", str(REPO), "-c", f"safe.directory={REPO}", "ls-files", "*.sops.yaml"],
        capture_output=True,
        text=True,
        check=True,
    )
    # The pathspec also matches .sops.yaml itself, which is the plaintext
    # recipient config, not an encrypted file.
    return sorted(line for line in out.stdout.splitlines() if line and line != SOPS_CONFIG_NAME)


def expected_recipients(relpath: str) -> set[str]:
    for rule in SOPS_CONFIG["creation_rules"]:
        if re.search(rule["path_regex"], relpath):
            return {k for group in rule["key_groups"] for k in group["age"]}
    raise AssertionError(f"{relpath} matches no creation rule in .sops.yaml")


@pytest.mark.parametrize("relpath", tracked_sops_files())
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
