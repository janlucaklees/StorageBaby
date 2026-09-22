import subprocess

from conftest import REPO


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
