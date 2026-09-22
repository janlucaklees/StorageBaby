"""Traefik itself: the unit runs, the container is healthy, and it serves.

The generic bits of the contract (user, secrets, route file, volume, auto-update
timer) are covered for every service by test_service.py -- this file only asserts
what is specific to Traefik.
"""


def run_as(host, user: str, cmd: str):
    # From /tmp: runuser keeps root's cwd, and rootless podman re-execs inside the
    # user namespace, where the service user cannot chdir back into root's 0700 home.
    #
    # DBUS_SESSION_BUS_ADDRESS is what makes this a *client of the running service*
    # rather than a stray process: without it podman finds no user session, falls back
    # to --cgroup-manager=cgroupfs, and every command that has to place a process in
    # the container's cgroup (exec, and healthcheck run, which is an exec) dies with
    # "write to .../cgroup.procs: Permission denied" -- that cgroup is delegated to the
    # user manager, and only systemd may write it.
    uid = host.check_output(f"id -u {user}")
    env = f"XDG_RUNTIME_DIR=/run/user/{uid} DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus"
    return host.run(f"cd /tmp && runuser -u {user} -- env {env} {cmd}")


def test_unit_active(host):
    r = host.run("systemctl --user -M svc-traefik@ is-active traefik.service")
    assert r.stdout.strip() == "active", r.stderr


def test_container_healthy(host):
    r = run_as(host, "svc-traefik", "podman healthcheck run traefik")
    assert r.rc == 0, r.stderr


def test_secrets_mounted(host):
    r = run_as(host, "svc-traefik", "podman exec traefik ls /run/secrets")
    assert r.rc == 0, r.stderr
    assert {"porkbun_api_key", "porkbun_secret_api_key"} <= set(r.stdout.split())


def test_dashboard_via_https(host):
    r = host.run("curl -sk -o /dev/null -w '%{http_code}' -H 'Host: traefik.test.local' https://127.0.0.1/dashboard/")
    assert r.stdout.strip() == "200", r.stderr


def test_http_redirects_to_https(host):
    r = host.run("curl -s -o /dev/null -w '%{http_code}' -H 'Host: traefik.test.local' http://127.0.0.1/")
    assert r.stdout.strip() in {"301", "302", "308"}, r.stderr


def test_no_root_containers(host):
    assert host.run("podman ps -q").stdout.strip() == ""
