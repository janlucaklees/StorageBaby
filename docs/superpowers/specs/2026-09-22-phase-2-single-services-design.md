# Phase 2: single-container services on the platform

Status: reviewed, approved (2026-09-22)
Date: 2026-09-22
Extends: `2026-09-21-gitops-podman-platform-design.md` (sections 5 and 8). Phase 1 delivered the platform with Traefik as the only service.

## 1. Scope

Migrate kopia (server only), jellyfin, stirling-pdf, yuzukam and paperless-upload onto `hosts/storagebaby/services/`, placed on `test-a` by symlink so the integration scenario runs all five. Retire the old `kopia/`, `jellyfin/`, `stirling-pdf/`, `yuzukam/`, `paperless-upload/`, `watchtower/`, `ofelia/` directories. Kopia backup clients (the per-service sidecar) move to Phase 3, where the first pods appear.

Harness work first, because the final Phase 1 review found it: unit-active, container-healthy and HTTPS checks generalised over placed services; `.build` units ordered before the containers that use their image; `make` installed on hosts.

## 2. Contract additions to `service.yml`

```yaml
name: jellyfin
port: 8096 # optional now: required only when `domain` is set
domain: jellyfin
volumes: { config: { class: pool } }
binds: # host paths the service uses but does not own
  media: { host: /pool/shared/media, container: /media, mode: ro, group: media }
devices: [/dev/dri] # only applied when the host declares `gpu: true`
groups: [render, video] # extra host groups for svc-<name>; implies GroupAdd=keep-groups
config: {} # service-level settings, merged with host.yml service_config
secrets: []
backup: none
```

- **`binds`** are paths that live inside this host's folder tree by definition (the service folder is host-specific), so absolute paths are fine. The role ensures the directory exists, ensures the named group exists, adds the service user to it, and sets no recursive permissions. The operator makes the tree readable or writable by that group once; Phase 4's samba role owns those trees on storagebaby. Test hosts get empty directories with the right group.
- **`devices`** render to `AddDevice=` only when `host.yml` has `gpu: true`. Test hosts have no GPU.
- **`groups`** adds `GroupAdd=keep-groups` to the unit so the host supplementary groups reach the container. Used for `/dev/dri` (render, video) and for `binds` groups.
- **`config`** is free-form and readable in templates as `service.config.*`. `host.yml` may override per service with `service_config: { kopia: { repository: filesystem } }`; the role deep-merges host over service. This is how test hosts get a filesystem Kopia repository while storagebaby uses S3.
- `port` becomes optional. `test_ports` ignores services without one. paperless-upload has none.

`host.yml` additions: `gpu: false|true`, `packages: [...]` (extra pacman packages installed by host_base, e.g. Mesa and Vulkan for Jellyfin), `service_config: {}`.

## 3. Services

| Service          | Image and update policy                                          | Port  | Volumes                                                                 | Notes                                                                                                                                                                                                                     |
| ---------------- | ---------------------------------------------------------------- | ----- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| kopia            | `docker.io/kopia/kopia:0.23.1`, pinned                           | 51515 | config pool, cache fast, logs fast, repo fast (filesystem backend only) | server runs plain HTTP on the loopback port, TLS by Traefik. Route backend `http://`.                                                                                                                                     |
| jellyfin         | `lscr.io/linuxserver/jellyfin:latest`, auto                      | 8096  | config pool                                                             | bind media ro; devices `/dev/dri`; groups render, video; `UserNS=keep-id:uid=1000,gid=1000` (PUID/PGID 1000); `JELLYFIN_PublishedServerUrl` from `service.config`.                                                        |
| stirling-pdf     | `docker.stirlingpdf.com/stirlingtools/stirling-pdf:latest`, auto | 8180  | configs, logs, pipeline, tessdata: pool                                 | from the old quadlet attempt; `SECURITY_ENABLELOGIN=true`.                                                                                                                                                                |
| yuzukam          | `ghcr.io/janlucaklees/yuzukam:latest`, auto                      | 3000  | none                                                                    | HTTP router dropped; the global redirect covers it.                                                                                                                                                                       |
| paperless-upload | `.build` from `build/Containerfile` in the service folder        | none  | none                                                                    | bind scans rw; `PAPERLESS_URL` env from `service.config` pointing at `paperless.<domain>`; runs as image user `bun` (uid 1000) with keep-id; `HealthCmd=test -d /data/in`. Uploads fail until Phase 3 delivers Paperless. |

Kopia connection is declared, not clicked: secrets `server_password`, `repository_password`, `b2_key_id`, `b2_application_key`; `service.config`: `repository: s3`, `s3_endpoint`, `s3_bucket`. The start script connects on first start (`kopia repository connect s3 ...`) or, when `repository: filesystem`, creates and connects a repository in the `repo` volume. The self-signed certificate and fingerprint file from the old setup go away; clients will use the Traefik FQDN in Phase 3.

## 4. Role changes

- `units.yml`: unit map gains ordering. Restart order is `.build` → `.pod` → `.container`. A changed `.build` also marks the containers of the same service for restart, since their image changed. `.timer`/plain units remain Phase 3.
- `host.yml`: `groups` membership, `binds` directories and groups, `devices` gating, `GroupAdd`.
- `host_base`: installs `make` and `host.yml` `packages`.
- New static tests: `binds` paths absolute, `devices` absolute, `groups` names valid, `service_config` keys refer to placed services. Contract test: `port` required iff `domain`.
- `test_service.py` gains: every `*.container.j2` of a placed service yields an active `<stem>.service` and a healthy container (name from `ContainerName=` or the stem); every service with `domain` answers on 443 with a non-5xx, non-404 status. `test_traefik.py` keeps only the Traefik-specific assertions.

## 5. Test scenario

`test-a` places all five via symlinks under `hosts/test-a/services/`. `host.yml` for `test-a` gets `service_config.kopia.repository: filesystem`, `gpu: false`. VM grows to 4 vCPUs and 8192 MiB. Generated secrets cover kopia's four keys and the uploader's token. CI runs the same scenario; first-run image pulls make it slower.

## 6. Migration order

1. Harness prep (test generalisation, `.build` ordering, `make`, contract changes, `binds`/`devices`/`groups`/`config` in the role) with Traefik still the only service, all green.
2. yuzukam (simplest), then stirling-pdf, then jellyfin, then paperless-upload (`.build`), then kopia (secrets and start script). Each: service folder, symlink on test-a, generated test secrets, green integration run, old directory deleted.
3. Docs: README service table, CLAUDE.md, operator note on shared-tree group permissions and the one-time Kopia S3 secrets.

## 7. Open item

Ownership and mode of `/pool/shared/media` and `/pool/shared/scans` on storagebaby decide the one-time permission command the operator runs before Jellyfin and the uploader can read them. Default proposal: `chgrp -R media /pool/shared/media && chmod -R g+rX /pool/shared/media`, same with `scans` and `g+rwX`. Not executed by the role.
