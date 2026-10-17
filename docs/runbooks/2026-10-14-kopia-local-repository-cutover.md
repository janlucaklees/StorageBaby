# Cutover runbook: kopia's repository moves onto the pool

The change in `docs/superpowers/specs/2026-10-14-kopia-local-repository-design.md` is
inert until an operator runs this. The repository is new, so this is a clean start, not
a migration — but one step below cannot move, and reordering it makes the whole change
look like it did nothing.

**This was stage 1 of two**, and the numbered steps below are stage 1's. It ships the
repository onto the pool, the hourly replica and the failed-unit report, and leaves
`/apps` in the snapraid array with its four nightly soft errors — the status quo, not a
regression, because taking `/apps` out before the repository held Immich's library
would have left 323 GB of photos with neither parity nor a snapshot for about a day.
Stage 2 is the last section of this document; it was committed on 2026-10-15 and goes
to the host by an ordinary merge and converge.

**`--delete` is on as of 2026-10-16, which step 2 below predates.** At cutover the
hourly replica push deliberately omitted it, on the reasoning that a replica should
survive a local mistake. That reasoning was wrong about what this replica is: kopia
owns the version history, and the bucket is a byte-for-byte copy of that repository
rather than a second history of it, so withholding the flag bought no undo and cost
unbounded growth — every pack blob local garbage collection drops would stay at
Backblaze forever. The operator enabled it with the trade stated: the bucket keeps
only the last version, so a propagated delete is final. The current position lives in
`hosts/StorageBaby/services/kopia/config/sync.sh` and `docs/ownership.md` § 7; step 2
below is kept as the record of what was decided on the night.

> **The wipe must land between the merge and the next converge, and nothing may
> converge in that window.** `start.sh` connects a repository only when
> `repository.config` is absent, and that file exists today, pointing at the old
> Backblaze bucket. A converge with it still there restarts kopia straight onto
> Backblaze — the `filesystem` branch never runs and the change appears to do nothing.
> A converge _after_ the wipe but on the _old_ commit is worse: it recreates
> `repository.config` against the old bucket and silently undoes the wipe. The deploy
> timer fires every five minutes and converges whenever **any** tracked checkout moves,
> including an external service's own `stable`, so it is stopped first (step 4) and
> only the operator decides when the host converges (step 7).

- [x] **1. Free the space — done.** The pool was at 87% and the new repository will
      hold roughly 500 GB once Immich's `upload` is actually in it, which it never has
      been (step 8). The operator has already deleted the stale 532 GB `CloudBaby` tree
      under `/pool/backups/devices/`; the pool now has 1.6 TB free, comfortably ahead of
      the ~500 GB needed. Nothing left to do here.

  - The design also named `/pool/jlk` (954 GB) as a second candidate. It has not been
    touched and is not required for this cutover — noted here so a reader doesn't wonder
    whether that half of step 1 was dropped, not as an instruction: it's the operator's
    own data and nobody has decided it's stale.

- [x] **2. The bucket and its credentials — done.**
  - [x] `b2_key_id` and `b2_application_key` for bucket `GTMyXab9-jlkkb` are committed
        in `hosts/StorageBaby/services/kopia/secrets.sops.yaml`.
  - [x] **Superseded 2026-10-16 — see the note under the title. What follows was the
        position at cutover: the lifecycle rule is no longer a precondition, and the
        bucket should keep versions for now.** `config/sync.sh` does not pass `--delete`:
        the replica is downstream of a local primary, and propagating removals hourly
        would carry a local fault — a degraded branch, a bad restore, a mistaken `rm`
        — off-site inside the hour, which is exactly what a replica is supposed to
        survive. Nothing is deleted or hidden at Backblaze, so there is nothing for a
        lifecycle rule to expire. Leave versioning as it is. The cost is that the
        replica keeps blobs the local repository's garbage collection has already
        dropped, and grows faster than the source — the accepted trade until a sanity
        guard exists on this side. Full argument in the service README, "The off-site
        replica", and in `docs/ownership.md` § 7.

- [ ] **3. Rotate `repository_password` — optional.** Not required: a fresh repository
      derives fresh keys from whatever password it is given, and the bucket the current
      value opens is being deleted in step 11 anyway. Rotate it only for a clean break.
      Either way: **back the value up outside this repository before anything is
      written.** There is no reset and no escrow.

- [ ] **4. Stop the deploy timer.** As root on the host:

  ```
  doas systemctl stop cradle-deploy.timer
  doas systemctl is-active cradle-deploy.service
  ```

  This is first, before the merge and long before the wipe. The timer fires every five
  minutes and the deploy script converges whenever **any** checkout it tracks differs
  from the last successful run — not just this repository, but every external service
  this host points at, `renaissance` among them. Such a tick during the window below
  converges the _old_ commit, and the `service` role's "Start units that are not up"
  task restarts kopia with the old `start.sh`, which recreates `repository.config`
  against the old bucket and silently undoes the wipe. That is exactly what the banner
  at the top warns about, and stopping the timer is what makes the warning hold.

  Stopping the timer does not stop a converge already running, hence the second line.
  Read its answer: `inactive` means nothing is in flight, go on. `active` or
  `activating` means a converge is running — wait for it. `failed` means the last one
  failed and waiting will not help; read
  `journalctl -u cradle-deploy.service` before going any further, because a host
  that cannot converge the old commit will not converge the new one either, and step 6
  would leave the nine services down with no converge to bring them back.

  Nothing is left disabled afterwards. `host_base` sets the timer `enabled` and
  `state: started` from `deploy_timer: true` on every converge
  (`ansible/roles/host_base/tasks/main.yml`, "Deploy timer state"), so step 7 re-arms
  it as a side effect of converging.

- [ ] **5. Merge, and wait for the host's view of it.** Merge this branch to master,
      then wait for two things, not one: CI green (it runs the static suite and the
      `test-ci` Molecule scenario), and CI's fast-forward of `stable` to that commit.
      Nothing on the host reacts to a commit that has not reached `stable`.

  This is the step that can take a while and the step that can fail. It is deliberately
  before the wipe: if CI goes red, nothing has been stopped, nothing has been deleted,
  and the branch gets fixed with every service still running. Stopping nine pods first
  would mean waiting out the whole CI window with them down — and no way forward if it
  never goes green.

- [ ] **6. Stop kopia and every client pod, then wipe.** Only now, with `stable`
      already pointing at the new commit and the deploy timer stopped. This is the
      short step: from here until step 7 completes, the nine services are offline.

  Stop the server and every service that carries a backup sidecar.
  `cradle-svc stop <name>` is pod-aware: for kopia itself, a single-container
  service with no `.pod` unit, it stops `kopia.service`; for each of the nine clients
  below it stops the whole pod.

  ```
  cradle-svc stop kopia
  cradle-svc stop hermes
  cradle-svc stop immich
  cradle-svc stop mailflow
  cradle-svc stop mcp
  cradle-svc stop mealie
  cradle-svc stop nextcloud
  cradle-svc stop openarchiver
  cradle-svc stop openproject
  cradle-svc stop paperless
  ```

  The window is the wipe plus one converge, because step 5 already absorbed the CI
  wait. Run step 7 straight after this one.

  With everything stopped, remove each path below — as a directory, not just its
  contents, so the next converge recreates it fresh with the right owner:

  ```
  rm -rf /pool/apps/kopia/config
  rm -rf /var/lib/storagebaby/fast/kopia/cache
  rm -rf /var/lib/storagebaby/fast/hermes/backup-cache
  rm -rf /var/lib/storagebaby/fast/immich/backup-cache
  rm -rf /var/lib/storagebaby/fast/mailflow/backup-cache
  rm -rf /var/lib/storagebaby/fast/mcp/backup-cache
  rm -rf /var/lib/storagebaby/fast/mealie/backup-cache
  rm -rf /var/lib/storagebaby/fast/nextcloud/backup-cache
  rm -rf /var/lib/storagebaby/fast/openarchiver/backup-cache
  rm -rf /var/lib/storagebaby/fast/openproject/backup-cache
  rm -rf /var/lib/storagebaby/fast/paperless/backup-cache
  ```

  `config` and `cache` are the stale connection and its matching cache; the nine
  `backup-cache` volumes fail the same way for the same reason — a cache that belongs
  to a different repository fails the next start with `cipher: message authentication
failed`, which reads like a wrong password and is not one.

  Leave alone:

  - every `backup-config` volume — the server URL and the client password are
    unchanged, and `kopia-client.sh` already checks the stored connection against
    `kopia repository status` on every start and discards and remakes it if that fails
  - kopia's `logs` volume
  - `/pool/backups/devices/StorageBaby/kopia` — **it does not exist.** Measured on the
    host: the parent `/pool/backups/devices/StorageBaby/` is there and empty, the
    `kopia` directory below it is not. The converge creates it, 0750 and owned by
    `svc-kopia`, and `start.sh` then finds it empty and creates the repository in it.
    Nothing to wipe and nothing to confirm beyond leaving it alone — and it is
    deliberately not in the block above, because nothing that lives under the
    repository path belongs in a wipe list, today or at any later run of this runbook.
    (Should anyone create it by hand before the converge, `owner: 0` on the `repo`
    volume makes the role adopt it; that is what the declaration is for.)

  **Do not start kopia by hand between here and step 7.** A manual start runs whatever
  `start.sh` is on disk, which is still the old one until the converge replaces it: it
  reads `KOPIA_REPOSITORY=s3` and reconnects straight to the old bucket, recreating the
  exact file just deleted.

- [ ] **7. Converge.** The timer is stopped, so this is the operator's call and not a
      wait:

  ```
  doas systemctl start cradle-deploy.service
  journalctl -u cradle-deploy.service -n 50
  ```

  Safe to run by hand: it's the same plain `Type=oneshot` the timer fires, fetching
  `stable` and every pointed repository and converging only when something differs from
  the last successful run — a no-op on an unchanged checkout, so running it more than
  once costs nothing (`docs/quickstart.md`, `docs/external-service.md`). It also puts
  the deploy timer back, so nothing from step 4 has to be undone by hand; confirm with
  `systemctl is-active cradle-deploy.timer` once the run is through.

  The role creates `/pool/backups/devices/StorageBaby/kopia` — it does not exist yet —
  owned by `svc-kopia`. `start.sh` then finds `repository.config` absent, takes the
  `filesystem` branch, and because that directory is empty **creates** the repository
  in it rather than connecting to one, then registers every client. The
  converge also starts every pod stopped in step 6: the `service` role's "Start units
  that are not up" task (`ansible/roles/service/tasks/units.yml`) starts any unit of a
  placed service that is not `active`, whether or not that service's own files changed
  in this run.

- [ ] **8. First full snapshot.** Not an operator action to start — each client
      sidecar's own scheduler takes it, at that service's `KOPIA_SNAPSHOT_TIME`. Local
      disk, no network, roughly 500 GB; Immich's 323 GB `upload` is the long pole and
      the whole point of this change. Verify, as root on the host:

  ```
  podman-as svc-kopia podman exec kopia kopia snapshot list --all
  ```

  shows `immich@storagebaby:/data/upload`. It never has.

- [ ] **9. First sync.** Also not an operator action to start — `kopia-sync.timer`
      fires hourly on its own (`RandomizedDelaySec=300`), and it will very likely fire
      before step 8's first full snapshot finishes. That is fine: a Kopia repository on
      disk is append-and-delete only, so a sync mid-snapshot just copies a blob set
      that is a little behind and picks up the rest next hour — never wrong, only late.

  `sync_must_exist` is already `false` in the committed `service.yml`, which is what
  lets this first push create the replica's repository format blob. Watch it run:

  ```
  journalctl _SYSTEMD_USER_UNIT=kopia-sync.service
  ```

  It will not finish overnight — it is a ~500 GB upload over a home line, and the
  hourly timer will start further runs beside it. "Still running" is the expected
  state for a good while; the nightly mail's failed-unit list staying empty is the
  signal that nothing has gone wrong with it.

  Once a run has completed — and not before, since the flag would make every earlier
  attempt fail against a bucket that has no repository yet: flip `sync_must_exist` to
  `true` in
  `hosts/StorageBaby/services/kopia/service.yml`, commit, push to master, and converge as in step 7. From then on a mistyped bucket fails loudly instead of quietly starting a
  second, empty repository.

- [ ] **10. First snapraid sync.** Automatic, at 02:00. It picks up the new repository
      as roughly 500 GB of new files and writes parity for all of it in one run —
      expect it to take noticeably longer than usual, once. The run that matters for
      stage 2 is one that _started after_ step 8 finished; an earlier one parities a
      half-written repository. And it will not come back clean: the four `/apps` soft
      errors are still there (stage 1 keeps `/apps` in the array), and kopia's hourly
      maintenance deletes blobs while the sync reads, which snapraid reports too. A
      removal under the repository path is expected and benign — see Verification.

- [ ] **11. Delete the old bucket.** Operator, manual, and last — only once step 8 has
      verified a complete set in `kopia snapshot list --all`. The old bucket still
      holds the old repository until this point, which is what the rollback below
      depends on.

## Verification

- `snapraid sync` reports **no soft error that is new**. Not `exit:ok`, which this
  said and which is the wrong bar twice over. Stage 1 keeps `/apps` in the array, so
  its four known soft errors stay — they are what stage 2 removes, and zero under
  `/apps` is stage 2's criterion, not this one. And once the repository is in the
  array, kopia's hourly maintenance deletes blobs while a multi-hour sync reads them,
  so removals under the repository path appear too. Those are expected: a kopia
  repository on disk is append-and-delete only, so a race there can only mean
  _missing_, never _wrong_. What to look for is a soft error under `/apps` that is not
  one of the four, or one anywhere else in the pool.
- `kopia snapshot list --all` lists a source for every `backup.paths` entry of every
  service that declares one — Immich's `upload` above all.
- `kopia maintenance info` shows full cycles with no `cap exceeded`.
- Backblaze's bucket report shows Class B downloads at effectively zero over a week,
  and Class C within the free allowance at 24 syncs a day.
- The nightly mail lists failed units; forcing one unit into failure makes it appear.
- `tests/static` passes, and the `test-ci` Molecule scenario converges with
  `sync: none` rendering the timer and doing nothing.

## Rollback

After step 7, in the same order as the cutover: stop the deploy timer (step 4), get the
previous commit onto `stable` (revert the merge, or point `stable` back), then repeat
step 6 — stop kopia and the nine client pods and wipe `config`, `cache` and every
`backup-cache` again — and converge (step 7). The order matters for the same reason it
does going forward: a converge between the wipe and the right commit undoes the wipe.

The old bucket still holds the old repository until step 11, which is why step 11 is
last and not step 8.

## Stage 2: `/apps` leaves the array — committed 2026-10-15, not yet converged

**Status: the change is made.** It is in the repository as its own commit and is no
longer something an operator edits by hand; what is left is the ordinary route to the
host — merge to master, CI green, `stable` fast-forwarded, converge (steps 5 and 7
above). Kept here because the preconditions and the consequences are the record of why
it was allowed to land, and because the revert is the one thing a reader may still need.

**The preconditions, and which of them was actually verified.**

1. `podman-as svc-kopia podman exec kopia kopia snapshot list --all` shows
   `immich@storagebaby:/data/upload`, **complete**. **Verified by the operator on
   2026-10-15**, together with `nextcloud@storagebaby:/data/data` — every source has a
   completed snapshot. That snapshot is what replaces parity for the photo library.
2. A `snapraid sync` that **started after** that snapshot finished has run. **Not
   confirmed, and the operator's to check before the merge.** Stage 1 converged on the
   evening of 2026-10-14 and the first full snapshot is a multi-hour local copy of
   341 GB, so the 02:00 run may well have started in the middle of it and parityed a
   half-written repository. A manual `mergerfs.balance` moving blobs between branches
   has the same effect on whatever parity that run did write. If the mail from the run
   that counts has not arrived yet, hold the merge — nothing else in the change depends
   on timing.

**The change**, in `hosts/StorageBaby/host.yml` under `storage.snapraid.excludes` —
done, and this is what to read the diff against:

- `- /apps/`, anchored, directly after `- /lost+found/`
- the `- /apps/nextcloud/data/jlk/files/Projects/Kannji/…` line is gone, dead once the
  whole tree above it is excluded
- the comment above `excludes:` now says why `/apps` _is_ excluded

What changes on the host at that converge: the next `snapraid sync` stops reading
`/apps`, the four soft errors are expected to go with it — the first sync after the
converge is what shows that, not the converge itself — and `snapraid fix` after a dead
disk restores media, `/pool/jlk`, `/pool/backups` and the kopia repository.
Application state comes back from a snapshot instead, up to a night old. Jellyfin's
32 GB of metadata is then protected by nothing and is accepted as disposable
(`docs/ownership.md` § 7, item 14).

Reverting is symmetric and cheap: take the exclude back out and converge. The next sync
re-parities `/apps`, long once.
