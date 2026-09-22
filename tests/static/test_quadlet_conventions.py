from pathlib import Path

import pytest

from conftest import placements


def _service_dirs() -> list[Path]:
    # Placements repeat a shared service once per host; the templates are the same file.
    return sorted({p.dir for p in placements()})


def _containers() -> list[Path]:
    return sorted(f for d in _service_dirs() for f in (d / "quadlet").glob("*.container.j2"))


@pytest.mark.parametrize("path", _containers(), ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_container_declares_health_and_restart(path):
    # A container that cannot report its own health is one systemd will happily keep
    # "running" while it serves errors, so every unit declares all three.
    lines = path.read_text().splitlines()
    for required in ["HealthCmd=", "HealthOnFailure=kill", "Restart=always"]:
        assert any(ln.startswith(required) for ln in lines), f"{path.name}: no line starting with {required!r}"
