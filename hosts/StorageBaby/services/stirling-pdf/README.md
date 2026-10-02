# Stirling-PDF

Self-hosted PDF tools (merge, split, convert, OCR, etc.), reachable at
`stirling.<host domain>`. Login is on (`SECURITY_ENABLELOGIN=true`).

## How it runs

Rootless Podman + Quadlet as `svc-stirling-pdf`, converged by the `service`
role from this folder. There never was a `docker-compose.yml` for it — it was
built on the Podman/Quadlet pattern from the start.

The four `pool`-class volumes hold the state that must survive an image swap:
`configs`, `logs`, `pipeline` and the `tessdata` OCR language files.

The image chowns and chmods `/configs`, `/logs` and `/pipeline` to its own
in-container user on every start, so on the host those directories end up owned
by a subuid of `svc-stirling-pdf` and mode 0755. That is fine and expected: the
`service` role creates a volume directory and then never re-permissions it.

## Health check

`HealthCmd` probes `/api/v1/info/status`, not `/`. With login enabled `/` answers
**401**, and `curl -f` reports any 4xx as a failure, so a check on `/` could never
go healthy. `/api/v1/info/status` is the app's own status endpoint, public in both
login modes, and answers 200 — it is what upstream's own `HEALTHCHECK` uses.

Startup is slow — it is a Spring Boot app — hence `HealthStartPeriod=120s`;
without it `HealthOnFailure=kill` would kill a container that is merely still
booting.

The same 401 is what Traefik returns for an anonymous `GET /`, which is a correct
answer, not an outage.

## Networking

The container binds `127.0.0.1:8180` only, never the LAN interface.
Reachability comes entirely from Traefik, whose route file the `service` role
renders from `domain` and `port` in `service.yml`. Traefik finds it through its
file provider, not Docker labels — Podman containers are invisible to Traefik's
Docker provider whatever the network mode.

## Updates

`AutoUpdate=registry`, backed by `svc-stirling-pdf`'s `podman-auto-update.timer`:
it checks for a new image periodically and rolls back automatically if the new
one fails its health check.

## Changing something

Edit `service.yml` or `quadlet/stirling-pdf.container.j2` and let the host's
deploy timer converge — the role re-renders the unit and restarts it, and a
changed `domain`/`port` rewrites the Traefik route with no restart at all.
