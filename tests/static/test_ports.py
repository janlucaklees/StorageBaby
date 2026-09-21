from collections import defaultdict

from conftest import host_names, placements


def test_loopback_ports_unique_per_host():
    all_placements = placements()
    for host in host_names():
        seen = defaultdict(list)
        for p in all_placements:
            if p.host == host:
                seen[p.spec["port"]].append(p.name)
        dupes = {port: names for port, names in seen.items() if len(names) > 1}
        assert not dupes, f"{host}: port collisions {dupes}"
