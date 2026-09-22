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
    # Success means nc was still listening when timeout killed it after 2s. Alpine's busybox
    # timeout reports that as 143 (128+SIGTERM); GNU timeout would say 124 -- accept either, so
    # the probe does not silently depend on which one the image ships. A failed bind makes nc
    # exit immediately (rc 1, "nc: bind: Address not available"), so the test fails.
    probe = "timeout 2 nc -l -p 80 -s 127.0.0.1 </dev/null >/dev/null; rc=$?; [ $rc -eq 124 ] || [ $rc -eq 143 ]"
    r = run_as(host, "svc-probe", f"podman run --rm --network host docker.io/library/alpine:3 sh -c '{probe}'")
    assert r.rc == 0, r.stderr
