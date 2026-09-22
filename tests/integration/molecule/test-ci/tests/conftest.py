"""Helpers shared by the verifiers in this directory.

pytest puts a conftest's own directory on sys.path, and these tests are a flat
directory with no package of their own -- so `from conftest import run_as` is how
they reach this.
"""


def run_as(host, user: str, cmd: str):
    """Run `cmd` as a service user, as a client of that user's running systemd manager.

    From /tmp: runuser keeps root's cwd, and rootless podman re-execs inside the user
    namespace, where the service user cannot chdir back into root's 0700 home --
    "cannot chdir to /root: Permission denied".

    DBUS_SESSION_BUS_ADDRESS is what makes this a *client of the running service*
    rather than a stray process: without it podman finds no user session, falls back
    to --cgroup-manager=cgroupfs, and every command that has to place a process in the
    container's cgroup (exec, and healthcheck run, which is an exec) dies with "write
    to .../cgroup.procs: Permission denied" -- that cgroup is delegated to the user
    manager, and only systemd may write it.
    """
    uid = host.user(user).uid
    env = f"XDG_RUNTIME_DIR=/run/user/{uid} DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus"
    return host.run(f"cd /tmp && runuser -u {user} -- env {env} {cmd}")
