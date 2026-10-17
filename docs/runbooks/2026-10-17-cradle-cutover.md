# Cutover runbook: StorageBaby adopts Cradle

This is a record of what happened, not a procedure — every step below is checked off
because it already ran. All timestamps are host/workstation local time (CEST),
2026-10-17.

The change in `docs/superpowers/specs/2026-10-16-cradle-split-design.md` is inert until
an operator runs `bootstrap.sh` on the host and lets it converge. §9 of that spec is the
plan this runbook executed.

## Preconditions (15:00:32 – 15:02:35)

```
$ ssh root@192.168.178.2 'cat /etc/storagebaby/deploy.conf'
REPO_URL=git@github.com:janlucaklees/StorageBaby.git
BRANCH=stable
EXTRA_ARGS=
```

- `systemctl is-active storagebaby-deploy.service` → `inactive` (no tick mid-run).
- `systemctl is-enabled storagebaby-deploy.timer` → `enabled` (still the old engine's).
- `/var/lib/storagebaby` before adoption: `deploy.state`, `fast`, `repo`, `sources`.
- Cradle `stable` on the workstation: `96f44a5951a28aca8f7ef41377098af1ae5187f6`;
  `bootstrap.sh` extracted from it (204 lines) — the private-repo fallback, since `curl`
  of the raw file is not possible on a private repository.
- The hourly kopia replica push (`kopia-sync.service`) was `activating` at 15:00:32 and
  finished within seconds; its next run was not due until 16:00:16, ample room for a
  5–10 minute converge, and `kopia-sync.service`'s `ExecStart` carries no host path (it
  `podman exec`s into the already-running container), so the push was never a hard
  deadline either side of the converge.
- `pacman -S --needed --print git ansible sops age podman passt openssh` printed
  nothing — no package mutation was possible before the run.
- `gh auth status` → logged in as `janlucaklees`, scopes `gist, read:org, repo`.
- Host uptime: up 2 weeks, 18 hours, 43 minutes (relevant to the blocker below).

## The bootstrap run (15:02:35 – 15:03:12)

- [x] Old timer off, `cradle-bootstrap` installed:

  ```
  15:02:35 $ ssh root@192.168.178.2 'systemctl is-active storagebaby-deploy.service; systemctl disable --now storagebaby-deploy.timer; ...'
  inactive
  Removed '/etc/systemd/system/timers.target.wants/storagebaby-deploy.timer'.
  inactive
  disabled
  15:02:36 $ scp …/cradle-bootstrap root@192.168.178.2:/usr/local/sbin/cradle-bootstrap && chmod 0755
  -rwxr-xr-x 1 root root 8424 Oct 17 15:02 /usr/local/sbin/cradle-bootstrap
  ```

- [x] **First bootstrap run (15:02:45 – 15:02:46), exit 1 — expected, the private-clone
      failure:**

  ```
  adopting /etc/storagebaby as /etc/cradle
  adopting the deploy state under /var/lib/storagebaby
  warning: git-2.56.0-1 is up to date -- skipping          (… and ansible, sops, age, podman, passt, openssh)
   there is nothing to do
  /usr/local/sbin/cradle-bootstrap: line 155: hostname: command not found
  git@github.com: Permission denied (publickey).
  Cradle could not be cloned from git@github.com:janlucaklees/cradle.git. If it is private, add this as a
  read-only deploy key on it and run bootstrap again with the same arguments:
  ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKd+WtMPxZyspVXXRWer0Tj3FKSNa7vh1ganp78uX7R8 cradle@
  ```

  Arguments used (both runs, identical):
  `cradle-bootstrap --local --no-upgrade --cradle-repo git@github.com:janlucaklees/cradle.git --host-repo git@github.com:janlucaklees/StorageBaby.git --host-path hosts/StorageBaby`

  One cosmetic deviation: `hostname: command not found` (see "Follow-ups" below) — the
  generated key's comment is `cradle@` instead of `cradle@StorageBaby`. The key itself is
  valid; `set -e` did not trip, because the failure is a command substitution inside an
  argument.

- [x] **Deploy key registered (15:03:01):**

  ```
  $ gh repo deploy-key add …/cradle_key.pub -R janlucaklees/cradle -t storagebaby
  $ gh repo deploy-key list -R janlucaklees/cradle
  166760981  storagebaby  read-only  ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKd+WtMPxZyspVXXRWer0Tj3FKSNa7vh1ganp78uX7R8  2026-10-17T13:03:01Z
  ```

  Title `storagebaby`, read-only, id `166760981`.

- [x] **Second bootstrap run (15:03:09 – 15:03:12), exit 0:** the package line again
      `there is nothing to do`, then the three key/recipient blocks. The host-repo deploy
      key was unchanged (`…Gw6Gt6Z06x63BLy+jHcpsqbfKv9HwBgG4zWJbvGEX+v storagebaby-deploy@`,
      adopted, not regenerated — it did not need to be, only `cradle_key` needed
      registering); the age recipient was unchanged
      (`age142s7lfpzjdddrrxkee8gr057u9hqkanu220n4f6hvzmr9n66x4rs9w3zvs`), so no
      `.sops.yaml` work was needed.

## The adoption state (15:03:18)

```
/etc/cradle            drwx------ (0700)  — age.key/age.pub, cradle_key(.pub), deploy_key(.pub),
                       deploy.conf (0600, new), mail/<service>.secrets.sops.yaml, per-service config dirs
/var/lib/cradle        cradle/ (new clone), host/ (was …/storagebaby/repo), sources/ (moved)
/var/lib/storagebaby   fast                      ← only entry left
cradle checkout        96f44a5951a28aca8f7ef41377098af1ae5187f6  (branch stable)
/usr/local/sbin/cradle-deploy                     8592 bytes, 0755
/etc/systemd/system/cradle-deploy.{service,timer} installed
systemctl is-enabled cradle-deploy.timer          disabled      ← --local, as intended
```

`/etc/cradle/deploy.conf` was written by bootstrap (`CRADLE_REPO`/`CRADLE_REF`,
`HOST_REPO`/`HOST_REF`, `HOST_PATH`, `EXTRA_ARGS`); the old `REPO_URL`/`BRANCH` file was
removed by the adoption step. `/var/lib/storagebaby/deploy.state` was gone — deleted,
not moved.

All 16 service managers were checked read-only (`svc-calino, hermes, immich, jellyfin,
kopia, mailflow, mcp, mealie, nextcloud, openarchiver, openproject, paperless,
renaissance, stirling-pdf, traefik, yuzukam`) — every one `running`, zero failed units.
Nothing was restarted, stopped or started.

## The `--check` recap (15:03:32 – 15:09:14, 5 min 42 s)

```
PLAY RECAP
localhost : ok=1094 changed=44 unreachable=0 failed=0 skipped=798 rescued=0 ignored=0
```

Every changed task fell into one of these categories, all inside the brief's expected
set:

| Category                                                  | What                                                                                                                                                                                                                                                                       |
| --------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Migration 0001 — old units, helpers, udev rule, `/opt`    | `storagebaby-deploy.timer`, `storagebaby-deploy.service`, `/usr/local/sbin/storagebaby-{deploy,svc}`, `/etc/udev/rules.d/70-storagebaby-render.rules`, `/opt/storagebaby/maintenance`, `/opt/storagebaby`                                                                  |
| Migration 0001 — drop-in rename                           | `10-storagebaby-hosts.conf` removed for all 16 services (uids 933–949)                                                                                                                                                                                                     |
| storage role — maintenance tree under `/opt/cradle`       | `/opt/cradle/maintenance`, `…/plugins`, the six scripts, `maintenance.env`, `storage-maintenance.service`, `storage-maintenance-failed.service`, timer restart                                                                                                             |
| host_base                                                 | `cradle-svc` installed; `/etc/cradle` mode `0700 → 0711`; udev rule re-rendered as `70-cradle-render.rules`; "Deploy timer state" changed (the blocker, below)                                                                                                             |
| service role — quadlets whose config path moved           | `traefik.container`, `kopia.container`, `kopia-sync.service`, `calino-app.build`, `mcp-hub.build`, `mcp-hub.container`, `nextcloud-nginx.container`, `nextcloud-app.container`, `openproject-web.container`, `openproject-worker.container`, `renaissance-proxy.container` |
| service role — generated backup sidecars (same path move) | hermes, immich, mailflow, mcp, mealie, nextcloud, openarchiver, openproject, paperless                                                                                                                                                                                     |
| service role — host-gateway drop-ins re-rendered          | all 16 services, as `cradle-hosts.conf`                                                                                                                                                                                                                                    |

Every diff inspected was a path rename and nothing else, e.g.:

```
-Volume=/etc/storagebaby/traefik/dynamic.d:/etc/traefik/dynamic.d:ro
+Volume=/etc/cradle/traefik/dynamic.d:/etc/traefik/dynamic.d:ro

-Exec=/etc/storagebaby/hermes/kopia-client.sh
+Exec=/etc/cradle/hermes/kopia-client.sh
-Volume=/etc/storagebaby/hermes/kopia-client.sh:/etc/storagebaby/hermes/kopia-client.sh:ro
+Volume=/etc/cradle/hermes/kopia-client.sh:/etc/cradle/hermes/kopia-client.sh:ro
```

Data volume lines were untouched — `/var/lib/storagebaby/fast/...` stayed exactly as it
is (a declared storage root, not an engine path).

None of the forbidden change classes appeared: no pool remount (both remount-warning
tasks `skipping`, all four disk/parity mounts and the pool `ok`), no volume-directory
create, no `chown`, no `podman secret` change, no route-content change, `failed=0`,
`unreachable=0`, `rescued=0`.

## The blocker: the timer/converge race

`host_base`'s last task before the service role runs is:

```yaml
# cradle@96f44a5 ansible/roles/host_base/tasks/main.yml:241-245
- name: Deploy timer state
  ansible.builtin.systemd:
    name: cradle-deploy.timer
    enabled: '{{ deploy_timer }}'
    state: "{{ 'started' if deploy_timer else 'stopped' }}"
```

It reported **changed** in the `--check`, about a minute into the run — before the
service role touches the first service. `hosts/StorageBaby/host.yml` has
`deploy_timer: true`, and the timer was `disabled`/`inactive` because bootstrap ran
`--local`.

`cradle-deploy.timer` is `OnBootSec=2min` + `OnUnitActiveSec=5min`. The host had been up
two weeks and `cradle-deploy.service` had never been activated, so the monotonic trigger
was long in the past — systemd elapses such a timer immediately when it is started. A
hand-run converge that then enabled the timer as one of its own tasks would therefore
trigger `cradle-deploy.service` mid-run, which would find no `/var/lib/cradle/deploy.state`
(bootstrap deleted the old one; the manual converge would write the new one only at its
very end) and run a second `ansible-playbook` concurrently with the one still in
progress — two converges restarting the same pods under each other, on the live NAS.
Worse, the second tick's checkout refresh runs `git reset --hard` + `git clean -fdx` on
the exact tree the first run is executing roles and templates out of. No mutual
exclusion exists anywhere in Cradle (no `flock`, no `RefuseManualStart`).

CI never caught it because both test hosts declare `deploy_timer: false`, so the task's
branch is always `stopped` there; the hazard is specific to "manual converge on a host
whose `deploy_timer` is `true` while the timer is still disabled" — exactly this
cutover, and only this cutover.

**The ruling:** converge the way production does — enable the timer itself
(`systemctl enable --now cradle-deploy.timer`), whose immediately-elapsing tick runs the
single converge, so the playbook's own "Deploy timer state" task finds the timer already
enabled and active and is a no-op. systemd serialises starts of the same oneshot, so the
race cannot occur that way. No Cradle change, no flock — that became a follow-up
instead. A harness-side permission gate first denied a hand-run
`systemctl enable --now cradle-deploy.timer`; JLK ran it himself on the host at
**16:59:23**, which is what carried the converge below.

## The converge (16:59:23 – 17:10:42, 11 min 19 s)

The hazard did not materialise, and the ruling's reasoning was confirmed on the host —
the playbook's own timer task found the timer already enabled and active and reported
`ok`:

```
TASK [host_base : Deploy timer state]
Oct 17 17:00:09 … systemd[…]: Invoked with enabled=True name=cradle-deploy.timer state=started …
ok: [localhost]
```

```
Oct 17 16:59:23  Starting Cradle deploy …
Oct 17 16:59:23  cradle: git@github.com:janlucaklees/cradle.git (stable)
Oct 17 16:59:24  host repository: git@github.com:janlucaklees/StorageBaby.git (stable, hosts/StorageBaby)
Oct 17 16:59:25  external repository for renaissance: https://github.com/janlucaklees/renaissance (stable, deploy/storagebaby)
Oct 17 17:10:41  PLAY RECAP
Oct 17 17:10:41  localhost : ok=1156 changed=88 unreachable=0 failed=0 skipped=736 rescued=0 ignored=0
Oct 17 17:10:42  Finished Cradle deploy …
```

The Cradle checkout had moved on between the first bootstrap clone and this tick — CI
promoted `stable` from `96f44a5` to `49c4a19` in the interval, and the tick converged
the newer tip; the dispatch allowed either. The only `FAILED - RETRYING` lines were
`service : Wait for hook containers to be healthy` (6 retries across nextcloud and
openproject, out of 60 allowed), which then succeeded — `failed=0`.

Changed tasks, grouped:

| Category                             | What                                                                                                                                                                                                                                                                                                                                                     |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Migration 0001 — old engine removed  | `storagebaby-deploy.{timer,service}`, `/usr/local/sbin/storagebaby-{deploy,svc}`, `/etc/udev/rules.d/70-storagebaby-render.rules`, `/opt/storagebaby/maintenance`, `/opt/storagebaby`                                                                                                                                                                    |
| Migration 0001 — drop-in rename      | `10-storagebaby-hosts.conf` removed for all 16 services (uids 933–949)                                                                                                                                                                                                                                                                                   |
| host_base                            | `cradle-svc` installed; `/etc/cradle` `0700 → 0711`; `70-cradle-render.rules` rendered + udev rule applied; "Deploy timer state" → `ok` (no-op, as ruled)                                                                                                                                                                                                |
| storage                              | `/opt/cradle/maintenance` + `plugins`, the six scripts, `maintenance.env`, `storage-maintenance{,-failed}.service`, timer restarted                                                                                                                                                                                                                      |
| service — renders                    | the config-path quadlets (traefik, kopia, kopia-sync.service, calino build, mcp build + hub, nextcloud nginx/app, openproject web/worker, renaissance proxy), 9 generated backup sidecars, host-gateway drop-ins for all 16 as `cradle-hosts.conf`                                                                                                       |
| service — restarts (one per service) | `traefik`, `kopia`, `jellyfin`, `stirling-pdf`, `yuzukam`, `calino-app-build` + `calino-pod`, `mcp-hub-build` + `mcp-pod`, and the pods `hermes`, `immich`, `mailflow`, `mealie`, `nextcloud`, `openarchiver`, `openproject`, `paperless`, `renaissance`; 16 × "Reload the user manager"; `kopia-sync.timer` restarted; 10 "Start units that are not up" |
| service — after-change hooks         | nextcloud's five `occ` commands, `openproject-web: rake db:seed`, `paperless-app: touch /tmp/.hook-ran` (the known drift fixture)                                                                                                                                                                                                                        |

Forbidden classes again absent: no pool remount (both tasks `skipping`, all mounts
`ok`), no volume-directory create, no `chown`, no `podman secret` change, no
route-content change.

## Verification (17:11:39 – 17:11:54)

| Check                                           | Result                                                                                                                  |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `cradle-svc ps <service>`, all 16               | all "Active: active (running)", restarted once each between 17:00:32 (traefik) and 17:10:39 (renaissance)               |
| Failed units, every `svc-*` manager             | 0 in all 16                                                                                                             |
| `/etc/cradle`                                   | `drwx--x--x` (0711)                                                                                                     |
| `/opt/storagebaby`, `/etc/storagebaby`          | gone                                                                                                                    |
| `/usr/local/sbin/storagebaby-{svc,deploy}`      | gone                                                                                                                    |
| `/etc/udev/rules.d/70-storagebaby-render.rules` | gone                                                                                                                    |
| `storagebaby-deploy.timer`                      | not-found                                                                                                               |
| `/usr/local/sbin/cradle-svc`                    | present, 0755                                                                                                           |
| `/var/lib/storagebaby`                          | only `fast`                                                                                                             |
| `storage-maintenance.service` `ExecStart=`      | `/opt/cradle/maintenance/storage-maintenance-unattended.sh`                                                             |
| `/opt/cradle/maintenance`                       | `maintenance.env`, `plugins`, the six scripts                                                                           |
| `kopia-sync.service` `ExecStart=`               | `… podman exec kopia /bin/sh /etc/cradle/kopia/sync.sh`                                                                 |
| renaissance (external pointer)                  | app, database, frontend, proxy, pod — all active (running)                                                              |
| traefik `dynamic.d`                             | 19 route files + `paperless-tcp.yml`, all present under `/etc/cradle`                                                   |
| `https://nextcloud.klees.io/status.php`         | `200`                                                                                                                   |
| `https://jellyfin.home.klees.io/`               | `302` — its baseline (Jellyfin redirects `/` to `/web/`; measured before any converge too, so this is not a regression) |

`/var/lib/cradle/deploy.state` after this converge:

```
cradle 49c4a19feafc02954b8d350900fd3631f692c84c
host bf0a2065fbee34cc55e695353169e78c716a85cc
source:renaissance b0d54f4baee7d8e954b1da1c9f2875b5bb998883
```

The 17:00 kopia replica push survived the cutover: `kopia-sync.service` ran at 17:02:46
(inside the converge window, against the still-old container; kopia itself restarted at
17:03:32), left no failed unit behind, and its next run was scheduled for 18:04:32 from
the re-rendered unit at the new path.

## The no-op tick (17:10:42 – 17:10:45)

Not run by hand — the timer's own next tick, five minutes after the previous one, did
it:

```
Oct 17 17:10:42  Starting Cradle deploy …
Oct 17 17:10:42  cradle: git@github.com:janlucaklees/cradle.git (stable)
Oct 17 17:10:43  host repository: git@github.com:janlucaklees/StorageBaby.git (stable, hosts/StorageBaby)
Oct 17 17:10:45  external repository for renaissance: https://github.com/janlucaklees/renaissance (stable, deploy/storagebaby)
Oct 17 17:10:45  nothing changed since the last converge; not running the playbook
Oct 17 17:10:45  Finished Cradle deploy …
```

`cradle-deploy.timer`: enabled, LAST 17:10:42, NEXT 17:15:42. The host was
self-deploying under Cradle, and every unit on disk pointed at `/etc/cradle`, so the
`podman-auto-update` timers due at 00:00:40 that night would restart into valid paths.

## The merge and the parity tick

With the host converged under Cradle, the StorageBaby split (branch `cradle-split`,
commits `5fd4cea` + `c725f73` on top of master's `bf0a206`) was merged:

```
git push -u origin cradle-split
gh pr create --base master --head cradle-split \
  --title "This repository is a host repository; the engine is Cradle" \
  --body-file <scratchpad>/pr-body.md
gh pr merge 1 --merge --delete-branch=false
```

- PR: https://github.com/janlucaklees/StorageBaby/pull/1
- Merged (mergedAt `2026-10-17T15:15:36Z` UTC = 17:15:36 CEST): merge commit
  `954d909cd917020631bcac78e27c4803c181b564`.
- CI run (`static` + `promote` jobs, both green) fast-forwarded `stable` to the same
  commit, confirmed by `git fetch origin stable` and
  `gh api repos/janlucaklees/StorageBaby/branches/master --jq .commit.sha` both
  returning `954d909…` — `stable == master == the merge commit`.

A bounded poll of `/var/lib/cradle/deploy.state` on the host (every 30 s) caught the
`host` line changing to the merge SHA at **17:26:33**, about sixteen minutes after the
merge — well inside one `cradle-deploy.timer` cycle of the converge's own duration:

```
cradle 49c4a19feafc02954b8d350900fd3631f692c84c
host 954d909cd917020631bcac78e27c4803c181b564
source:renaissance b0d54f4baee7d8e954b1da1c9f2875b5bb998883
```

```
localhost                  : ok=1095 changed=0    unreachable=0    failed=0    skipped=797  rescued=0    ignored=0
```

`changed=0 failed=0` — exact render parity: the split merge changed nothing the host
runs. `systemctl --user -M svc-traefik@ show traefik.service -p ActiveEnterTimestamp`
read `Sat 2026-10-17 17:00:32 CEST` both before and after this tick — identical to the
pre-merge baseline, so traefik (and by extension every other service) was not
restarted.

## Follow-ups

All three Cradle-side ones were closed by the review's fix wave the same day.

- **The timer/converge race is hand-run against tick, not tick against tick.** Two ticks
  of the timer cannot overlap: `cradle-deploy.service` is a oneshot, so the start job the
  timer elapsed at 17:04:23 — in the middle of this 11 min 19 s converge — was merged into
  the running one and re-elapsed three seconds after it finished, at 17:10:42, which is
  exactly what the journal above shows. The real exposure is a hand-run `cradle-deploy`
  or `--check` pre-flight beside the timer's tick, and a hand converge on a host whose
  `deploy_timer: true` while the timer is still disabled — this cutover's own case, where
  the converge's "Deploy timer state" task would have started the timer mid-run. It did
  not bite here because the converge was carried by the timer's own tick. Closed in
  Cradle: `exec 9> /run/cradle-deploy.lock` + `flock -w 900 9` before the first fetch,
  waiting rather than failing, so a pre-flight blocks behind a tick and a tick fired mid
  hand-converge waits and then no-ops on the state file.
- **Bootstrap left `/etc/cradle` at `0700`** until the first converge set it to `0711`;
  between adoption and that converge the platform root is not traversable by the `svc-*`
  users, which made "restart nothing before the converge" load-bearing for a second reason
  it doesn't state. Closed: bootstrap creates it `0711`, held equal to the role's mode by
  a static test.
- **Bootstrap's `$(hostname)` fails on a host without `inetutils`** — cosmetic, the
  generated key's comment was `cradle@` instead of `cradle@StorageBaby`. Closed:
  `$(uname -n)`.
- **The converge-via-timer rule** stands as the operating rule — the first converge after
  bootstrap is the timer's own tick, never a hand-run `cradle-deploy` — and the lock is
  what now makes a breach of it safe rather than merely documented.
- The known drift `paperless: touch /tmp/.hook-ran` ran as an after-change hook on the
  live host, same as it does in CI — recorded here, not a new finding.
