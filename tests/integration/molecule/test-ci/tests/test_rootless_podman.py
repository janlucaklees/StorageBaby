def run_as(host, user, cmd):
    # cd out of root's home first: runuser keeps the caller's cwd, and rootless
    # podman re-execs itself inside the user namespace, where svc-probe cannot
    # chdir back into /root (0700) -- "cannot chdir to /root: Permission denied".
    uid = host.user(user).uid
    return host.run(f"cd /tmp && runuser -u {user} -- env XDG_RUNTIME_DIR=/run/user/{uid} {cmd}")


def test_probe_user_can_run_rootless_container(host):
    assert host.user("svc-probe").exists
    assert host.run("loginctl show-user svc-probe -p Linger").stdout.strip() == "Linger=yes"
    r = run_as(host, "svc-probe", "podman run --rm docker.io/library/alpine:3 id -u")
    assert r.rc == 0, r.stderr
    assert r.stdout.strip() == "0"


def test_probe_user_manager_reachable_from_root(host):
    r = host.run("systemctl --user -M svc-probe@ is-system-running --wait")
    assert r.stdout.strip() in {"running", "degraded"}, r.stderr


def test_rootless_container_can_bind_port_80(host):
    assert host.run("sysctl -n net.ipv4.ip_unprivileged_port_start").stdout.strip() == "80"
    r = run_as(host, "svc-probe", "podman run --rm --network host docker.io/library/alpine:3 sh -c 'nc -l -p 80 -s 127.0.0.1 -w 1 </dev/null >/dev/null & sleep 0.5; kill %1 2>/dev/null; echo ok'")
    assert r.rc == 0, r.stderr
