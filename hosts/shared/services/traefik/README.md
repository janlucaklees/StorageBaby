# Traefik

Rootless Traefik v3 on the host network under `svc-traefik`. It is the only
process on 80/443. Every other service binds `127.0.0.1:<port>` and gets a
route file rendered by the `service` role into
`/etc/storagebaby/traefik/dynamic.d/<name>-<domain>.yml` from its `service.yml`
— one per entry of `routes`, or one for the `domain` + `port` shorthand, each
with its own router and service named `<name>-<domain>`.

A `route:` block carries options for all of a service's routes and a `routes[]`
entry may override them: `internal: api@internal` and `wildcard_cert: true` are
traefik's own, `scheme: https` + `insecure_skip_verify: true` are how Traefik
reaches a backend that keeps its own TLS (kopia, nextcloud's collabora), and
`basic_auth: <secret>` puts a `basicAuth` middleware in front of the router.

## Dashboard

`traefik.<domain>` is `api@internal`, behind basic auth: `route.basic_auth:
dashboard_htpasswd` names the podman secret the `service` role mounts as the
middleware's `usersFile`. The secret is one or more htpasswd lines (`user:hash`,
`openssl passwd -apr1` or `htpasswd -nB`), the same file CloudBaby's compose stack
hands Traefik as `traefik_ui_basicauth`. A `basic_auth` secret is always traefik's
own, whichever route it protects — Traefik opens the file, and a rootless user
cannot see another user's secrets. The test hosts get a generated `test:test` line
from `prepare.yml`, which is what the integration test sends.

## TCP entrypoints

`web` (:80), `websecure` (:443) and `traefik` (127.0.0.1:8080, the dashboard) are
fixed. Everything else on this unit's `Exec=` line is rendered: one entrypoint
`tcp-<port>` on `<tcp_bind_address>:<port>` for every plain-TCP port declared by a
service placed on this host, collected by the playbook into `placed_tcp_ports`.
storagebaby's list is Paperless's FTP drop — 21 plus the passive range 21100–21109; the
test hosts place that and a `tcp-echo` fixture.

A TCP entrypoint binds **one concrete address**, unlike `web` and `websecure`, which
are on the wildcard. The reason is the other end: a service reached over plain TCP
publishes `127.0.0.1:<target>`, and `target` equals `port` whenever the protocol
advertises the port it listens on — an FTP passive range has no other option. A
wildcard listener and a loopback listener on the same port cannot both exist (whichever
binds second gets EADDRINUSE), so Traefik takes the routable address and leaves loopback
to the service. It is still the only process on a routable address, which is the rule
this preserves rather than bends. `tcp_bind_address` defaults to the host's default-route
address (`ansible_default_ipv4.address`, the one fact this playbook gathers, and only on
a host that places a TCP port) and is overridable as a top-level key in `host.yml` on a
host with several addresses. Two consequences: the value cannot be rendered on the
controller, so the static render checks it through a placeholder; and a host whose
address changes needs a converge to re-render this unit.

**An entrypoint Traefik cannot bind is fatal to Traefik, not to that entrypoint.**
Measured on the test VM with Traefik v3.7.13: one TCP entrypoint pointed at an address the
host does not hold, the rest of the unit untouched.

```
ERR Command error error="command traefik error: error while building entryPoint tcp-7777:
building listener: error opening listener: listen tcp <that address>:7777: bind: cannot
assign requested address"
```

The process exits 1 (`Result=exit-code`, `ExecMainStatus=1`), `Restart=always` brings it
back, it fails the same way, and `ss -ltn` shows **no** listener of this service at all —
80, 443 and every other TCP entrypoint are down with it, for as long as the value is wrong.
So a stale `tcp_bind_address` is not "FTP will not transfer"; it is "this host serves
nothing". The two ways it goes stale are a DHCP lease that moves and a NIC that is
replaced, and the fix for both is upstream of Traefik: give the host a static address or a
reservation, or pin `tcp_bind_address` in `host.yml`. No code change: an entrypoint the
operator declared and the kernel refuses is a configuration error, and a Traefik that
carried on without it would serve the other routes while the FTP drop silently accepted
nothing.

Entrypoints are static configuration and cannot be added through the file provider,
which is why they are here and not in `dynamic.d` — and why **a change to the set
restarts Traefik**, once: the port list reaches this template, the template is the
unit, and the `service` role restarts a unit whose file changed. The list is sorted
and de-duplicated in the playbook so that a converge which placed nothing new
renders the same bytes and restarts nothing.

The routers behind those entrypoints are dynamic and belong to the services: the
`service` role renders `dynamic.d/<name>-tcp.yml`. See
`ansible/roles/service/README.md`, "TCP ports".

## Certificates

With `acme: true` in the host's `host.yml`, the dashboard router carries the
`porkbun` resolver and the wildcard domains, which is what triggers issuance;
all other routers just say `tls: {}` and inherit the wildcard cert. State lives
in the `letsencrypt` volume (`fast` class).

**One wildcard per zone, and the zones are the host's.** `cert_zones` in `host.yml`
lists the domains that host can obtain a certificate for and becomes one `domains`
entry each on this router — `main: <zone>`, `sans: *.<zone>`. It defaults to
`[domain]`, so a host whose services all answer under its own domain declares
nothing; storagebaby declares `home.klees.io` and `klees.io`, because four of its
services take names directly under `klees.io` and `*.home.klees.io` does not cover
those. Both zones sit in the same Porkbun account, so the one resolver on this unit
serves both — a zone at a different provider would need a second resolver, and
Traefik takes provider credentials from process environment variables rather than
per resolver, so two accounts at the _same_ provider could not coexist in one
Traefik. The list is also an allowlist: `tests/static/test_hosts.py` refuses a route
whose name is not under any declared zone, because the alternative is a certificate
that silently does not cover it. `docs/ownership.md` § 4.2 has where this is going —
the method and the credentials belong per domain, with the host, and today they are
still one resolver and one `acme` boolean for the whole machine.

With `acme: false` (test hosts) there is no issuance, and Traefik serves a default
certificate for everything. Not the one it invents, though: left to itself Traefik
generates a **fresh** self-signed certificate on every start, and a Kopia backup
sidecar pins the fingerprint it saw when it connected — so one Traefik restart would
lock it out of the repository. So `host_base` generates one self-signed certificate
into `/etc/storagebaby/traefik/certs` (once, ten years, `CN=<domain>` with a wildcard
SAN), the unit mounts that directory read-only at `/etc/traefik/certs`, and a
`00-default-certificate.yml` in `dynamic.d` installs it through `tls.stores.default`.
Stable across restarts is the whole point, and
`tests/.../test_host_base.py::test_traefik_serves_the_default_certificate_across_a_restart`
is what holds it: the fingerprint served on :443 is the file's, before and after a
restart. On an `acme: true` host none of it exists — `host_base` removes the dynamic
file and the directory, and the unit does not mount it.

ACME is router-driven, not entrypoint-driven: only a router with a
`certResolver` and `domains` triggers a request. lego checks DNS propagation
through public resolvers (`1.1.1.1`, `8.8.8.8`) because a local resolver that
is authoritative for the domain never sees the TXT record.

## Secrets

`porkbun_api_key`, `porkbun_secret_api_key` from `secrets.sops.yaml`, mounted
at `/run/secrets/*` and read through lego's `_FILE` convention; `dashboard_htpasswd`
from the same file, read by the dashboard's basicAuth middleware.
