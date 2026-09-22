"""The production deploy path: `storagebaby-deploy.service` pulling the stable branch.

The unit is a oneshot that runs `ansible-pull` against the bare repo seeded in
prepare, so `systemctl start` blocks until the whole convergence is done -- a minute
or two on the first run, which clones the repo first. `timeout` keeps a wedged pull
from hanging the verifier.

These two run last (`order(-1)`, both, which keeps their relative order): the second
one pushes a change to the seeded remote and restarts Traefik, which every other test
would otherwise have to account for.
"""

import pytest

DEPLOY = "timeout 900 systemctl start storagebaby-deploy.service"
JOURNAL = "journalctl -u storagebaby-deploy.service --no-pager | tail -80"
DASHBOARD = "curl -sk -o /dev/null -w '%{http_code}' -H 'Host: traefik.test.local' https://127.0.0.1/dashboard/"


def active_since(host, user, unit):
    cmd = f"systemctl --user -M {user}@ show {unit} -p ActiveEnterTimestampMonotonic --value"
    return host.run(cmd).stdout.strip()


@pytest.mark.order(-1)
def test_deploy_service_is_a_noop_when_nothing_changed(host):
    # The checkout renders byte for byte what the converge already put on the host, so
    # nothing is reported changed and no unit is restarted -- that is the whole claim.
    before = active_since(host, "svc-traefik", "traefik.service")
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout
    assert host.file("/var/lib/storagebaby/repo/ansible/playbook.yml").exists
    assert active_since(host, "svc-traefik", "traefik.service") == before


@pytest.mark.order(-1)
def test_deploy_restarts_only_the_changed_service(host):
    # `tz` reaches exactly one rendered file, traefik.container's Environment=TZ, so a
    # restart of traefik.service is the narrowest observable effect a git change can have.
    before = active_since(host, "svc-traefik", "traefik.service")
    r = host.run(
        "cd /srv/src && sed -i 's|^tz: .*|tz: Europe/Vienna|' hosts/test-a/host.yml "
        "&& git -c user.name=t -c user.email=t@t commit -qam 'change tz' && git push -q origin stable"
    )
    assert r.rc == 0, r.stderr
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout
    assert active_since(host, "svc-traefik", "traefik.service") != before
    assert host.run("systemctl --user -M svc-traefik@ is-active traefik.service").stdout.strip() == "active"
    # Polled, not a single shot: the restart is synchronous but Traefik's own startup
    # is not, so the first request after it can still be refused.
    served = host.run(f'for _ in $(seq 30); do c=$({DASHBOARD}); [ "$c" = 200 ] && break; sleep 1; done; echo "$c"')
    assert served.stdout.strip() == "200", host.run(JOURNAL).stdout
