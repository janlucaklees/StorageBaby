import pytest


@pytest.mark.parametrize("pkg", ["podman", "passt", "sops", "age", "ansible", "git"])
def test_packages(host, pkg):
    assert host.package(pkg).is_installed


def test_unprivileged_ports(host):
    assert host.run("sysctl -n net.ipv4.ip_unprivileged_port_start").stdout.strip() == "80"


def test_platform_dirs(host):
    d = host.file("/etc/storagebaby/traefik/dynamic.d")
    assert d.is_directory and d.mode == 0o755
    assert host.file("/etc/containers/systemd/users").is_directory


def test_deploy_units_installed_but_timer_off_for_test_host(host):
    assert host.file("/etc/systemd/system/storagebaby-deploy.service").exists
    assert host.service("storagebaby-deploy.timer").is_enabled is False


def test_age_key_present(host):
    f = host.file("/etc/storagebaby/age.key")
    assert f.exists and f.mode == 0o600 and f.user == "root"
