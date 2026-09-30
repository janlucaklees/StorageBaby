from collections import defaultdict

from conftest import host_names, placements, route_ports, tcp_ports

# Traefik's own two listeners. A service claiming either would either fail to bind or
# take HTTPS off the host.
RESERVED = {80: "traefik's web entrypoint", 443: "traefik's websecure entrypoint"}


def test_ports_are_unique_per_host():
    """Route ports, TCP entrypoints and TCP targets are one namespace.

    A Traefik entrypoint binds `:<port>`, which is every address including 127.0.0.1 --
    so a public TCP port that equals another service's loopback port is a real collision,
    not a coincidence of numbers. Checking the three sets separately would miss exactly
    that, which is the case the FTP control port (21) is one sysctl away from.

    Overlapping ranges need no check of their own: `tcp_ports` expands a range port by
    port, so two ranges that overlap arrive here as the same number twice.
    """
    all_placements = placements()
    for host in host_names():
        seen = defaultdict(list)
        for p in all_placements:
            if p.host != host:
                continue
            # Every route's port, not just the first: a multi-route service publishes
            # one loopback port per route, and each of them has to be free on the host.
            for port in route_ports(p.spec):
                seen[port].append(f"{p.name} route")
            for entry in tcp_ports(p.spec):
                seen[entry["port"]].append(f"{p.name} tcp entrypoint")
                if entry["target"] != entry["port"]:
                    seen[entry["target"]].append(f"{p.name} tcp target")
        dupes = {port: names for port, names in seen.items() if len(names) > 1}
        assert not dupes, f"{host}: port collisions {dupes}"
        clash = {port: RESERVED[port] for port in set(seen) & set(RESERVED)}
        assert not clash, f"{host}: {clash} cannot be claimed by a service"
