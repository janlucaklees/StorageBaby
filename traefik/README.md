# Traefik

Traefik v3 reverse proxy with automatic wildcard TLS via Let's Encrypt and Porkbun DNS challenge.

## Certificates

Traefik handles cert issuance and renewal automatically via lego. No manual steps needed.

The wildcard cert (`home.klees.io`, `*.home.klees.io`) is requested on first startup and stored in the `letsencrypt` Docker volume at `/letsencrypt/acme.json`. Renewal happens automatically 30 days before expiry.

## Secrets

Porkbun API credentials are mounted as Docker secrets (files in `traefik/secrets/`, git-ignored):

```
traefik/secrets/porkbun_api_key
traefik/secrets/porkbun_secret_api_key
```

Each file contains just the key value (no newline). lego's Porkbun provider reads them via the `_FILE` env var convention (`PORKBUN_API_KEY_FILE`, `PORKBUN_SECRET_API_KEY_FILE`).

## Adding a new service

On the service's secure router, just set `tls: true` — no certresolver needed. Traefik serves the wildcard cert automatically:

```yaml
traefik.http.routers.myapp-secure.entrypoints: "websecure"
traefik.http.routers.myapp-secure.rule: "Host(`myapp.home.klees.io`)"
traefik.http.routers.myapp-secure.tls: "true"
traefik.http.routers.myapp-secure.service: "myapp"
```

## Gotchas

**ACME is router-driven, not entrypoint-driven.**
The entrypoint-level `--entrypoints.websecure.http.tls.certresolver` and `domains` flags do NOT trigger cert requests on their own. Traefik only requests certs when a router with an explicit `certresolver` + `domains` is loaded. The wildcard cert is triggered by the `traefik-ui-secure` router labels in `docker-compose.yml`. Other service routers just use `tls: true` and inherit the already-issued wildcard cert.

**Local DNS interferes with the propagation check.**
lego verifies TXT record propagation using the container's recursive nameservers, which inside Docker resolves to `127.0.0.11` — Docker's internal DNS, which forwards to the host's local resolver. If the local resolver is authoritative for `home.klees.io` (even just for A records), it won't have the ACME TXT record and the propagation check loops forever. Fixed by pointing lego at public resolvers:
```
--certificatesresolvers.porkbun.acme.dnschallenge.resolvers=1.1.1.1:53,8.8.8.8:53
```
