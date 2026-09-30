import subprocess

from conftest import REPO, load_yaml

UNIT_DIR = REPO / "ansible/roles/host_base/files"
STORAGE_DEFAULTS = REPO / "ansible/roles/storage/defaults/main.yml"


def test_bootstrap_is_valid_bash():
    subprocess.run(["bash", "-n", str(REPO / "bootstrap.sh")], check=True)


def test_bootstrap_embeds_the_same_units_host_base_installs():
    script = (REPO / "bootstrap.sh").read_text()
    for unit in ["storagebaby-deploy.service", "storagebaby-deploy.timer"]:
        body = (UNIT_DIR / unit).read_text().strip()
        assert body in script, f"{unit} in bootstrap.sh drifted from host_base/files/{unit}"


def test_bootstrap_sets_up_the_same_chaotic_aur_as_the_storage_role():
    """bootstrap and `storage/tasks/tools.yml` configure one repository, the same way.

    Two independent copies of the same setup is what this is, and the drift is not
    cosmetic. The `storage` role never runs a full upgrade and never reboots -- package
    upgrades are the operator's -- so the one `pacman -Syu` a host ever gets from this
    repository is bootstrap's, and mergerfs has to be resolvable against it. Different key
    ids or package URLs would leave that upgrade blind to the repository; a section without
    ansible's `blockinfile` markers would make the first converge append a second one.
    """
    script = (REPO / "bootstrap.sh").read_text()
    defaults = load_yaml(STORAGE_DEFAULTS)
    assert defaults["chaotic_key_id"] in script
    assert defaults["chaotic_keyserver"] in script
    for url in defaults["chaotic_packages"]:
        assert url in script, f"bootstrap.sh does not install {url}"
    for line in [
        "# BEGIN ANSIBLE MANAGED chaotic-aur",
        "[chaotic-aur]",
        "Include = /etc/pacman.d/chaotic-mirrorlist",
        "# END ANSIBLE MANAGED chaotic-aur",
    ]:
        assert line in script, f"bootstrap.sh writes no {line!r}"
    # Before the upgrade, or the upgrade cannot see the repository it just gained.
    assert script.index("[chaotic-aur]") < script.index("pacman -Syu"), "the repository is added after the upgrade"


def test_git_ssh_command_is_one_quoted_environment_value():
    # systemd splits an unquoted Environment= on whitespace, so the bare form silently
    # reduces to GIT_SSH_COMMAND=ssh and drops the deploy key and both -o options --
    # it only logs "Invalid environment assignment, ignoring: -i". The byte-equality
    # test above carries the fix into bootstrap.sh.
    lines = (UNIT_DIR / "storagebaby-deploy.service").read_text().splitlines()
    line = next(ln for ln in lines if ln.startswith("Environment=") and "GIT_SSH_COMMAND" in ln)
    assert line.startswith('Environment="') and line.endswith('"'), line
