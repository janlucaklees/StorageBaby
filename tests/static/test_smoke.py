import shutil
import subprocess
from pathlib import Path


def test_tooling_present():
    tools = [
        "ansible-playbook",
        "molecule",
        "sops",
        "age",
        "age-keygen",
        "prettier",
        "git",
        "virsh",
        "qemu-img",
        "mkisofs",
    ]
    for tool in tools:
        assert shutil.which(tool), f"{tool} missing from devtools image"
    assert shutil.which("quadlet", path="/usr/lib/podman"), "quadlet generator missing"


def test_ansible_collections_present():
    out = subprocess.run(["ansible-galaxy", "collection", "list"], capture_output=True, text=True, check=True).stdout
    for coll in ["community.general", "ansible.posix", "community.libvirt"]:
        assert coll in out, f"{coll} not installed"


def test_playbook_syntax():
    subprocess.run(
        ["ansible-playbook", "--syntax-check", "-i", "storagebaby,", "ansible/playbook.yml"],
        check=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
