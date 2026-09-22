import subprocess

from conftest import REPO

UNIT_DIR = REPO / "ansible/roles/host_base/files"


def test_bootstrap_is_valid_bash():
    subprocess.run(["bash", "-n", str(REPO / "bootstrap.sh")], check=True)


def test_bootstrap_embeds_the_same_units_host_base_installs():
    script = (REPO / "bootstrap.sh").read_text()
    for unit in ["storagebaby-deploy.service", "storagebaby-deploy.timer"]:
        body = (UNIT_DIR / unit).read_text().strip()
        assert body in script, f"{unit} in bootstrap.sh drifted from host_base/files/{unit}"


def test_git_ssh_command_is_one_quoted_environment_value():
    # systemd splits an unquoted Environment= on whitespace, so the bare form silently
    # reduces to GIT_SSH_COMMAND=ssh and drops the deploy key and both -o options --
    # it only logs "Invalid environment assignment, ignoring: -i". The byte-equality
    # test above carries the fix into bootstrap.sh.
    lines = (UNIT_DIR / "storagebaby-deploy.service").read_text().splitlines()
    line = next(ln for ln in lines if ln.startswith("Environment=") and "GIT_SSH_COMMAND" in ln)
    assert line.startswith('Environment="') and line.endswith('"'), line
