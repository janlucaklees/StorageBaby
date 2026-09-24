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
reaches a backend that keeps its own TLS (kopia, nextcloud's collabora).

## Certificates

With `acme: true` in the host's `host.yml`, the dashboard router carries the
`porkbun` resolver and the wildcard domains, which is what triggers issuance;
all other routers just say `tls: {}` and inherit the wildcard cert. State lives
in the `letsencrypt` volume (`fast` class).

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
at `/run/secrets/*` and read through lego's `_FILE` convention.
