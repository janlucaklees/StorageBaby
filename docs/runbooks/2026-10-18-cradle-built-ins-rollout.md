# Rollout runbook: Traefik and the kopia server become Cradle's built-ins

**Draft — written before the rollout, filled in during it.** Every box below is unticked
on purpose: this is the procedure, and it becomes the record once it has run. The stage 1
cutover (`2026-10-17-cradle-cutover.md`) is the model, and the only runbook in this folder
that is already a record rather than a plan.

What this rolls out is stage 2 of the Cradle split,
`docs/superpowers/specs/2026-10-17-cradle-built-ins-design.md` § 7. Traefik and the kopia
server stop being service folders this repository carries and become **built-ins** —
folders Cradle ships and places itself — so `hosts/shared/` and
`hosts/StorageBaby/services/kopia/` leave this repository and `host.yml` says in one
`backup:` block how the mechanism is used. The kopia client passwords stop being a pair of
secrets the operator keeps in step and become one generated value per backed-up service,
written by nobody.

Timestamps below are host/workstation local time (CEST). The operator is present
throughout; nothing here is left to an unattended tick to discover.

## Preconditions

- [x] **Cradle `stable` carries stage 2**, CI green. _Confirmed 2026-10-19 before the
      rollout: `stable` = `f8eb03b7d3b068d5c2328b61b8dd2c9e876a3b80` (the fix wave)._ Two SHAs are in play: stage 2 itself
      landed at `161e63d`, and `f8eb03b` is the fix wave after the final review (the
      refusal message for a missing built-in secrets file, the quickstart, two comments —
      no behaviour change this runbook depends on). `stable` is whichever of them CI last
      promoted. Confirm with `git -C /home/jlk/Projects/cradle fetch origin stable` and
      `gh api repos/janlucaklees/cradle/branches/stable --jq .commit.sha`, and **write it
      down** — the rollback below pins the branch from before stage 2, not either of these.
- [x] **`hosts/StorageBaby/secrets/backup.sops.yaml` exists and is committed**, with four
      keys: `repository_password`, `server_password`, `b2_key_id`, `b2_application_key` —
      the same four values `hosts/StorageBaby/services/kopia/secrets.sops.yaml` held.
      `repository_password` above all: **the repository on disk can only be opened with
      it**, kopia derives its encryption keys from it, and there is no reset and no
      escrow. A different value there does not start a new repository, it fails the
      connect. Read the old values with
      `mise run sops hosts/StorageBaby/services/kopia/secrets.sops.yaml` **before the PR
      is merged** — the file leaves the tree with the merge.
- [x] **`hosts/StorageBaby/secrets/ingress.sops.yaml` exists and is committed**, with
      three keys: `porkbun_api_key`, `porkbun_secret_api_key`, `dashboard_htpasswd` — the
      three from `hosts/shared/services/traefik/secrets.sops.yaml`, read the same way and
      before the same merge. `dashboard_htpasswd` is Traefik's own basic-auth file and the
      only thing standing in front of the dashboard; a wrong one locks the operator out of
      it and nothing else.
- [x] **The nine `client_*` values and the nine `kopia_password` values are not needed and
      are not carried over.** _Nothing to do: true by construction._ They are generated on the host from this converge on. The
      keys stay in the service `secrets.sops.yaml` files, ignored rather than refused, and
      come out the next time the operator edits one (spec § 10).
- [ ] **The time is outside 01:30–04:00.** `storage-maintenance.timer` fires at 02:00 and
      the seven `*-dump.timer`s at 02:30 (immich, mailflow, mealie, nextcloud,
      openarchiver, openproject, paperless); the window leaves both clear on either side.
- [ ] **The hourly replica push is not running.**
      `systemctl --user -M svc-kopia@ list-timers` — `kopia-sync.timer` fires at
      `:00`-ish every hour; start inside the first half of an hour so a 10–15 minute
      converge does not meet it. It is not a hard deadline (`kopia-sync.service` only
      `podman exec`s into whatever container is running and leaves no failed unit behind
      when it meets a restart, as the stage 1 cutover measured at 17:02:46), but a push
      interrupted mid-converge makes the verification below ambiguous.
- [ ] **No Molecule run on the workstation**, and no second converge in flight:
      `systemctl is-active cradle-deploy.service` → `inactive`.
- [x] **The StorageBaby PR is reviewed and not merged**, and this runbook is in it.
      _Reviewed 2026-10-18 (task review, final whole-branch review, re-review of the fix
      wave); open as PR #4; the operator's own read is the last review._

## The pre-flight

`cradle-deploy --check` fetches everything, runs the playbook `--check --diff`, and
converges nothing: it writes no `/var/lib/cradle/deploy.state` and changes nothing the host
runs. It is not inert on disk, though — it refreshes all three kinds of checkout the same
way a converge does (`git fetch --prune`, `checkout --detach`, `reset --hard`,
`clean -fdx`) and removes any `/var/lib/cradle/sources/<name>` no pointer names any more.
Since stage 1's follow-up it takes `/run/cradle-deploy.lock` with `flock -w 900`, so a tick
fired beside it waits rather than running a second playbook over the same checkout. That
lock is why a hand-run **pre-flight** is safe; a hand-run **converge** on this host is
still not (see "The converge").

**The check has to be able to fail.** `cradle-deploy` reads the ref to converge out of
`/etc/cradle/deploy.conf` — it has no `--host-ref` flag, `--check` is its only argument —
so once the PR is merged and CI has moved `stable`, the timer's next tick converges within
five minutes whatever the check said, and there is nothing left to stop. **Stop the timer
first**, which is the same ruling stage 1 took for the same reason:

```sh
doas systemctl stop cradle-deploy.timer
doas systemctl is-active cradle-deploy.timer # inactive
```

- [ ] Timer stopped, **then** merge the PR and let CI fast-forward `stable`
      (`git fetch origin stable` and
      `gh api repos/janlucaklees/StorageBaby/branches/master --jq .commit.sha` agree).
- [ ] The pre-flight, logged in full — the traefik and kopia diffs come early in the
      service loop, so a `tail` would cut exactly the part this check exists for:

```sh
doas cradle-deploy --check 2>&1 | tee /root/stage2-check.log
```

- [ ] Read the log against the two lists below. **Anything beyond them stops the rollout**:
      leave the timer stopped and revert the merge (CI moves `stable` back). Nothing the
      host runs has changed — the check wrote no `deploy.state` and no unit, secret or
      directory — so the revert's own tick is the whole of the undo.

The alternative, if the merge should not happen before the check: point `HOST_REF` in
`/etc/cradle/deploy.conf` at the branch (`HOST_REF=cradle-built-ins`), run the check, and
set it back to `stable`. It needs the two new secret files committed **on the branch** as
well as on master, and the timer stopped just the same, so it buys only the un-merging — not
worth it unless the merge itself is the thing in doubt.

**What must appear:**

- [ ] `traefik.container`: **no diff at all.** The built-in's unit is the folder's unit,
      the dashboard's label is still `traefik` (`ingress` is not declared, so the default
      applies), the three secret names are unchanged and `config_dir` is unchanged.
      `traefik-dynamic.d/traefik-traefik.yml` likewise unchanged.
- [ ] `kopia.container`: **exactly two lines**, the ones the render diff in the PR
      enumerated — `Environment=KOPIA_REPOSITORY=filesystem` removed (the `s3`
      repository form went with the built-in; the repository is always a local filesystem
      one now) and `Environment=KOPIA_SYNC_MUST_EXIST` `false` → `true`. Everything else
      is byte-identical: `KOPIA_SERVER_USERNAME=jlk`, `KOPIA_PUBLIC_HOST=kopia.home.klees.io`,
      `KOPIA_SYNC=s3`, the endpoint, the bucket, `KOPIA_CLIENT_HOSTS=storagebaby`, and the
      nine `Secret=client_*` lines, sorted.
- [ ] `kopia-sync.service` and `kopia-sync.timer`, and the route file
      `/etc/cradle/traefik/dynamic.d/kopia-kopia.yml`: unchanged.

**Five more diffs the check prints, in the four bullets below, all of them expected.** None
is a unit and none is a
line this rollout chose; they are what moving two folders into Cradle does to files the
roles copy verbatim. The list above would make an operator stop on every one of them, so
it is here:

- [ ] `/etc/cradle/kopia/start.sh`: **a large diff, and a second reason kopia restarts.**
      The role copies a service's `config/` tree to `/etc/cradle/<name>/`, and the
      built-in's `start.sh` is not the folder's: it lost the `s3` _repository_ branch
      (Backblaze is a replica fed by `sync.sh`, never the repository) and the `REPLACE_ME`
      guard over `/run/secrets/client_*`, which has nothing left to guard now that those
      values are generated. Measured against the file leaving this repository: 177 lines
      down to 124, 69 removed and 16 added. A changed `config/` copy marks **every** unit
      of the service changed, so this alone would restart kopia even if its two
      `Environment=` lines had not moved.
- [ ] `/etc/cradle/kopia/sync.sh`: **three comment lines**, and nothing else — the
      "every test host" wording became "both test hosts" and the sentence about symlinked
      service folders became one about a built-in being placed on every host that backs
      up. Not a behaviour change; it is in the same `config/` copy as `start.sh`, so it
      changes no restart decision that file has not already made.
- [ ] `/usr/local/sbin/cradle-deploy`: **the shared-pointer glob removed**, seven lines —
      the script no longer looks for `hosts/shared/services/*/source.yml`, because there
      is no `hosts/shared/` any more. `host_base` installs this script, so the converge
      rewrites the very file it is running from; `copy` writes a temp file and renames it
      over the old one, so the running tick keeps the inode it opened and finishes on the
      old script. The next tick is the first to use the new one.
- [ ] The ciphertext of `/etc/cradle/traefik.secrets.sops.yaml` and
      `/etc/cradle/kopia.secrets.sops.yaml`: **both replaced wholesale.** The copy's
      source is a different file now — `hosts/StorageBaby/secrets/ingress.sops.yaml` and
      `backup.sops.yaml` instead of the two folders' own — and sops ciphertext differs
      per encryption even for an identical value, so the diff is the whole file both
      times and says nothing about whether the values match. Neither copy is
      `register:`ed, so **neither restarts anything**; that is why traefik can take a new
      secrets file and still not restart.
- [ ] `host_base`: **the backup-clients directory changed** (`/var/lib/cradle/backup-clients`,
      0700 root) and **nine generator items changed** — "One generated password per
      backed-up service, created once and never rewritten", one per service: hermes, immich,
      mailflow, mcp, mealie, nextcloud, openarchiver, openproject, paperless. In check mode
      a `command` task with a `creates:` that does not exist reports changed without running,
      so this is "would have run", not nine passwords written.
- [ ] **Eighteen debug lines** from the `service` role's "What the first converge will sync
      for this service", reading "`<name>` will be synced from
      `/var/lib/cradle/backup-clients/<service>`, which host_base creates on this same
      run" — each consumer's own `kopia_password`, plus kopia's nine `client_*`.
- [ ] The nine `<svc>-backup.container` sidecars: **unchanged**. `backup_fqdn` resolves to
      the same `kopia.home.klees.io`, so `KOPIA_SERVER_URL=https://kopia.home.klees.io:443`
      is the line it already was.

**What a check cannot show, and must not:** the generated files do not exist yet, so the
secret-sync loop has nothing to iterate and **no `podman secret` change and no restart line
appears anywhere**. The restarts are the converge's to produce, and they are listed under it.
A `--check` that does report a secret change or a restart is showing something this rollout
did not ask for.

**What the check cannot prove, and the operator has to check another way: the two new
secret files.** A green pre-flight says nothing about their contents. The copy to
`/etc/cradle/<name>.secrets.sops.yaml` is a write, so `--check` skips it, and the two
tasks that read a secrets file — "Decrypt on the host" (`check_mode: false`, because its
registered output is parsed) and "All declared secrets present" — therefore run against
the **old** file the last converge left on disk. Traefik's three key names and kopia's
four are subsets of the two old key sets, so both pass. What a `--check` consequently does
**not** catch: a wrong value, a file encrypted to the wrong recipient, or a misspelled key
name in the new file. What it does catch is a **missing** file: all three candidate paths
fail to exist, and the role refuses the service by name, naming the paths it looked for.

The checks that cover what is coverable without decrypting anything, in the host
repository's checkout — sops leaves key names and recipients in plaintext:

```sh
# Two recipients each, and one of them the host's own key (the `storagebaby` anchor
# in .sops.yaml, age142s7lf…). `2` and `1` for both files.
grep -c 'recipient: age1' hosts/StorageBaby/secrets/backup.sops.yaml hosts/StorageBaby/secrets/ingress.sops.yaml
grep -c 'age142s7lfpzjdddrrxkee8gr057u9hqkanu220n4f6hvzmr9n66x4rs9w3zvs' \
	hosts/StorageBaby/secrets/backup.sops.yaml hosts/StorageBaby/secrets/ingress.sops.yaml

# The key names themselves: 4 and 3.
grep -cE '^(server_password|repository_password|b2_key_id|b2_application_key):' \
	hosts/StorageBaby/secrets/backup.sops.yaml
grep -cE '^(porkbun_api_key|porkbun_secret_api_key|dashboard_htpasswd):' \
	hosts/StorageBaby/secrets/ingress.sops.yaml
```

A misspelled key name costs nothing but noise — the converge fails at traefik's or
kopia's "All declared secrets present" assert — the two built-ins converge first, before
any consumer, so it fails before any restart. A wrong **value** is the expensive one, and it surfaces late:
`repository_password` wrong shows as kopia restart-looping (it fails the repository
connect, and there is no reset and no escrow — see the preconditions), `server_password`
wrong as every sidecar failing to connect, and a wrong Porkbun key **not until the next
certificate renewal**, weeks later, in Traefik's log and nowhere else.

**What must not appear.** Any one of these stops the rollout — nothing below is a thing
stage 2 asks for:

- [ ] No pool remount, and no `storage` role change at all. Both remount-warning tasks
      `skipping`, the four disk/parity mounts and the pool `ok`.
- [ ] **No volume-directory creation and no `chown`.** `backup.repository` is the exact
      path `volume_overrides: kopia/repo` named, so the `repo` directory already exists
      with the owner it has; the role creates a volume directory only when it is absent
      and adopts an owner only when it differs. A path that differs by one character would
      show up here as a fresh directory on the pool — and nowhere else, because nothing
      refuses it.
- [ ] No route-content change, no new or removed route file, and no `podman secret` change
      for any service (see above).
- [ ] `failed=0`, `unreachable=0`, `rescued=0`.

## The converge

The merge and `stable` are already done — the pre-flight needed them. What is left is to
let the deploy loop pick it up.

**The two secret files have to be in the commit `stable` points at.** The merge is what
moves `stable`, so they must be committed on master, or on the PR branch that the merge
carries — a file sitting unstaged in the operator's checkout is a converge that fails at
the first built-in. The precondition above says committed for exactly this reason.

**The server is always converged before its clients, which decides what a half-finished
run costs.** The playbook places the built-ins first and the host's own services after
them, sorted, so kopia takes its nine new `client_*` passwords and registers them on its
restart before any consumer's `kopia_password` is replaced. Two consequences. In the
normal case there is a short window — one restart apart — in which kopia is already
serving the new passwords and a consumer's sidecar still holds the old one; the sidecar
verifies its stored connection on every start and reconnects when it does not open, so it
repairs itself and the only cost is a snapshot that does not happen in those minutes. If
the play **dies** between kopia and some consumer — any failing task in a service sorted
before it will do that, because the role is included once per service from one play and a
failure stops the play for every service that has not converged yet; the hook health wait
is the only failure in the role that is deliberately not fatal — that consumer is never
reached, so it keeps the password the server has already replaced, and
its sidecar is locked out: it retries the connect under a 120 s timeout with 15 s between
attempts, logging the failure, until the next tick converges it and replaces the secret.
That is backups down for minutes on one service. **It is a wait, not a rollback** — the
next tick fixes it, because a failed converge writes no `deploy.state`.

- [ ] **Start the timer again and let its own tick converge.** Do not run `cradle-deploy`
      by hand on this host. That is the operating rule stage 1 established and the reason
      is in `2026-10-17-cradle-cutover.md`, "The blocker: the timer/converge race" and its
      follow-ups: a hand converge beside the timer's tick is the real exposure, two
      playbooks restarting the same pods while the second tick's checkout refresh runs
      `git reset --hard` + `git clean -fdx` on the tree the first is executing out of. The
      lock added afterwards makes a breach wait rather than collide, but the rule stands.
      A timer that has been stopped past its interval elapses immediately when started, so
      this is the converge, not a wait: `doas systemctl start cradle-deploy.timer`.
- [ ] **Follow it:** `journalctl -u cradle-deploy.service -f`. Stage 1's comparable
      converge took 11 min 19 s and restarted sixteen services; this one restarts ten.
- [ ] **Ten restarts, and traefik is not among them.** Eighteen `podman secret` changes
      (the nine consumers' `kopia_password` and kopia's nine `client_*`), then one restart
      of kopia and one of each of the nine consumer pods. **Traefik does not restart**: its
      unit is byte-identical, and `podman-secret-sync` inspects a secret's current value
      before replacing it and prints `UNCHANGED` when it matches — so its three credentials
      moving from the deleted `hosts/shared/` folder to the host's `ingress.sops.yaml`,
      under the same names with the same values, is not a change anything sees. A traefik
      restart is not a failure, but it means a value or a line did differ after all: find
      out which before trusting the rest of the run.

**The order within the tick, and why each restart happens:**

1. `host_base` generates the nine client passwords **before any service is touched** — one
   `openssl rand -base64 32` per backed-up service, written to a temporary name and moved
   into place, guarded by `creates:` so it happens exactly once in the life of the host.
2. `traefik`, then `kopia`: re-rendered from the built-ins, same users, same volume paths,
   same data. **Traefik does not restart** — nothing about it changed, and the role
   restarts only what did. Kopia restarts on the two changed `Environment=` lines and on
   its nine `client_*` secrets changing.
3. The nine consumers, one restart each, because a secret of their pod changed
   (`kopia_password`).
4. **The after-change hooks of the three services that declare any.** A changed secret
   does not mark one unit changed, it marks **every** unit of the service changed — which
   is also what makes a hook fire. So nextcloud runs its five `unit_changed` `occ`
   commands (`maintenance:repair --include-expensive` is the slow one) plus its two
   `when: always` ones, openproject its `bundle exec rake db:seed`, and paperless its
   `touch`. Each service's hooks wait for their container's own health check first, up to
   **ten minutes** (60 attempts, 10 s apart), so **multi-minute silences in the journal
   are expected** at nextcloud and openproject and are not a hung converge.

The server registers the new passwords **on its own restart**: `config/start.sh` loops
over `/run/secrets/client_*` and runs `kopia server users add … || … set`, before the
server binds. A sidecar that comes back holding a password the server has not registered
yet, or whose stored connection no longer opens, throws `repository.config` away and
reconnects. `ansible/roles/service/files/kopia-client.sh:84-94` in Cradle **verifies** the
stored connection on every start (`kopia repository status` under a timeout) and reconnects
**only when that fails** — so a sidecar whose connection still opens is left alone, and one
holding a password the server has not caught up with repairs itself. That is what makes the
restart order harmless either way.

## Verification

- [ ] `cradle-svc ps traefik`: `active (running)`, and its `ActiveEnterTimestamp`
      **unchanged** — nothing about the ingress changed, so it must not have restarted.
      `cradle-svc ps kopia`: `active (running)`, restarted once inside the converge window.
- [ ] **If Traefik did restart, stop here and find out why before checking anything else.**
      Its unit and its three secret values are supposed to be byte-identical, so a restart
      means one of them is not: the three values in `ingress.sops.yaml` differ from the
      three the deleted `hosts/shared/services/traefik/secrets.sops.yaml` held. Diff them
      — `mise run sops` each side, on the workstation, from a checkout that still has the
      old file (`git show <pre-merge sha>:hosts/shared/services/traefik/secrets.sops.yaml`)
      — and fix the new file before trusting any of the backup verification below. A wrong
      `dashboard_htpasswd` only locks the operator out of the dashboard; a wrong Porkbun
      key is silent until the next certificate renewal, which is why this is checked now
      and not when a certificate expires.
- [ ] Each of the nine pods: `cradle-svc ps <name>` → `active (running)`. And the five
      services with no backup block — calino, jellyfin, renaissance, stirling-pdf, yuzukam
      — untouched: their `ActiveEnterTimestamp` must predate the converge.
- [ ] Zero failed units in every `svc-*` manager.
- [ ] `ls -l /var/lib/cradle/backup-clients` — nine files, `0600 root root`, in a `0700`
      directory. Their contents are not read here and belong in no record.
- [ ] **The kopia UI answers** at `https://kopia.home.klees.io/` and `jlk` can log in with
      `server_password` from `backup.sops.yaml`. An unauthenticated probe
      (`curl -sk -o /dev/null -w '%{http_code}'`) against it returns `401`, which is the
      healthy answer and what the unit's own health check greps for.
- [ ] **Every sidecar healthy within ten minutes.** Per service user:
      `podman ps` showing `<svc>-backup` as `healthy`, or `cradle-svc ps <svc>`. For the
      slowest, the direct question:
      `podman exec <svc>-backup kopia repository status` — it must print a connected
      repository, not a password prompt or a `rpc error`.
- [ ] `kopia snapshot list --all` on the server lists **all nine sources** as
      `<service>@storagebaby`, with their last snapshot from before the rollout still
      present. A client re-registered with a new password keeps its identity and therefore
      its snapshot history; a source that has gone missing means the identity changed,
      which it must not have.
- [ ] **The next hourly replica push succeeds.** Wait for it rather than forcing it, then
      `journalctl --user -M svc-kopia@ -u kopia-sync.service -n 40`: no `AWS` credential
      error, no "repository does not exist" — the last is the one `must_exist: true` now
      turns into a loud failure, and the bucket has held the repository since the
      2026-10-14 cutover, so it must not appear.
- [ ] One **no-op tick** afterwards: the next `cradle-deploy` run logs "nothing changed
      since the last converge", or converges with `changed=0`. That is render parity, and
      the proof the built-ins converge to a fixed point.
- [ ] **If the converge ends with "After-change hooks were skipped for: …"**, it is
      converged but **not finished**, and it is **not a rollback trigger**. A container
      that did not become healthy within ten minutes makes the role skip that service's
      hooks all-or-nothing and record it; the playbook fails on the collected list after
      the last service, so every other service converged and only the named ones are
      un-finished. **The next tick does not retry them.** The converge failed, so no
      `deploy.state` was written and the tick does converge again — but by then nothing is
      re-rendered and no secret changes, `changed_units` is empty, and an empty
      `changed_units` is exactly what filters the hook list down to the `when: always`
      ones. So nextcloud's two `config:*:set` calls run again and its five repairs never
      do. Read the named container's journal from the top, and once it is healthy **run
      the skipped commands by hand** — they are the ones listed in that service's
      `service.yml` under `hooks.after_change`, e.g.
      `doas /usr/local/sbin/podman-as svc-nextcloud podman exec -u www-data nextcloud-app php occ maintenance:repair --include-expensive`.

## Rollback

Two things have to move together, and reverting only the first leaves the host refusing to
converge at all:

- [ ] **Revert the StorageBaby PR** (`gh pr revert` / `git revert -m 1 <merge sha>`, push,
      let CI move `stable`). That brings `hosts/shared/`, the kopia folder and the nine
      `kopia_password` declarations back.
- [ ] **Confirm the pre-stage-2 branch exists in Cradle**:
      `git ls-remote origin pre-built-ins` must print
      `63e47a2f1f2726c6d79619cfb19dd521cfe8a384`. It was pushed when this runbook was
      written (`git push origin 63e47a2:refs/heads/pre-built-ins`) and exists for this
      rollback alone.
- [ ] **Pin Cradle back** by hand-editing `/etc/cradle/deploy.conf` on the host:
      `CRADLE_REF=pre-built-ins` in place of `CRADLE_REF=stable`.

**It has to be a branch name, not a SHA.** `cradle-deploy`'s `sync_checkout` fetches
`+refs/heads/<ref>:refs/remotes/origin/<ref>` and nothing else, so a commit id in
`CRADLE_REF` makes every single tick fail at the fetch — the host would stop converging
altogether rather than converge the old engine. Hence the branch.

Pinning Cradle back is necessary, not optional: **the new Cradle refuses a host repository
that still has `hosts/shared/`**, so the revert's own converge would fail against stage-2
Cradle. `deploy.conf` is written once by bootstrap and read by every tick — no role
rewrites it — so the pin survives converges and has to be taken out by hand:
`CRADLE_REF=stable` again when stage 2 is attempted a second time. Check it with
`grep CRADLE_REF /etc/cradle/deploy.conf` before and after.

**What the revert restores, and what it does not.** The nine `client_*` and nine
`kopia_password` podman secrets were **replaced under the same names**, not added beside
the old ones: `podman-secret-sync` writes with `podman secret create --replace`. So
nothing was orphaned, and the revert's converge re-syncs the sops values the same way,
over the generated ones. (Spec § 7 says the old podman secrets "still exist"; they do not,
and this is the decision that replaces that sentence.) The cost is a second restart cycle
— kopia and the nine pods once each — and the server re-registering the sops passwords on
its restart, exactly as it registered the generated ones.

The nine files under `/var/lib/cradle/backup-clients` are then unread and can be left
where they are; `creates:` means a second attempt at stage 2 reuses them rather than
generating new ones, which is the desirable outcome. Remove them only if the sops values
and the server's registered users have diverged in a way worth starting clean from — and
even then the server re-registers whatever it is handed on its next start, so the only
thing a stale file can cost is one more restart.

## Follow-ups

(To be filled in during the rollout.)
