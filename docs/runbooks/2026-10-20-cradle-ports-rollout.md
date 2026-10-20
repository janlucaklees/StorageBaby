# Rollout runbook: a service declares `container_port`, Cradle derives the loopback port

**Draft — written before the rollout, filled in during it.** Every box below is unticked
on purpose: this is the procedure, and it becomes the record when it is run. The stage 2
rollout (`2026-10-18-cradle-built-ins-rollout.md`) is the model.

What this rolls out is stage 3 of the Cradle split,
`docs/superpowers/specs/2026-10-19-cradle-ports-design.md` § 6. A service stops choosing
the loopback port Traefik reaches it on. It declares `container_port` — the port its own
process listens on inside the container, which is the one port only it knows — and Cradle
derives the host-side one from the service name and the route's domain:

```
publish_port = 10000 + (crc32("<service name>/<route domain>") mod 10000)
```

Deterministic, stateless, in no file, and a collision refused at converge naming both
sides. Port bookkeeping across services and across repositories — a per-host namespace
that nothing a host repository ran ever checked — disappears.

**This is a restart window, not a migration.** Nothing in this stage touches a secret, a
volume, a bind, the pool or the snapraid array. Every unit's publish line and every
Traefik route file changes by a port number; nothing else changes at all. Every service
restarts once, because its unit changed.

Timestamps below are host/workstation local time (CEST). The operator is present
throughout; nothing here is left to an unattended tick to discover.

## The eighteen derived ports

The numbers to expect, everywhere below. Computed from the function above; no file holds
them.

| route                              | derived port |
| ---------------------------------- | ------------ |
| `mealie/mealie.klees.io`           | 11478        |
| `calino/calino.klees.io`           | 11773        |
| `traefik/traefik`                  | 12195        |
| `openproject/openproject.klees.io` | 12726        |
| `paperless/paperless.klees.io`     | 13177        |
| `jellyfin/jellyfin`                | 13465        |
| `hermes/hermes`                    | 13500        |
| `yuzukam/yuzukam`                  | 13721        |
| `openarchiver/openarchiver`        | 13910        |
| `immich/immich.klees.io`           | 14550        |
| `nextcloud/collabora.klees.io`     | 15130        |
| `mcp/mcp`                          | 15171        |
| `renaissance/renaissance.klees.io` | 16100        |
| `mailflow/mailflow.klees.io`       | 16109        |
| `kopia/kopia`                      | 16915        |
| `stirling-pdf/stirling.klees.io`   | 17609        |
| `nextcloud/nextcloud.klees.io`     | 18643        |
| `calino/dav.klees.io`              | 19778        |

Eighteen routes, eighteen distinct ports, all inside 10000–19999, and none equal to 21,
2121 or any of 21100–21109 — paperless's FTP ports, which stay **declared** and stay
unchanged. No route label is renamed, so no DNS record and no certificate changes.

## Preconditions

- [ ] **The Cradle PR is open, green and not merged** — the PR from branch `ports` in
      `janlucaklees/cradle`. Confirm its CI with `gh pr checks` before anything else.
- [ ] **Cradle's `stable` is still at the pre-stage-3 SHA**:
      `f8eb03b7d3b068d5c2328b61b8dd2c9e876a3b80`. Confirm with
      `gh api repos/janlucaklees/cradle/branches/stable --jq .commit.sha` and **write the
      value down** — the rollback pins the branch from before stage 3, not this one.
- [ ] **`origin/pre-ports` exists in Cradle at that same SHA**:
      `git ls-remote https://github.com/janlucaklees/cradle pre-ports` must print
      `f8eb03b7d3b068d5c2328b61b8dd2c9e876a3b80`. It was pushed when this plan was written
      and exists for the rollback below alone. **It has to be a branch name, not a SHA**:
      `cradle-deploy`'s `sync_checkout` fetches
      `+refs/heads/<ref>:refs/remotes/origin/<ref>` and nothing else, so a commit id in
      `CRADLE_REF` makes every tick fail at the fetch.
- [ ] **This repository's PR is open, reviewed and not merged**, and this runbook is in it.
- [ ] **The renaissance diff below is reviewed**, and the two `gh api` commands in
      "The renaissance change" still print what this runbook says they printed. renaissance
      is the operator's repository; this plan does not touch it.
- [ ] **The time is outside 01:30–04:00.** `storage-maintenance.timer` fires at 02:00 and
      the seven `*-dump.timer`s at 02:30 (immich, mailflow, mealie, nextcloud,
      openarchiver, openproject, paperless); the window leaves both clear on either side.
- [ ] **The hourly replica push is not running.**
      `systemctl --user -M svc-kopia@ list-timers` — `kopia-sync.timer` fires at `:00`-ish
      every hour; start inside the first half of an hour so the converge does not meet it.
      Not a hard deadline, but a push interrupted mid-converge makes the verification
      ambiguous.
- [ ] **No Molecule run on the workstation**, and no second converge in flight:
      `systemctl is-active cradle-deploy.service` → `inactive`.
- [ ] **No data precondition at all.** Nothing in this stage touches a secret, a volume,
      a bind or the pool, so there is nothing to back up first and nothing to read out of
      a sops file before a merge. That is what makes this a restart window rather than a
      migration, and it is the reason the rollback is cheap.

## Stop the timer, first

- [ ] `systemctl stop cradle-deploy.timer`
- [ ] `systemctl is-active cradle-deploy.timer` → `inactive`

**Before any merge.** Three repositories break together here. The moment Cradle's `stable`
carries stage 3, a host whose folders still say `port:` is refused — the role and the
static contract both reject the key, which is the point of rejecting it rather than
ignoring it. A tick landing between the Cradle merge and this repository's merge therefore
fails, and keeps failing every five minutes, until the second merge lands. The reverse
order is no better: a `container_port` folder on the pre-stage-3 engine renders no publish
line at all.

## The three merges, in order, each waited out

- [ ] **Merge the Cradle PR** (branch `ports`). Then `gh run watch` on the master push,
      and confirm the promotion:
      `gh api repos/janlucaklees/cradle/branches/stable --jq .commit.sha` equals the merge
      commit.
- [ ] **Merge this repository's PR.** The same wait on this repository's own CI
      (`.github/workflows/ci.yml` checks formatting and fast-forwards `stable` on a green
      master push), and the same confirmation:
      `gh api repos/janlucaklees/StorageBaby/branches/stable --jq .commit.sha`.
- [ ] **Apply the renaissance diff** — § "The renaissance change" below, verbatim — in
      `janlucaklees/renaissance`, let its CI promote its `stable`, and confirm
      `gh api repos/janlucaklees/renaissance/branches/stable --jq .commit.sha` moved.

Only then the pre-flight. The host is fetching nothing in the meantime: the timer is
stopped.

## The pre-flight

- [ ] `doas cradle-deploy --check 2>&1 | tail -200`

Expected, and **only** this:

- every service's unit reported as changing, with the diff being its publish line's host
  port — `127.0.0.1:<old>:<container>` → `127.0.0.1:<derived>:<container>`, the second
  number unchanged in every case. Five services' `container_port` is not the old number,
  because the old one was the loopback side: stirling-pdf 8180→8080, openarchiver
  3001→3000, openproject 8081→8080, mailflow 3003→80, nextcloud's web route 8280→80;
- **traefik's two flag lines instead of a publish line**, because `Network=host` collapses
  the two sides: `--entrypoints.traefik.address=127.0.0.1:8080` → `:12195` and the health
  check's `http://127.0.0.1:8080/ping` → `:12195`. Traefik publishes nothing;
- **kopia's one publish line**: `PublishPort=127.0.0.1:51515:51515` →
  `127.0.0.1:16915:51515`;
- every `traefik-dynamic.d/<name>-<domain>.yml` reported as changing, with the diff being
  the `url:`'s port — `https://` for kopia's and collabora's, whose `scheme` is unchanged.
  `traefik-traefik.yml` is **not** among them: the dashboard's router is
  `service: api@internal` and carries no `url:` at all;
- `paperless-tcp.yml` **unchanged**, and paperless's own `2121` and `21100-21109` publish
  lines unchanged. `tcp_ports` stay declared;
- `renaissance` fetched at its new `stable`, its unit changing the same way.

**No secret sync, no volume creation, no chown, no remount, no `backup-clients` write, no
storage task changed.** Nothing in this stage writes anything but a unit and a route file.
The derived numbers to expect are the eighteen in the table above. Anything beyond that
list stops the rollout — read it before converging.

## The converge

- [ ] `doas systemctl start cradle-deploy.service`, blocking.

Order within the tick: traefik first (it is placed first), then kopia, then the host's own
services in folder order. Each restarts once, because its unit changed. Traefik's route
files are rewritten in place and it watches the directory, so the only outage per service
is its own restart. Expect the whole tick to take as long as fifteen container restarts.

## Verification

- [ ] `cradle-svc ps traefik` and `cradle-svc ps kopia`: active.
- [ ] Each of the fourteen — calino, hermes, immich, jellyfin, mailflow, mcp, mealie,
      nextcloud, openarchiver, openproject, paperless, renaissance, stirling-pdf,
      yuzukam: active.
- [ ] `ss -H -lntpe | grep 127.0.0.1` — one line per derived port of the table above, each
      with the `uid:` of its own `svc-<name>`, and **no line on the old ports**: 8085,
      8086, 8080, 8096, 8000, 8081, 8180, 8280, 9980, 9119, 9000, 3000, 3001, 3002, 3003,
      2283, 8300, 51515 as _host_-side ports. Several of those are still container-side
      and live inside a netns, which `ss` on the host does not show — the claim is about
      the host side only.
- [ ] Every route through Traefik, which is the claim that matters — the loop below.
- [ ] **The FTP path is unchanged and still has to be proved**: one
      `curl --ftp-pasv ftp://<lan address>/` as the scanner's user, which is the live
      equivalent of Cradle's `test_ftp`. The ports did not move; the pod they forward into
      restarted.
- [ ] **One hourly `kopia-sync.service` run**, and one sidecar's
      `kopia repository status`. The sidecars reach the server through Traefik, and
      Traefik's own port moved under them — this is the one indirect consequence of the
      stage worth measuring rather than assuming.

The route loop:

```bash
for n in jellyfin.home.klees.io mcp.home.klees.io hermes.home.klees.io \
	openarchiver.home.klees.io yuzukam.home.klees.io kopia.home.klees.io \
	traefik.home.klees.io immich.klees.io mealie.klees.io stirling.klees.io \
	openproject.klees.io mailflow.klees.io paperless.klees.io nextcloud.klees.io \
	collabora.klees.io calino.klees.io dav.klees.io renaissance.klees.io; do
	printf '%-32s %s\n' "$n" "$(curl -sk -o /dev/null -w '%{http_code}' -H "Host: $n" https://127.0.0.1/)"
done
```

Expected: no `404` and no `000`. A `404` is Traefik matching no router; `000` is nothing
answering. `dav.klees.io` and `mcp.home.klees.io` answer on their declared `probe_path`
rather than `/` — check those two with `/health` and `/livez`.

## Rollback

Three things go back together, for the same reason they went forward together: a
`container_port` folder on a pre-stage-3 engine renders no publish line at all, and a
`port:` folder on the stage-3 engine is refused.

- [ ] **Pin Cradle back** by hand-editing `/etc/cradle/deploy.conf` on the host:
      `CRADLE_REF=pre-ports` in place of `CRADLE_REF=stable`. `deploy.conf` is written
      once by bootstrap and read by every tick — no role rewrites it — so the pin survives
      converges and has to be taken out by hand (`CRADLE_REF=stable` again) when stage 3
      is attempted a second time. Check it with
      `grep CRADLE_REF /etc/cradle/deploy.conf` before and after.
- [ ] **Revert this repository's merge** (`git revert -m 1 <merge sha>`, push, let CI move
      `stable`).
- [ ] **Revert renaissance's change** the same way, and let its CI move its own `stable`.
- [ ] **Start the timer only after both `stable` branches have moved.**

Every service restarts a second time and every route file is rewritten back. Nothing else
is undone, because nothing else was done — no secret was replaced, no volume created, no
directory re-owned. That is the whole cost of this rollback, and it is why the window is
cheap to abandon at any point before the converge.

## Start the timer

- [ ] `systemctl start cradle-deploy.timer`
- [ ] `systemctl list-timers cradle-deploy.timer` shows a next elapse.

**This is the step that is forgotten**, and a host with a stopped timer looks healthy for
weeks.

## The renaissance change

renaissance is the operator's repository and this plan does not touch it. The diff below
was computed against its `stable` while this runbook was written. Confirm nothing moved in
between with the two commands that produced it:

```bash
gh api "repos/janlucaklees/renaissance/contents/deploy/storagebaby/service.yml?ref=stable" -H "Accept: application/vnd.github.raw" | sed -n '1,12p'
gh api "repos/janlucaklees/renaissance/contents/deploy/storagebaby/quadlet/renaissance.pod.j2?ref=stable" -H "Accept: application/vnd.github.raw" | sed -n '1,10p'
```

Two files, three lines, and nothing else in that folder: its README names only
container-side ports (nginx on 80, Postgres on the pod's loopback), which are unchanged,
and `config/nginx.conf` names no host port at all.

```diff
--- a/deploy/storagebaby/service.yml
+++ b/deploy/storagebaby/service.yml
@@
-# Free on the host's loopback when this was written; the host refuses a converge in
-# which two services claim one port.
-port: 8300
+# The port nginx listens on inside the pod. The loopback port Traefik reaches it on is
+# Cradle's and is derived from `renaissance/renaissance.klees.io` -- 16100 today, and in
+# no file. A collision with another service is refused at converge, naming both.
+container_port: 80
--- a/deploy/storagebaby/quadlet/renaissance.pod.j2
+++ b/deploy/storagebaby/quadlet/renaissance.pod.j2
@@
-{# Literal, as every pod on the platform writes it: the host's static check reads the
-   published ports out of the raw template. One port, nginx's, which splits the one
-   hostname between the API and the frontend -- the platform routes a hostname to a
-   port and knows no path rules. #}
-PublishPort=127.0.0.1:8300:80
+{# The loopback port is Cradle's, derived and in no file; `container_port` is this pod's
+   own. The static check reads this line out of the raw template and accepts a `publish_port`
+   expression for a route and a literal only for a `tcp_ports` target.
+
+   One port, nginx's, which splits the one hostname between the API and the frontend --
+   the platform routes a hostname to a port and knows no path rules. #}
+PublishPort=127.0.0.1:{{ publish_port }}:{{ container_port }}
```

`container_port: 80` and not `8300`: the pod's second number is nginx's own port, and
`8300` was only ever the loopback side — the number this stage exists to stop anybody
picking.
