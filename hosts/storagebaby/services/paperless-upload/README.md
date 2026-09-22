# paperless-upload

A ~100-line Bun script that watches the scans folder for PDFs and posts them to
Paperless-ngx. No web UI, no port, no Traefik route — it only ever talks outward.

It is the first service in this repo built **on the host** instead of pulled from
a registry: `quadlet/paperless-upload.build.j2` is a Quadlet `.build` unit, and the
container references the image it produces by unit name.

## How it runs

Rootless Podman + Quadlet as `svc-paperless-upload`, converged by the `service`
role from this folder. Two units:

| Template                        | systemd unit                     | What it does                                         |
| ------------------------------- | -------------------------------- | ---------------------------------------------------- |
| `paperless-upload.build.j2`     | `paperless-upload-build.service` | `podman build` → `localhost/paperless-upload:latest` |
| `paperless-upload.container.j2` | `paperless-upload.service`       | runs that image                                      |

`Image=paperless-upload.build` in the container unit is what ties them together:
Quadlet turns the reference into a `Requires=`/`After=` on the build unit, so the
image is always built before the container that needs it starts.

### The build context is the deployed config

```ini
[Build]
ImageTag=localhost/paperless-upload:latest
File={{ config_dir }}/build/Containerfile
SetWorkingDirectory=file
```

`config/` of this folder is deployed by the role to `/etc/storagebaby/paperless-upload/`,
owned by `svc-paperless-upload` and world-readable, so `config/build/` **is** the build
context on the host — `SetWorkingDirectory=file` points the build at the directory
holding the `Containerfile`, which is where `index.ts` lands next to it.

No `AutoUpdate=registry` here, deliberately: there is no registry to watch. The image
is only ever as new as this git folder.

### A change to the script really does reach the container

`index.ts` lives under `config/`, so editing it makes the role's config copy report
changed, which restarts **all** of the service's units in order — build first, then
the container. Restarting a `.build` re-runs the oneshot build, `COPY index.ts .` is
invalidated, and the restarted container picks the new image up by tag. Verified on
the test VM by changing one comment in `index.ts` and converging: the build's
`ExecMainStartTimestampMonotonic` moved (259843525 → 681641175), the image id changed
(`sha256:1e592e6d…` → `sha256:000186b7…`) and `paperless-upload.service` re-entered
active afterwards (258456900 → 683803629), with the new comment readable in
`/app/index.ts` inside the running container.

A change to a quadlet template alone takes the ordinary path (`quadlet_render`
changed → that unit restarted).

Note the build unit is a `Type=oneshot` **without** `RemainAfterExit` here, so a
successful build sits at `inactive`/`Result=success` and its
`ActiveEnterTimestampMonotonic` is permanently `0`. That is not a failure, and the
`service` role knows about it — see `ansible/roles/service/README.md`.

## The scans bind, and where files go

```yaml
binds:
  scans: { host: /pool/shared/scans, container: /data, mode: rw, group: scans }
```

**This is a layout change from the old `docker-compose.yaml`**, which mounted
`/pool/shared/scans` at `/data/in` — the whole tree was the inbox and processed files
went to a `processed/` directory the container created _inside_ it. Here the tree is
mounted one level up at `/data`, so:

- incoming scans: `/pool/shared/scans/in`
- uploaded, archived: `/pool/shared/scans/processed`

Whatever puts scans on the NAS (the scanner's Samba share) has to write into `in/`
from now on. Confirm that with the operator before the first real deploy on
storagebaby, and move anything already sitting in `/pool/shared/scans` into `in/`.

`rw`, because the uploader moves a file out of `in/` once Paperless has accepted it.
The role creates `/pool/shared/scans` as `root:scans 2775` **only if it does not
exist**; on storagebaby it already does, and its permissions stay the operator's.
`in/` itself is created by the script at startup (`mkdir -p`), `processed/` on the
first successful upload.

### It writes as the service user

```ini
UserNS=keep-id:uid=1000,gid=1000
GroupAdd=keep-groups
```

The image runs as its own `bun` user, uid 1000, and — unlike the linuxserver images
(see `../jellyfin/README.md`) — it never switches users at runtime, so nothing inside
ever calls `setgroups()`. That is what makes `keep-id` usable here where it is not for
jellyfin: container uid/gid 1000 is mapped straight onto `svc-paperless-upload` on the
host, and the `scans` group `keep-groups` carries in stays. Measured on the test VM
(`svc-paperless-upload` = uid 966, `scans` = gid 964):

```
$ podman exec paperless-upload id
uid=1000(bun) gid=1000(bun) groups=1000(bun),65534(nogroup)

# the uploader itself, on the host -- not a subuid, and it still has `scans`
$ podman top paperless-upload huser hpid user args
966  5505  bun  bun run index.ts
$ grep -e Uid: -e Gid: -e Groups: /proc/5505/status
Uid:	966	966	966	966
Gid:	965	965	965	965
Groups:	964 965
```

`65534(nogroup)` inside is `scans`: kept, but unmapped in the user namespace, which is
what `keep-groups` does — and `Groups: 964` on the host is the half that counts, since
that is what the kernel checks against the bind. This is the case jellyfin's README
describes as impossible for an s6 image, and here it really does hold for the
long-running process, not just for `podman exec`.

So files the uploader writes under the bind belong to `svc-paperless-upload`, not to a
subuid:

```
$ stat -c %U:%G:%a /pool/shared/scans/in /pool/shared/scans/in/<probe>
svc-paperless-upload:scans:2755
svc-paperless-upload:scans:644
```

which is what keeps `processed/` readable to Samba and the rest of the host.

## Configuration and the token

`config.paperless_url` in `service.yml` becomes `Environment=PAPERLESS_URL`, which
`index.ts` reads (its hard-coded fallback only ever applies to a hand-run container).
A host can override it through `service_config.paperless-upload` in its `host.yml`.

The API token is the one secret: `secrets: [paperless_token]` → `Secret=paperless_token`
→ `/run/secrets/paperless_token` inside the container, which is the path `index.ts`
reads. `secrets.sops.yaml` is encrypted to the operator key only; the test VM gets a
generated throwaway value from the Molecule `prepare` play.

> **The committed token is a placeholder.** The old `paperless-upload/paperless-token.secret`
> that this migration was supposed to carry over was a zero-byte file, so
> `secrets.sops.yaml` holds `REPLACE_ME_paperless_api_token`. Put the real token in with
> `make sops FILE=hosts/storagebaby/services/paperless-upload/secrets.sops.yaml` before
> the first real deploy.

**Uploads fail until Paperless itself exists** — that is Phase 3. Until then the
container is healthy and idle, and every PDF dropped in `in/` is retried on the next
arrival (`scan()` re-reads the whole directory each time), so nothing is lost.

## Health check

`HealthCmd=test -d /data/in`. `in/` exists only after `index.ts` has read the token
and run its startup `mkdir -p`, so the check answers the two questions that can
actually go wrong here: did the script get past startup, and is the bind mounted and
writable. A crash-looping container (missing token, unreadable bind) never creates it.
There is no endpoint to probe — the service listens on nothing.

`HealthOnFailure=kill` + `Restart=always`: a wedged uploader is restarted.

## Changing something

Edit `service.yml`, `config/build/index.ts` or a quadlet template and let the host's
deploy timer converge.
