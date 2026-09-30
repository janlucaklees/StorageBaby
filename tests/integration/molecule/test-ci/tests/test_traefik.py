"""Traefik itself: the secrets it needs, the dashboard, the HTTP redirect.

The generic bits of the contract (user, secrets, route file, volume, auto-update
timer, unit active, container healthy, the FQDN answering over HTTPS) are covered for
every service by test_service.py -- this file only asserts what is specific to Traefik.
"""

import re

import pytest
from conftest import run_as
from test_service import HOSTS, load_spec, placed_tcp_entries


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


# `--entrypoints.tcp-<port>.address=<address>:<port>` as the rendered unit carries it.
TCP_ENTRYPOINT = re.compile(r"--entrypoints\.tcp-(\d+)\.address=([^:\s\\]+):(\d+)")


def traefik_tcp_bind_address(host) -> str:
    """The address Traefik was told to bind its TCP entrypoints on, from its rendered unit.

    Read from the unit rather than recomputed here, because the unit is what Traefik was
    started with -- and the value is a fact of the host (`ansible_default_ipv4.address`),
    which a test on the controller cannot derive. What the assertions below then add is
    that the address is really the VM's own routable one and that Traefik really holds it.
    """
    unit = host.file(f"/etc/containers/systemd/users/{host.user('svc-traefik').uid}/traefik.container")
    assert unit.exists, unit.path
    found = TCP_ENTRYPOINT.findall(unit.content_string)
    assert found, f"traefik's unit renders no TCP entrypoint:\n{unit.content_string}"
    addresses = {address for _, address, _ in found}
    assert len(addresses) == 1, f"traefik binds its TCP entrypoints on several addresses: {addresses}"
    return addresses.pop()


def listener_addresses(host) -> list[tuple[str, str]]:
    """(local address:port, the rest of the line) for every listening TCP socket.

    The local address is column 3 of `ss -H -lntp` and it is taken as a whole: a substring
    search for `:<port>` also matches `127.0.0.1:<port>`, which is precisely the line a
    port forwarded to itself adds -- and the old form of this test would then have blamed
    the pod for holding Traefik's port.
    """
    out = []
    for line in host.check_output("ss -H -lntp").splitlines():
        fields = line.split()
        if len(fields) >= 4:
            out.append((fields[3], line))
    return out


WILDCARDS = ("*", "0.0.0.0", "[::]", "::")


def test_traefik_is_the_only_routable_listener_on_every_placed_tcp_port(host):
    """Traefik holds `<tcp_bind_address>:<port>`, the service holds `127.0.0.1:<target>`.

    Three claims, and each is a different failure. A port Traefik does not hold is a
    scanner that cannot connect. A wildcard listener on that port -- Traefik's own, or a
    service that published past loopback -- breaks the rule the whole platform rests on,
    that Traefik is the only process on a routable address, and it is also the collision
    that made this necessary: a wildcard and `127.0.0.1:<port>` cannot both bind, so
    whichever of the two started second would be dead. And a missing loopback listener is
    a router forwarding to nothing.

    The bind address is asserted to be the host's own: the default is the primary-address
    fact, and a `host.yml` that overrides it says so here too.
    """
    entries = placed_tcp_entries(host)
    if not entries:
        pytest.skip("no service on this host claims a TCP port")
    bind = traefik_tcp_bind_address(host)
    hostvars = load_spec(HOSTS / host.check_output("uname -n") / "host.yml")
    assert bind == hostvars.get("tcp_bind_address", vm_address(host)), bind
    assert bind not in WILDCARDS, f"traefik's TCP entrypoints are on the wildcard: {bind}"
    listeners = listener_addresses(host)
    for _, port, target in entries:
        public = [line for local, line in listeners if local == f"{bind}:{port}"]
        assert public, f"traefik does not listen on {bind}:{port}:\n{listeners}"
        assert all("traefik" in line for line in public), f"something other than traefik holds {port}:\n{public}"
        wildcard = [line for local, line in listeners if local in [f"{w}:{port}" for w in WILDCARDS]]
        assert not wildcard, f"{port} is bound on a wildcard address:\n{wildcard}"
        loopback = [line for local, line in listeners if local == f"127.0.0.1:{target}"]
        assert loopback, f"nothing listens on 127.0.0.1:{target}:\n{listeners}"


def test_no_tcp_port_was_lost_to_an_address_collision(host):
    """Neither journal says "address already in use" for a placed TCP port.

    The assertions above read the sockets that exist now; this one reads what happened
    when they were bound. A listener lost to EADDRINUSE does not stay lost -- both units
    carry `Restart=always`, so the pair flaps rather than failing, and a converge that
    happened to catch the good half of the cycle would pass everything above.

    Asserted on what the journal *says*, not on grep's exit status: `journalctl | grep`
    answers 1 for a clean journal and 1 for an empty one, so the same pass would have come
    back from a rotated journal, a `_UID=` that matched nothing, or a `journalctl` that
    failed with `-q` swallowing its own diagnostic. This is the one assertion standing for
    the whole entrypoint-versus-loopback fix, so it reads the haystack first and requires it
    to be non-empty.

    Read per service *user* rather than per unit: the error can come from either side and
    from either process -- Traefik's own listener, or the rootlessport helper podman starts
    for a `PublishPort=` -- and a unit filter would have to guess which unit of a pod
    service logs it. Everything a service user logs belongs to that service anyway.
    """
    entries = placed_tcp_entries(host)
    if not entries:
        pytest.skip("no service on this host claims a TCP port")
    users = ["svc-traefik"] + sorted({f"svc-{name}" for name, _, _ in entries})
    for user in users:
        uid = host.user(user).uid
        r = host.run(f"journalctl _UID={uid} --no-pager -q")
        assert r.rc == 0, f"journalctl for {user} (uid {uid}) failed: {r.stderr}"
        assert r.stdout.strip(), f"{user} (uid {uid}) logged nothing at all: there is no haystack here"
        offending = [line for line in r.stdout.splitlines() if "address already in use" in line.lower()]
        assert not offending, "{} could not bind a port:\n{}".format(user, "\n".join(offending[:20]))


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
    own client, which is what `test_ftp.py` does for paperless's FTP drop.
    """
    entries = [e for e in placed_tcp_entries(host) if e[0] == "tcp-echo"]
    if not entries:
        pytest.skip("tcp-echo is not placed on this host")
    _, port, _ = entries[0]
    r = host.run(f"cat > /tmp/tcp-echo-probe.py <<'PY'\n{ECHO_PROBE}\nPY")
    assert r.rc == 0, r.stderr
    address = vm_address(host)
    # The VM's routable address is now the only address that reaches Traefik at all: the
    # entrypoint binds it and nothing else, so `127.0.0.1:<port>` is the service's own
    # publish -- a connect there would echo back without Traefik in the path and prove
    # nothing. It is also the path a scanner on the LAN takes.
    r = host.run(f"timeout 60 python3 /tmp/tcp-echo-probe.py {address} {port}")
    assert r.rc == 0, f"no echo from {address}:{port}: {r.stderr}"
    assert r.stdout.strip() == "storagebaby-tcp", repr(r.stdout)
