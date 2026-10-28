---
name: upgrading-services
description: Use when upgrading, bumping or checking the versions of this host's container images - a single service's tag, a sweep across all of them, or a question about whether a pinned version is behind. Also use when a converge crash-loops, a pod is killed mid-migration, or an image refuses to start after a version change.
---

# Upgrading this host's services

## Overview

Every image on this host is either **floating** — a bare tag plus `AutoUpdate=registry`, which
Podman's own timer moves nightly — or **pinned**, which only moves as a line in git. Only the
pinned ones are an upgrade.

The work is never the tag. It is the two questions around it: **does the image perform its own
migration, and will the health check still be waiting when it finishes?** A bump that gets either
wrong does not fail cleanly — it crash-loops every five minutes, because the deploy timer retries
a failed converge on the next tick.

**Core principle: the goal is that the converge performs the whole upgrade.** If a step needs an
operator, it belongs in the unit (an env flag, a longer start period) or in `hooks.after_change` —
not in a runbook.

## When to use

- Bumping one service, or sweeping all of them.
- "Is X behind?" — the scripts here answer it in one command.
- A pod crash-loops or is killed after a version change (look at the start period first).
- An image refuses to start complaining about a config value or an old data directory.

Not for: adding a new service (that is Cradle's `adding-a-service` skill), or the host's pacman
packages (the operator's job; no role upgrades a host).

## The loop

1. **Find the gap.** `./tags.sh <image>` per pinned image. Candidate versions come from the
   **registry**, never from a GitHub release — a release with no pushed image is not an upgrade.
2. **Read the changelog across the whole gap**, not just the newest entry. What you are looking
   for, in order: a required new setting, a renamed setting, a changed _default_, a data-format
   change, and a minimum-source-version rule.
3. **Check the image, not the repo's Dockerfile.** `./imgcfg.py <image:tag>` prints User,
   Entrypoint, Cmd, ExposedPorts. A changed uid breaks a `volumes.owner`; a changed entrypoint
   breaks a unit that wraps it. When a health probe depends on a binary being present, run the
   image once and look (`podman run --rm --entrypoint="" <img> sh -c 'command -v bash'`).
4. **Decide where the migration happens.** Read the image's own init scripts when it has them —
   that is how you learn a hook is unnecessary. Anything the image will not do goes in the unit.
5. **Give the migration room.** Any blocking migration needs `HealthStartPeriod` longer than it
   takes, because `HealthOnFailure=kill` otherwise restarts the migration from the top, forever.
   A long start period is free: it only bounds the _first_ healthy report.
6. **Pre-flight.** `mise run fmt-check` here, then in `/home/jlk/Projects/cradle`:
   `mise run render /home/jlk/Projects/StorageBaby hosts/StorageBaby <outdir>`. Grep the rendered
   output for the values you just changed — rendering green is not the same as rendering _right_.
   No test in this repository checks a placement, so this is the only gate before the real host.
7. **Commit per decision, one PR**, then **update this skill** (see the last section).

## Quick reference

```bash
./tags.sh ghcr.io/mealie-recipes/mealie '^v3\.'                                 # all tags, paginated, version-sorted
./tags.sh docker.io/nextcloud '^3[0-9]-fpm-alpine$' fpm-alpine                  # 3rd arg = Docker Hub name filter
./imgcfg.py docker.io/nextcloud:33-fpm-alpine docker.io/nextcloud:34-fpm-alpine # diff two images
```

`tags.sh` handles ghcr.io, Docker Hub and any anonymous v2 registry. Docker Hub repos with
thousands of tags **need** the third argument, or pagination truncates the answer and a newer
version looks like it does not exist. There is no `skopeo` on this workstation and installing one
is not on the table.

Compare two tags' digests to find out whether they are the same image:

```bash
T=$(curl -s 'https://auth.docker.io/token?service=registry.docker.io&scope=repository:apache/tika:pull' \
	| sed 's/.*"token":"\([^"]*\)".*/\1/')
curl -s -H "Accept: application/vnd.oci.image.index.v1+json" -H "Authorization: Bearer $T" \
	-o /dev/null -w '%header{docker-content-digest}\n' \
	https://registry-1.docker.io/v2/apache/tika/manifests/latest
```

## Per-service notes

Columns: where the version lives, what to read, and what bit us.

| Service                                                                                   | Pinned in                                                                     | Source to read                                                                                              | Migration                                                                                                                                                                | Trap                                                                                                                                                                                                                        |
| ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **paperless**                                                                             | `quadlet/paperless-app.container.j2`                                          | `raw.githubusercontent.com/paperless-ngx/paperless-ngx/main/docs/migration-v3.md` + `docs/configuration.md` | Self. `docker/rootfs/etc/s6-overlay/s6-rc.d/init-{migrations,search-index,env-file}/run` run `migrate`, `document_index reindex --if-needed` and the `*_FILE` convention | **v3 can only be entered from exactly 2.20.15.** Migration `0016_sha256_checksums` is a blocking `RunPython` over every file — needs `HealthStartPeriod=1800s`. v3 made `DBENGINE` required and renamed `CONSUMER_POLLING`  |
| **nextcloud**                                                                             | `service.yml` → `config.version`                                              | `nextcloud/docker` `docker-entrypoint.sh`; `help.nextcloud.com`                                             | Self — the entrypoint runs `occ upgrade`; the five `occ` hooks follow                                                                                                    | **One major per converge**, enforced at entrypoint line ~192. Never skip. PHP 8.5 opcache breakage (`nextcloud/docker` #2595) only hits installs persisting the whole `php/conf.d` — we bind one file, so it does not apply |
| **meilisearch** (librechat + openarchiver)                                                | both `*-search`/`*-meilisearch` units                                         | `meilisearch.com/docs/learn/update_and_migration/updating`                                                  | Self, **because of** `MEILI_UPGRADE_DB=true` — idempotent, stays in the unit forever                                                                                     | Without that flag a bump is a crash-loop: it will not open an older index. The in-place upgrade is **not atomic**, hence the 600s start period. Cannot read an index written before v1.12                                   |
| **tika** (paperless + openarchiver)                                                       | both `*-tika` units                                                           | OpenArchiver's compose is the de-facto pin                                                                  | Stateless                                                                                                                                                                | `apache/tika:latest` **is** `4.1.0` (same digest) — `latest` vs `-full` is a _variant_ split, not a version one. `-full` is a superset. The `/dev/tcp` probe needs `bash`, which `-full` has; `/bin/sh` is dash             |
| **immich**                                                                                | `service.yml` → `config.version`                                              | `github.com/immich-app/immich/releases`                                                                     | Self, on server start                                                                                                                                                    | Server and ML share the one value. The database is a separate, digest-pinned VectorChord image — see standing decisions                                                                                                     |
| **openarchiver**                                                                          | `quadlet/openarchiver-app.container.j2`                                       | `github.com/LogicLabs-OU/OpenArchiver` (note the org name) + its `docker-compose.yml`                       | Self                                                                                                                                                                     | Its compose is the reference for the sidecars' versions. Needs meilisearch ≥ 1.14                                                                                                                                           |
| **mailflow**                                                                              | backend **and** frontend units                                                | `github.com/maathimself/mailflow/releases`                                                                  | Self, on backend start                                                                                                                                                   | The two must move together. Downgrading past 4.1.0 needs the scheduled-message tables dropped by hand                                                                                                                       |
| **lobehub**                                                                               | app, gateway, files, search units                                             | per-component releases                                                                                      | Self                                                                                                                                                                     | rustfs is the object store — check for an on-disk format change before moving it. Gateway states a minimum LobeHub version                                                                                                  |
| **openproject**                                                                           | web **and** worker units                                                      | `github.com/opf/openproject/releases`                                                                       | Self — the unit sets `MIGRATE=true`                                                                                                                                      | Both units share the tag                                                                                                                                                                                                    |
| **mealie**                                                                                | `quadlet/mealie-app.container.j2`                                             | `github.com/mealie-recipes/mealie/releases`                                                                 | Self (alembic on start)                                                                                                                                                  | —                                                                                                                                                                                                                           |
| **librechat**                                                                             | app, database, rag, search, vectordb units                                    | `github.com/danny-avila/LibreChat/releases`                                                                 | Self                                                                                                                                                                     | Mongo stays on the **8.0** line; 8.1+ is a major                                                                                                                                                                            |
| **mcp / mcp-mayu**                                                                        | `mcp/config/build/Containerfile` **and** `mcp-mayu-hub` unit                  | per-component releases                                                                                      | Stateless                                                                                                                                                                | The hub version is pinned **twice** — mcp builds from it as a base. paperless-mcp must stay ≥ API v9 for paperless 3.x; its default is `9`                                                                                  |
| **calino**                                                                                | `config/build/Containerfile` (`ADD ...tags/vX.tar.gz`) **and** the proxy unit | `github.com/Ivan-Malinovski/calino` releases + its `Dockerfile`                                             | Rebuild                                                                                                                                                                  | App and proxy are **one project at one version** — lockstep. The Containerfile's contract is to mirror upstream's build, so read upstream's `Dockerfile` at the new tag and follow it, including its node base              |
| **cliproxy**                                                                              | `config/build/Containerfile`                                                  | `github.com/router-for-me/CLIProxyAPI/releases`                                                             | Rebuild                                                                                                                                                                  | Upstream ships several releases a day, so the gap is always wide and always shallow. Its live config is written by its own panel — check for a `config.yaml` schema change specifically                                     |
| **jellyfin, stirling-pdf, yuzukam, collabora, gotenberg, redis, postgres, nginx, dm-mcp** | —                                                                             | —                                                                                                           | —                                                                                                                                                                        | **Floating + `AutoUpdate=registry`. Nothing to do.**                                                                                                                                                                        |
| **renaissance**                                                                           | `source.yml` → `ref: stable`                                                  | its own repository                                                                                          | —                                                                                                                                                                        | Another repo's CI owns it                                                                                                                                                                                                   |

## Standing decisions — do not quietly reverse these

- **No postgres major moves.** immich's `14-vectorchord`, librechat's `pgvector:*-pg15`,
  lobehub's `paradedb:*-pg17` and the six plain `postgres:17-alpine`. postgres will not start on a
  data directory from an older major, so each needs a dump, a fresh volume and a restore with the
  service down. Report it; never put it in a converge. (2026-10-28, JLK: "report only".)
- **openarchiver's valkey stays `8-alpine`** while upstream's compose pins 8. The bar for a major
  is _upstream calling it safe_, not merely that a newer tag exists.
- **A sidecar image is the host's, not upstream's.** `postgres:17-alpine` and `redis:alpine` are
  shared by every service that can use one. Upstream naming a different major is not a reason to
  diverge — only a real technical need is, and the folder must say what it is.
- **calino's node base follows upstream**, not the newest alpine.

## Host facts worth not re-measuring

- `/pool/apps/paperless/media` ≈ 2.3 G across 4256 files, on the pool's spinning disks — that is
  the number behind paperless's 1800s start period.
- CPU is an **AMD Ryzen 5 PRO 4650G** and reports `sse4_2`, so paperless v3's NumPy `x86-64-v2`
  floor does not strand the document classifier.
- SSH for read-only checks: `root@192.168.178.2`.
- Secrets are **off limits**. If an upgrade needs a new or changed secret, stop and hand it to
  JLK — do not decrypt, hash, or edit any `*.sops.yaml`. A secret requirement blocking a PR is an
  acceptable outcome.

## Common mistakes

| Mistake                                                | What happens                                                                             |
| ------------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| Taking candidate versions from GitHub releases         | A release with no pushed image looks like an upgrade and the converge cannot pull it     |
| Omitting `tags.sh`'s Docker Hub name filter            | Pagination truncates; a newer version looks nonexistent                                  |
| Bumping a pinned tag without checking the start period | `HealthOnFailure=kill` restarts a blocking migration from the top on a five-minute loop  |
| Reading the repo's Dockerfile instead of the image     | The pinned tag is what runs; the repo's HEAD is not it                                   |
| Moving one half of a lockstep pair                     | calino app/proxy, mailflow backend/frontend, openproject web/worker, immich server/ml    |
| Treating a changed _default_ as a non-change           | paperless v3 flipped archive generation and duplicate handling without renaming anything |
| Assuming "converge succeeded" means the upgrade worked | It means the units started. Grep the rendered output and check the app                   |

## Keep this skill current

**This is part of the upgrade, not an optional extra.** At the end of every run, before handing
back:

1. **Add** what you learned per service — a new trap, a corrected source URL, a confirmed
   migration mechanism.
2. **Delete** what no longer holds. A version-specific note stops being useful once it is behind
   us: paperless's "only from 2.20.15" rule matters until v3 is in, then it is history. A trap that
   turned out not to apply should say so in one line, or go.
3. **Record a decision JLK made** under standing decisions, with the date and his words, so the
   next run does not re-ask it.
4. Re-check the per-service table against the repo — a service that moved folders, or an image that
   stopped being pinned, makes a row wrong rather than merely stale.

Stale guidance here is worse than none: it gets trusted.
