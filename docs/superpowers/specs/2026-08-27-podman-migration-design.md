# StorageBaby: Docker → Podman/Quadlet Migration Design

Status: draft, pending review
Date: 2026-08-27

## 1. Motivation

The repo currently runs every service as a Docker Compose stack (`<service>/docker-compose.yml`), operated via `manage.sh` (outdate, not used) / per-service `Makefile`s, with Watchtower doing blind image-swap updates and no isolation between services beyond Docker's own container boundary.

Pain points driving this migration:

- Everything runs as one shared Docker daemon/root trust boundary — no isolation between services.
- Updates are manual per-service, or blind (Watchtower swaps images with no health-gated rollback).
- No declarative, git-driven reconciliation — state only matches the repo if someone runs the right command.
- The disk hosting this is tight on space; there's no deliberate control over where a service's data or the platform's own state lands.
- Any automation (including AI-driven changes) has to go through hand-run commands, not a repo-is-truth model.

## 2. Goals

- Each service is isolated under its own dedicated, unprivileged Linux user (rootless Podman, separate user namespace per service).
- Each service stays fully self-contained in its own repo folder — config, secrets, unit definitions, operational Makefile — and can be provisioned and run independently. The **only** external dependency any service has is Traefik itself (via a small file-provider snippet it contributes, see §7).
- Host-level setup (users, subuid/subgid ranges, packages, base directories) is declarative and idempotent, not manual.
- Config changes land by editing files in the repo and pushing — no more "someone has to remember to run the compose command."
- Image updates are health-checked, with automatic rollback on failure — no more blind Watchtower swaps.
- A lightweight web UI exists for visibility and ad hoc container testing, without becoming a second source of truth.
- The existing storage-maintenance pipeline (snapraid balance/sync/scrub) continues to be able to stop/start services around maintenance windows.

## 3. Non-goals / deferred

- **Multi-node / clustering** — single NAS box only; nothing here assumes more machines join later.
- **Build-from-source deploys** — the platform deploys pre-built images (`podman auto-update` pulling from a registry); it does not build images itself. CI/build remains external.
- **Polished self-service test-environment UI** — Cockpit's ad hoc "run a container" view covers this adequately; no bespoke tooling is being built for it.
- **Nomad, Komodo, Coolify/CapRover/Dokploy, YunoHost** — all considered and rejected during design (see §10 for why). Not revisited unless a future requirement invalidates the reasoning below.

## 4. Architecture overview

```
Ansible (repo: ansible/)
  Provisions the host:
    - one dedicated system user per service (svc-jellyfin, svc-paperless, ...)
    - subuid/subgid range per user
    - `loginctl enable-linger <user>` so each user's systemd --user instance
      runs without an active login
    - base packages: podman, cockpit, cockpit-podman
    - per-service directory ownership under /pool/apps/<service>/...

Per service (repo: <service>/quadlet/*.container, *.pod, *.volume)
  Podman Quadlet unit files, owned and read by that service's dedicated
  user, running under that user's `systemd --user` instance.
  ~svc-<name>/.config/containers/systemd/ is a directory-level symlink
  straight into <service>/quadlet/ in the repo checkout — the repo folder
  IS the live unit source, no copy step. Each service's container binds
  its app port to 127.0.0.1 only (loopback), not the LAN interface.

Traefik (repo: traefik/quadlet/traefik.container, host network mode)
  Migrated to Podman/Quadlet like everything else, but running with
  Network=host so it can bind 80/443 directly and reach every backend's
  loopback port. Two discovery mechanisms run side by side:
    - providers.docker, endpoint=docker.sock (read-only), unchanged —
      keeps discovering the still-on-Docker fleet via labels exactly as
      today, no changes required to those services.
    - providers.file, directory=traefik/dynamic.d/ (a real repo directory,
      bind-mounted directly into the Traefik container — not reached via
      a symlink) — one small static router/service snippet per migrated
      service, each pointing at that service's 127.0.0.1:<port>.
      Per-service snippets are authored at <service>/traefik/dynamic.yml
      (so the service folder stays the source of truth) and *copied*
      into traefik/dynamic.d/ by the reconciler on change — not
      symlinked. Symlinking doesn't work here the way it does for Quadlet
      units: those are resolved by host systemd, which never enters a
      container's mount namespace, but this directory IS bind-mounted
      into Traefik's container, and a symlink pointing outside that
      mount (at another service's folder elsewhere in the repo) dangles
      once resolved from inside it. Copying avoids both that and bind-
      mounting the whole repo (secrets included) into the one
      internet-facing container.
  See §7 for why label-based discovery can't be used for rootless
  per-service-user containers.

Reconciler (repo: small script + a systemd system-level timer)
  `git pull` in the repo checkout, then for each service whose unit
  files changed: `machinectl shell svc-<name>@ -- systemctl --user
  daemon-reload` and restart the affected unit(s).

Update pipeline
  `podman auto-update` (systemd --user timer, per service user),
  label-driven (`io.containers.autoupdate=registry`), with Podman's
  built-in rollback if the new image fails its healthcheck.
  For services that are deploy targets for your own apps: a small
  webhook receiver triggers an immediate `podman auto-update` for that
  one unit on registry push, instead of waiting for the poll interval.

Cockpit + cockpit-podman
  Web UI for visibility (containers, logs, resource/journal data across
  the box) and ad hoc "run this image" testing. Purely a viewer/actor on
  live state — not a source of truth, not part of the reconciliation path.

Storage-maintenance (existing snapraid pipeline, unchanged in structure)
  Plugin hooks (on-before-balance, on-after-scrub, etc.) updated to
  stop/start each service's Quadlet unit via that service's own systemd
  --user instance, instead of `docker stop/start`.
```

## 5. Per-service packaging

Each service folder (`jellyfin/`, `paperless/`, ...) contains everything needed to run it:

- `quadlet/*.container` (+ `.pod`/`.volume` where the service is a multi-container stack, e.g. Nextcloud, Paperless)
- `config/` — bind-mount sources, as today
- `setup.sh` — one-time per-service setup: creates the Podman secrets this service needs from local `*.secret` files (see §6), anything else specific to first-time bring-up
- `Makefile` — same target names as today (`start`, `stop`, `ps`, `logs`, `remove`, `clean`), each shelling out to that service's own `svc-<name>` systemd user instance instead of `docker compose`

Bringing up a service from scratch is: Ansible has already created its user (host-level, one-time, not per-service-folder); after that, `cd <service> && ./setup.sh && make start` is self-contained. The only thing outside the folder it depends on is Traefik running, if it wants to be externally reachable — a service that doesn't need Traefik doesn't even need that. Reachability is wired by the reconciler copying the service's own `traefik/dynamic.yml` into Traefik's watched directory (see §7) — the service still only authors its own file, not a copy of Traefik's config; the copy step is the reconciler's job, not something the service folder does itself.

## 6. Secrets

Compose's `secrets: file: ./x.secret` has no direct Quadlet equivalent. Each service's `.container` unit instead references a named Podman secret (`Secret=paperless_db_password`), created once via `podman secret create` run as that service's user. This creation step lives in the service's own `setup.sh`, reading the same `*.secret` files already checked into (gitignored paths of) the service folder today — no change to where secret material lives, only to how it's handed to the container.

## 7. Networking

**This is not a shared Podman network, and that's deliberate.** Per-service dedicated unprivileged users mean each service's rootless Podman runs in its own private user network namespace — two different users' rootless containers cannot reach each other by container IP, regardless of network backend (pasta or rootless netavark bridge). This is a structural property of rootless namespaces, not a config option ([podman/discussions#27911](https://github.com/containers/podman/discussions/27911), [podman/issues/20032](https://github.com/containers/podman/issues/20032)). A "shared `traefik_network` that everything joins," as Docker has today, is not achievable once services are isolated per-user — anything built on that assumption will silently fail to route.

The actual mechanism:

- Every service binds its app port to `127.0.0.1:<port>` only — loopback, never the LAN interface. Traefik is the only intended caller.
- Traefik runs with `Network=host` (see §4), giving it the same reachability the host machine has, including every service's loopback port and (per standard Linux bridge behavior) the still-on-Docker fleet's bridge-network container IPs.
- Traefik's **file provider** (`providers.file.directory`) replaces label-based discovery for migrated services: each service contributes one small static router/service definition (`<service>/traefik/dynamic.yml`, pointing at its own `127.0.0.1:<port>`), which the reconciler _copies_ (not symlinks — see §4) into Traefik's watched directory, so the service folder stays the authored source even though the live copy is a separate file.
- Rootless Podman drops the invoking user's supplementary host groups when entering the container's user namespace, so `svc-traefik` being in the `docker` group (§1) is not sufficient on its own for Traefik to read `docker.sock` — its Quadlet unit also needs `GroupAdd=keep-groups`.
- Traefik cannot express its own dashboard route via a label on its own Podman container: its Docker provider only ever sees containers known to the Docker daemon, and a Podman container never is, regardless of network mode. Traefik's dashboard router (and the wildcard-cert-triggering `certresolver`/`domains` config that rides on it) is instead a first-party file-provider snippet committed directly in `traefik/dynamic.d/` — no cross-service copy needed, since it's Traefik's own config.
- The still-on-Docker fleet is untouched: Traefik keeps `docker.sock` mounted read-only and keeps discovering them via labels exactly as today. Docker and file providers run in Traefik concurrently — this is normal, supported behavior, not a hack.
- Net effect: migrating Traefik itself carries less risk than it first appears to, since it changes nothing about how already-running Docker services are reached. It also drops Traefik's live `docker.sock` dependency for anything migrated going forward — a real reduction in what Traefik needs privileged access to.

## 8. Reconciliation

Because each service user's Quadlet directory is a live symlink into the repo, "reconcile" is intentionally thin:

1. `git pull`
2. For each unit file that changed (by path, diffed against the previous commit): `machinectl shell svc-<name>@ -- systemctl --user daemon-reload`, then restart the specific changed unit(s).

This runs on a systemd timer (system-level, since it needs to act across multiple per-service users). It is deliberately not a generic diff/apply framework — the symlink already makes the repo the running config; the reconciler's only job is telling systemd to notice and restart what changed.

## 9. Update pipeline

- Default: `podman auto-update`, one systemd --user timer per service user, checking images labeled `io.containers.autoupdate=registry`. Podman rolls back automatically if the new image fails its healthcheck — this replaces Watchtower's unconditional swap with a health-gated one.
- For services that are deploy targets for your own apps (built image pushed to a registry, no in-platform build): a small webhook receiver (triggered by the registry on push) runs `podman auto-update` for that specific unit immediately, rather than waiting for the poll interval. This is the only piece of custom "deploy pipeline" code in the design — everything else is Podman/systemd built-ins.

## 10. Alternatives considered and rejected

- **Docker + Komodo** — verified during design: Komodo's engine support is Docker-native; Podman works only via an undocumented `podman → docker` CLI alias, with a reported need to disable a security label (`security_opt: label=disable`) to fix rootless socket permissions. Maintainer confirmed this is community-tinkered, not officially supported. Rejected because it undermines the rootless-security rationale for moving to Podman at all.
- **Nomad (single-node), any engine** — has a real built-in reconciler (`nomad job run`) and health-gated canary/auto-revert updates, which is genuinely stronger than what this design hand-rolls. Rejected once per-service unprivileged-user isolation became a requirement: Nomad's podman driver talks to one podman endpoint per client node, so tasks share one rootless namespace — it has no first-class way to run a task as a fully separate host user with its own rootless Podman instance, which is exactly the isolation property wanted here.
- **Self-hosted PaaS (Coolify/CapRover/Dokploy)** — best UI polish, but source of truth lives mostly in the tool's own database with git/compose as one input among several; also tends to impose its own volume conventions, working against explicit per-service storage placement. Rejected as the weakest fit for "repo is truth, AI can edit it."
- **YunoHost** — an opinionated all-in-one self-hosting distro with its own app-catalog-managed state; not git/HCL/file driven, would fight the existing mergerfs/snapraid/Stow-based repo structure. Rejected.
- **Terraform for host provisioning** — Terraform's resource model fits cloud/API-provisioned infrastructure, not configuring an already-existing box (users, subuid/subgid, systemd units, packages); would require `local-exec`/community providers, fighting the tool's grain. Ansible is purpose-built for this and was chosen instead.

## 11. Known risks / explicit open items for the implementation plan

1. **Root-driven maintenance hooks vs. per-user systemd.** `storage-maintenance.sh` runs as root and today does `docker stop jellyfin` / `systemctl stop smb nmb`. Every hook that touches a Quadlet-managed service must instead reach into that service's own systemd --user instance (e.g. `machinectl shell svc-jellyfin@ -- systemctl --user stop jellyfin`). This is a real change to every existing plugin hook, not a rename, and needs to be implemented and tested per hook.
2. **Ofelia becomes redundant.** Its only current consumer is Nextcloud's 5-minute cron (`php -f cron.php`), which becomes a `systemd --user` timer running `podman exec` against the Nextcloud container for `svc-nextcloud`. Ofelia is retired rather than ported; flagged here in case there's a reason to keep it that hasn't come up.
3. **Multi-container stacks as `.pod` units.** Nextcloud (5 containers) and Paperless (5 containers) need to be modeled as Quadlet `.pod` + multiple `.container` units with correct `depends_on`-equivalent ordering (`Wants=`/`After=` between unit files). This is a bigger translation effort than the single-container services and should be scheduled later in the migration order.
4. **Symlinked Quadlet directory correctness.** Needs verification in practice that the per-user `podman-user-generator` follows a symlinked `~/.config/containers/systemd/` transparently (expected behavior for standard Linux path resolution, and consistent with how this repo already uses Stow-style symlinking elsewhere, but not yet confirmed against the actual generator on this host's Podman version).
5. **Loopback port assignment.** Each service picks its own `127.0.0.1:<port>` with nothing to prevent two services from colliding on the same one. Needs a simple convention (e.g. a port assigned per service, recorded where it's easy to check before adding a new one) before more than a couple of services are migrated — not a problem yet at pilot scale (one service), but will be by the time the full fleet moves.

## 12. Migration order

**Pilot phase (this round):** prove the whole pattern end to end on the smallest possible slice before touching the rest of the fleet.

1. Ansible host bootstrap, generalized (not hardcoded to one service): per-service user + subuid/subgid + lingering, base packages (podman, cockpit, cockpit-podman). Applied for two users this round: `svc-traefik`, `svc-stirling`.
2. Traefik migrated to Podman/Quadlet (host network mode, dual docker+file providers per §7). Existing Docker fleet stays up and unaffected throughout — verify every existing route (Jellyfin, Paperless, Nextcloud, Yuzukam, Traefik dashboard) still resolves correctly after the cutover, before relying on the new Traefik. Keep the old `traefik/docker-compose.yml` path intact as a fast rollback (`docker compose up -d` in `traefik/`) until this is confirmed solid.
3. Stirling-PDF added as a new service, fully on the new pattern: dedicated user, Quadlet unit, loopback port, `traefik/dynamic.yml` snippet, `podman auto-update` timer.
4. Capture what was learned (what took longer than expected, what the Quadlet/Ansible role needs generalized further) before scoping the next phase.

**Later phases (not detailed in this plan, scoped separately once the pilot lands):**

- Storage-maintenance plugin hooks updated in lockstep with whichever service they touch (Jellyfin's hook moves when Jellyfin moves).
- Remaining single-container services: Jellyfin, Yuzukam. Watchtower retired once nothing depends on it. Ofelia retired per §11.
- Multi-container stacks last: Nextcloud, Paperless (`.pod` modeling, §11).
- `manage.sh` deleted once nothing references it (confirmed already unused).

## 13. Operational UX continuity

`cd <service> && make start/stop/ps/logs` keeps working with the same target names throughout — only the implementation behind each target changes (from `docker compose ...` to per-user `systemctl --user ...` calls). `manage.sh` is retired in favor of these per-service Makefiles, since there's no single compose command left to generalize across services once each runs under its own user.
