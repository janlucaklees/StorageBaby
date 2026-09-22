from conftest import run_as


def test_service_user_can_run_rootless_container(host):
    assert host.user("svc-traefik").exists
    assert host.run("loginctl show-user svc-traefik -p Linger").stdout.strip() == "Linger=yes"
    r = run_as(host, "svc-traefik", "podman run --rm docker.io/library/alpine:3 id -u")
    assert r.rc == 0, r.stderr
    assert r.stdout.strip() == "0"


def test_service_user_manager_reachable_from_root(host):
    r = host.run("systemctl --user -M svc-traefik@ is-system-running --wait")
    assert r.stdout.strip() in {"running", "degraded"}, r.stderr


def test_rootless_container_holds_the_privileged_ports(host):
    # The platform's whole port story in one assertion: the sysctl is what lets a
    # rootless container bind 80/443 on the host network, and Traefik -- a rootless
    # container -- is what actually holds them. No separate probe binds them, because
    # Traefik is already there and a second listener would only conflict.
    assert host.run("sysctl -n net.ipv4.ip_unprivileged_port_start").stdout.strip() == "80"
    listeners = host.check_output("ss -Hltnp")
    for port in ("80", "443"):
        # Field 4 of `ss -ltn` is the local address; `*:80` for a dual-stack bind.
        owners = [line for line in listeners.splitlines() if line.split()[3].endswith(f":{port}")]
        assert owners, f"nothing listening on :{port}\n{listeners}"
        assert all('"traefik"' in line for line in owners), owners
