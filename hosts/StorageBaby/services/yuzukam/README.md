# Yuzukam

A small self-hosted web app of JLK's, listening on port 3000 in the container.

Image `ghcr.io/janlucaklees/yuzukam:latest`, kept current by `AutoUpdate=registry`
and the service user's `podman-auto-update.timer`.

Reachable at `yuzukam.<host domain>` through Traefik; the container itself binds
`127.0.0.1:3000` only.
