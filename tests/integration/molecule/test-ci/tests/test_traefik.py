"""Traefik itself: the secrets it needs, the dashboard, the HTTP redirect.

The generic bits of the contract (user, secrets, route file, volume, auto-update
timer, unit active, container healthy, the FQDN answering over HTTPS) are covered for
every service by test_service.py -- this file only asserts what is specific to Traefik.
"""

import pytest
from conftest import run_as
from test_service import placed_tcp_entries


def test_secrets_mounted(host):
    r = run_as(host, "svc-traefik", "podman exec traefik ls /run/secrets")
    assert r.rc == 0, r.stderr
    assert {"porkbun_api_key", "porkbun_secret_api_key"} <= set(r.stdout.split())


def test_dashboard_via_https(host):
    # Not covered by the generic HTTPS check: that one only asks for a sane status on
    # `/`, this one pins the dashboard path and a real 200 from api@internal.
    r = host.run("curl -sk -o /dev/null -w '%{http_code}' -H 'Host: traefik.test.local' https://127.0.0.1/dashboard/")
    assert r.stdout.strip() == "200", r.stderr


def test_http_redirects_to_https(host):
    r = host.run("curl -s -o /dev/null -w '%{http_code}' -H 'Host: traefik.test.local' http://127.0.0.1/")
    assert r.stdout.strip() in {"301", "302", "308"}, r.stderr


def test_no_root_containers(host):
    r = host.run("podman ps -q")
    assert r.rc == 0, r.stderr
    assert r.stdout.strip() == ""


def test_traefik_is_the_only_listener_on_every_placed_tcp_port(host):
    """Traefik listens on each declared port, on every address, and the pod only on loopback.

    Both halves matter: a port Traefik does not listen on is a scanner that cannot
    connect, and a service that published the same port on 0.0.0.0 would be reachable
    past Traefik -- which is the rule the whole platform is built on.
    """
    entries = placed_tcp_entries(host)
    if not entries:
        pytest.skip("no service on this host claims a TCP port")
    listeners = host.check_output("ss -H -lntp")
    for _, port, target in entries:
        public = [ln for ln in listeners.splitlines() if f":{port} " in ln]
        assert public, f"nothing listens on {port}:\n{listeners}"
        assert all("traefik" in ln for ln in public), f"something other than traefik holds {port}:\n{public}"
        loopback = [ln for ln in listeners.splitlines() if f"127.0.0.1:{target} " in ln]
        assert loopback, f"nothing listens on 127.0.0.1:{target}:\n{listeners}"


def vm_address(host) -> str:
    """The VM's own routable address -- not loopback, which would prove nothing here.

    `ip` and not `hostname -I`: the Arch cloud image ships coreutils, not inetutils.
    """
    out = host.check_output("ip -4 -o addr show scope global")
    return out.split()[3].split("/")[0]


# Raw TCP from the VM to itself, through Traefik. python3 rather than nc or curl's
# telnet:// -- Ansible needs a python3 on every target, so it is the one client that is
# certainly there, and a socket is exactly what a plain-TCP entrypoint carries.
ECHO_PROBE = """
import socket, sys
s = socket.create_connection((sys.argv[1], int(sys.argv[2])), 20)
s.settimeout(20)
s.sendall(b"storagebaby-tcp\\n")
sys.stdout.write(s.recv(64).decode(errors="replace"))
s.close()
"""


def test_traefik_forwards_a_placed_tcp_port_to_its_backend(host):
    """The whole TCP path in one connection: VM -> Traefik's entrypoint -> loopback backend.

    The listener check above says Traefik holds the port; this one says the router behind
    it reaches the service. They are separate failures -- an entrypoint whose router names
    an entrypoint that does not exist, or a `HostSNI` rule Traefik rejects for a non-TLS
    router, both leave a port that accepts a connection and closes it.

    `tcp-echo` is named because it is the one placed service whose payload is knowable:
    it echoes. The port and target are still read off its spec, so the connection is the
    contract's and not a constant. A service that carried a real protocol would need its
    own client, which is what Task 4 does for FTP.
    """
    entries = [e for e in placed_tcp_entries(host) if e[0] == "tcp-echo"]
    if not entries:
        pytest.skip("tcp-echo is not placed on this host")
    _, port, _ = entries[0]
    r = host.run(f"cat > /tmp/tcp-echo-probe.py <<'PY'\n{ECHO_PROBE}\nPY")
    assert r.rc == 0, r.stderr
    address = vm_address(host)
    # Through the VM's routable address on purpose: 127.0.0.1 would also reach Traefik's
    # `:<port>` entrypoint, but only the routable one is the path a scanner on the LAN
    # takes, and it is the one a `PublishPort` bound to 0.0.0.0 would silently win.
    r = host.run(f"timeout 60 python3 /tmp/tcp-echo-probe.py {address} {port}")
    assert r.rc == 0, f"no echo from {address}:{port}: {r.stderr}"
    assert r.stdout.strip() == "storagebaby-tcp", repr(r.stdout)
