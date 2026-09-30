from collections import defaultdict

from conftest import host_names, placements, route_ports, tcp_ports

# Traefik's own two listeners. A service claiming either would either fail to bind or
# take HTTPS off the host.
RESERVED = {80: "traefik's web entrypoint", 443: "traefik's websecure entrypoint"}


def test_ports_are_unique_per_host():
    """Route ports, TCP entrypoints and TCP targets are one namespace.

    A TCP entrypoint binds one concrete address (`tcp_bind_address`) and everything else on
    the host binds loopback, so a public port and a loopback port of the same number do
    coexist -- that is exactly what lets a `tcp_ports` entry forward a port to itself. They
    are still held in one namespace per host, deliberately: `tcp_bind_address` is a host
    variable, and a host that set it to a wildcard would bring the collision straight back,
    with every socket in the pair belonging to a different service. A number on a host
    means one thing.

    Within the pair that is a real collision either way: two services claiming the same
    public port would share one entrypoint and one of their two routers would be dead, and
    a target that equals another service's route port is two processes on 127.0.0.1.

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
                # Counted as a second number only when it is one: `target == port` is the
                # default and the shape a passive range must have, and it is one claim --
                # the entrypoint is on the host's address, the target on loopback.
                if entry["target"] != entry["port"]:
                    seen[entry["target"]].append(f"{p.name} tcp target")
        dupes = {port: names for port, names in seen.items() if len(names) > 1}
        assert not dupes, f"{host}: port collisions {dupes}"
        clash = {port: RESERVED[port] for port in set(seen) & set(RESERVED)}
        assert not clash, f"{host}: {clash} cannot be claimed by a service"
