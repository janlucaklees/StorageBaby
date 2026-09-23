from collections import defaultdict

from conftest import host_names, placements, route_ports


def test_loopback_ports_unique_per_host():
    all_placements = placements()
    for host in host_names():
        seen = defaultdict(list)
        for p in all_placements:
            if p.host != host:
                continue
            # Every route's port, not just the first: a multi-route service publishes
            # one loopback port per route, and each of them has to be free on the host.
            for port in route_ports(p.spec):
                seen[port].append(p.name)
        dupes = {port: names for port, names in seen.items() if len(names) > 1}
        assert not dupes, f"{host}: port collisions {dupes}"
