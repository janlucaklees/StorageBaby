import subprocess

from conftest import REPO

# The scenario's own playbooks, by the names Molecule gives its sequence steps.
MOLECULE_PLAYBOOKS = {"create.yml", "prepare.yml", "converge.yml", "destroy.yml", "verify.yml"}


def test_ansible_lint_is_clean():
    # --offline: the devtools image ships the collections, and the check must not
    # depend on galaxy being reachable. Profile and skips: .ansible-lint at the root.
    proc = subprocess.run(
        ["ansible-lint", "--offline", "ansible/"],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# Every playbook Molecule runs, which `ansible-lint` above does not see: it is pointed at
# `ansible/` alone, and these live under `tests/integration/`. A parse error here costs a
# whole CI integration job -- the VM is created and provisioned before the playbook that
# fails to load is reached -- so it is worth 0.1s of static check. The failure that
# prompted this one: Ansible runs `split_args` over the value of a `shell:` task, so a
# single unpaired apostrophe anywhere in that block, *including inside a shell comment*,
# fails the entire playbook at load time with "failed at splitting arguments, either an
# unbalanced jinja2 block or quotes".
def test_molecule_playbooks_parse():
    playbooks = sorted(
        p for p in (REPO / "tests" / "integration").rglob("*.yml") if p.name in MOLECULE_PLAYBOOKS
    )
    assert playbooks, "no molecule playbooks found -- has the scenario moved?"
    for playbook in playbooks:
        proc = subprocess.run(
            ["ansible-playbook", "--syntax-check", "-i", "localhost,", str(playbook)],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(REPO),
        )
        assert proc.returncode == 0, f"{playbook.relative_to(REPO)}\n{proc.stdout}{proc.stderr}"
