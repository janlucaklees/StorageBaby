# Yuzukam

A small self-hosted web app of JLK's, listening on port 3000 in the container.

Image `ghcr.io/janlucaklees/yuzukam:latest`, kept current by `AutoUpdate=registry`
and the service user's `podman-auto-update.timer`.

Reachable at `yuzukam.<host domain>` through Traefik; the container listens on 3000 —
its `container_port` — and the unit publishes that on loopback only, at the port Cradle
derives for the `yuzukam` route.
