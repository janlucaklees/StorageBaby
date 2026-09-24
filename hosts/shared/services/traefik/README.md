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
in the `letsencrypt` volume (`fast` class). With `acme: false` Traefik serves
its self-signed default certificate (test hosts).

ACME is router-driven, not entrypoint-driven: only a router with a
`certResolver` and `domains` triggers a request. lego checks DNS propagation
through public resolvers (`1.1.1.1`, `8.8.8.8`) because a local resolver that
is authoritative for the domain never sees the TXT record.

## Secrets

`porkbun_api_key`, `porkbun_secret_api_key` from `secrets.sops.yaml`, mounted
at `/run/secrets/*` and read through lego's `_FILE` convention.
