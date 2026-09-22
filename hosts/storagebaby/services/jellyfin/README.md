# Jellyfin

The media server, reachable at `jellyfin.<host domain>`. Image:
`lscr.io/linuxserver/jellyfin:latest`, updated by `AutoUpdate=registry` and
`svc-jellyfin`'s `podman-auto-update.timer` — a new image that fails its health
check is rolled back automatically.

`JELLYFIN_PublishedServerUrl` is `jellyfin.janlucaklees.de`: the URL Jellyfin
hands to clients that discover the server, so it is the public name and not the
internal one. It comes from `config.published_url` in `service.yml`, so a host can
override it through `service_config.jellyfin` in its `host.yml`.

## How it runs

Rootless Podman + Quadlet as `svc-jellyfin`, converged by the `service` role from
this folder. The single `pool`-class volume `config` holds the library database,
metadata and transcode settings.

There is deliberately **no `UserNS=keep-id`**. The linuxserver image is an
s6-overlay image: it starts as container-root and drops to its own `abc` user
(`PUID`/`PGID`) through `s6-applyuidgid`, which calls `setgroups(2)`
unconditionally. Under `UserNS=keep-id:uid=1000,gid=1000` the container's first
process is an unprivileged, capability-less uid, so that call fails and the image
never gets past init:

```
s6-applyuidgid: fatal: unable to set supplementary group list: Operation not permitted
```

— once per second, forever. With the default rootless mapping the container's
root is `svc-jellyfin` on the host, `s6-applyuidgid` has `CAP_SETGID` inside the
user namespace, and the image starts normally. The cost is that `/config` ends up
owned by a **subuid** of `svc-jellyfin` (`PUID=1000` inside → `first_subuid+999`
outside) rather than by `svc-jellyfin` itself. That is the same shape as
stirling-pdf and what `test_volume_dirs_belong_to_the_service` already allows.

## GPU passthrough

`devices: [/dev/dri]` renders `AddDevice=/dev/dri` **only** on a host whose
`host.yml` says `gpu: true`. storagebaby does; the test VM does not, so the same
service folder runs there with no device line at all. The Mesa/Vulkan userspace
this needs (`mesa`, `vulkan-mesa-layers`, `vulkan-radeon`, `vulkan-tools`,
`vdpauinfo`, `libva-utils`) is in storagebaby's `packages`, installed by the
`host_base` role — it used to be `jellyfin/install.sh`.

`groups: [render, video]` puts `svc-jellyfin` in the two groups that own the
`/dev/dri` nodes. Read the next section before assuming that is sufficient: the
app process loses those groups too, so hardware transcoding on storagebaby needs
the device nodes to be reachable without them (a udev rule giving `/dev/dri/*`
mode `0666`, or `PGID` set to the `render` gid). **Unverified** — the test VM has
no GPU, so nothing in this repo proves it either way yet.

## The media bind, and why group access does not survive

```yaml
binds:
  media: { host: /pool/shared/media, container: /media, mode: ro, group: media }
```

Read-only, because Jellyfin never writes to the library. The role guarantees the
`media` group exists, that `svc-jellyfin` is in it, and it creates
`/pool/shared/media` as `root:media 2775` **if it does not exist**. An existing
tree is never re-permissioned — its permissions belong to the operator.

`GroupAdd=keep-groups` carries `svc-jellyfin`'s host supplementary groups into the
container. It reaches the container's own credentials, and it **does not reach the
Jellyfin process**. Measured on the test VM, with `/pool/shared/media` group
`media` (gid 966) and the service user a member:

```
# the container's credentials -- keep-groups worked
$ podman exec jellyfin id
uid=0(root) gid=0(root) groups=0(root),65534(nogroup),65534(nogroup),65534(nogroup)
$ podman exec jellyfin cat /media/.storagebaby-probe    # root:media 0640
ok

# the actual app process, after s6 dropped to `abc` -- the groups are gone
$ podman top jellyfin huser hpid user args
166536  13001  abc  /usr/bin/jellyfin --ffmpeg=/usr/lib/jellyfin-ffmpeg/ffmpeg
$ grep Groups /proc/13001/status
Groups: 165636 166536
```

The three `nogroup` entries are `render`, `video` and `media`: kept, but unmapped
in the user namespace, which is exactly what `keep-groups` does. `s6-applyuidgid`
then calls `setgroups()` with `abc`'s _in-container_ group list, and a
`setgroups()` inside a user namespace can only set gids that namespace maps — so
the unmapped host groups are dropped and never come back. Host gid 966 is absent
from the surviving list, the probe file is `root:media 0640`, and the process is
neither its owner nor in its group. It cannot read it.

**So group membership is not the access mechanism here — the other bits are.** The
one-time operator command on storagebaby, not run by the role:

```bash
doas chgrp -R media /pool/shared/media
doas chmod -R o+rX /pool/shared/media
```

`chgrp` still matters: the group is what Samba and the rest of the host use, and
what the role would set on a fresh tree. `o+rX` is what Jellyfin actually reads
through. The tree is media files on a single-tenant NAS, so world-readable costs
nothing here; do not copy the pattern to a bind holding anything private.

Note that `test_binds.py::test_container_can_read_group_file` probes through
`podman exec`, which builds its credentials from the container config and
therefore still sees the kept groups. It passes for jellyfin, and it is **not**
evidence that Jellyfin itself can read the tree. Its docstring says so.

## Health check

`curl -sf http://127.0.0.1:8096/health` — Jellyfin's own health endpoint, and the
image ships curl. `HealthStartPeriod=90s` because a first start runs database
migrations before anything answers on 8096, and `HealthOnFailure=kill` would
otherwise kill a container that is merely still starting. Observed first start on
the test VM: healthy inside a minute.

Over Traefik, `GET /` answers **302** (the redirect to the web UI), which is a
correct answer and not an outage.

## Networking

The container binds `127.0.0.1:8096` only. Reachability comes entirely from
Traefik, whose route file the `service` role renders from `domain` and `port` in
`service.yml`; Traefik picks it up through its file provider, not Docker labels.

## Changing something

Edit `service.yml` or `quadlet/jellyfin.container.j2` and let the host's deploy
timer converge — the role re-renders the unit and restarts it, and a changed
`domain`/`port` rewrites the Traefik route with no restart at all.
