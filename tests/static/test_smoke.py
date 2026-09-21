import shutil
import subprocess


def test_tooling_present():
    for tool in ["ansible-playbook", "molecule", "sops", "age", "age-keygen", "prettier", "git"]:
        assert shutil.which(tool), f"{tool} missing from devtools image"
    assert shutil.which("quadlet", path="/usr/lib/podman"), "quadlet generator missing"


def test_ansible_collections_present():
    out = subprocess.run(["ansible-galaxy", "collection", "list"], capture_output=True, text=True, check=True).stdout
    for coll in ["community.docker", "community.general", "ansible.posix"]:
        assert coll in out, f"{coll} not installed"
