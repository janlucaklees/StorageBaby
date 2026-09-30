import re
import subprocess

from conftest import REPO, load_yaml

UNIT_DIR = REPO / "ansible/roles/host_base/files"
STORAGE_DEFAULTS = REPO / "ansible/roles/storage/defaults/main.yml"
STORAGE_TOOLS = REPO / "ansible/roles/storage/tasks/tools.yml"


def pacman_conf_block() -> list[str]:
    """The exact lines the role's `blockinfile` manages in `/etc/pacman.conf`.

    Derived from the task and not spelled out here, which is the whole point of the test
    below: a changed `marker:` or `block:` would otherwise keep the test green while the
    first converge stopped recognising bootstrap's section and appended a second one.
    """
    # The module may be written short or fully qualified, and the role uses the long form;
    # matched on the suffix so neither spelling silently finds nothing.
    tasks = load_yaml(STORAGE_TOOLS)
    blocks = [
        args
        for task in tasks
        for key, args in task.items()
        if key.split(".")[-1] == "blockinfile" and args.get("path") == "/etc/pacman.conf"
    ]
    assert len(blocks) == 1, f"{STORAGE_TOOLS} manages /etc/pacman.conf in {len(blocks)} tasks"
    args = blocks[0]
    return [
        args["marker"].replace("{mark}", "BEGIN"),
        *args["block"].strip().splitlines(),
        args["marker"].replace("{mark}", "END"),
    ]


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
    lines = pacman_conf_block()
    for line in lines:
        assert line in script, f"bootstrap.sh writes no {line!r}"
    # Before the upgrade, or the upgrade cannot see the repository it just gained. Anchored
    # on the start of a line: `script.index("pacman -Syu")` would find the string in a
    # comment as happily as in the command, and silently measure the wrong position.
    upgrade = re.search(r"^pacman -Syu", script, re.M)
    assert upgrade, "bootstrap.sh runs no `pacman -Syu`"
    assert script.index(lines[0]) < upgrade.start(), "the repository is added after the upgrade"


def test_git_ssh_command_is_one_quoted_environment_value():
    # systemd splits an unquoted Environment= on whitespace, so the bare form silently
    # reduces to GIT_SSH_COMMAND=ssh and drops the deploy key and both -o options --
    # it only logs "Invalid environment assignment, ignoring: -i". The byte-equality
    # test above carries the fix into bootstrap.sh.
    lines = (UNIT_DIR / "storagebaby-deploy.service").read_text().splitlines()
    line = next(ln for ln in lines if ln.startswith("Environment=") and "GIT_SSH_COMMAND" in ln)
    assert line.startswith('Environment="') and line.endswith('"'), line
