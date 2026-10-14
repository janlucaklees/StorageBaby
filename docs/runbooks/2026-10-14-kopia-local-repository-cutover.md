# Cutover runbook: kopia's repository moves onto the pool

The change in `docs/superpowers/specs/2026-10-14-kopia-local-repository-design.md` is
inert until an operator runs this. The repository is new, so this is a clean start, not
a migration — but one step below cannot move, and reordering it makes the whole change
look like it did nothing.

> **Steps 1–4 happen before this branch merges to master.** There is no operator-run
> "converge" step: once CI goes green, `stable` fast-forwards and the host's deploy timer
> applies the new code within five minutes, on its own schedule. `start.sh` connects a
> repository only when `repository.config` is absent, and that file exists today,
> pointing at the old Backblaze bucket. Merge before the wipe in step 4 is done, and the
> converge that follows just restarts kopia onto the old bucket — the `filesystem`
> branch never runs, and nothing about the change is visible.

- [x] **1. Free the space — done.** The pool was at 87% and the new repository will
      hold roughly 500 GB once Immich's `upload` is actually in it, which it never has
      been (step 6). The operator has already deleted the stale 532 GB `CloudBaby` tree
      under `/pool/backups/devices/`; the pool now has 1.6 TB free. Nothing left to do
      here.

- [ ] **2. The bucket and its credentials.**
  - [x] `b2_key_id` and `b2_application_key` for bucket `GTMyXab9-jlkkb` are committed
        in `hosts/StorageBaby/services/kopia/secrets.sops.yaml`.
  - [ ] **Outstanding: the bucket's lifecycle rule.** Backblaze keeps every version of
        an object by default. Under that policy `sync.sh`'s `--delete` only _hides_ a
        blob — the bytes are still billed and the replica grows forever while the
        source stays trim, which is the exact thing `--delete` exists to prevent. Set
        the bucket to keep only the last version (or hide-then-delete after a short
        window) before the first sync in step 7, not after — that push is ~500 GB and
        the mistake is expensive to walk back.

- [ ] **3. Rotate `repository_password` — optional.** Not required: a fresh repository
      derives fresh keys from whatever password it is given, and the bucket the current
      value opens is being deleted in step 9 anyway. Rotate it only for a clean break.
      Either way: **back the value up outside this repository before anything is
      written.** There is no reset and no escrow.

- [ ] **4. Stop kopia and every client pod, then wipe — before the converge.** This is
      what the warning above is about.

  Stop the server and every service that carries a backup sidecar — the whole pod in
  each case, which is what `storagebaby-svc stop <name>` does:

  ```
  storagebaby-svc stop kopia
  storagebaby-svc stop hermes
  storagebaby-svc stop immich
  storagebaby-svc stop mailflow
  storagebaby-svc stop mcp
  storagebaby-svc stop mealie
  storagebaby-svc stop nextcloud
  storagebaby-svc stop openarchiver
  storagebaby-svc stop openproject
  storagebaby-svc stop paperless
  ```

  That is a real maintenance window, and longer than the wipe itself: the nine services
  stay offline from this point until the merge below clears CI (including the
  `test-ci` Molecule scenario) and the host's deploy timer reaches it, up to five
  minutes after that. Budget for all of it, not just the few minutes the wipe takes.

  With everything stopped, remove — the directory itself, not just its contents, so the
  next converge recreates it fresh with the right owner. Measured on the host today:

  - `/pool/apps/kopia/config` — the stale connection, pointing at the old bucket
  - `/var/lib/storagebaby/fast/kopia/cache` — a cache keyed to the old repository
  - the nine `backup-cache` volumes, for the same reason:
    - `/var/lib/storagebaby/fast/hermes/backup-cache`
    - `/var/lib/storagebaby/fast/immich/backup-cache`
    - `/var/lib/storagebaby/fast/mailflow/backup-cache`
    - `/var/lib/storagebaby/fast/mcp/backup-cache`
    - `/var/lib/storagebaby/fast/mealie/backup-cache`
    - `/var/lib/storagebaby/fast/nextcloud/backup-cache`
    - `/var/lib/storagebaby/fast/openarchiver/backup-cache`
    - `/var/lib/storagebaby/fast/openproject/backup-cache`
    - `/var/lib/storagebaby/fast/paperless/backup-cache`

  A cache that belongs to a different repository fails the next start with `cipher:
message authentication failed` — which reads like a wrong password and is not one.

  Leave alone:

  - every `backup-config` volume — the server URL and the client password are
    unchanged, and `kopia-client.sh` already checks the stored connection against
    `kopia repository status` on every start and discards and remakes it if that fails
  - kopia's `logs` volume
  - `/pool/backups/devices/StorageBaby/kopia` itself — the new repository's target
    path, already confirmed empty; it needs no action, just confirmation it stays that
    way until step 5

  **Do not start kopia by hand after this.** A manual start before the merge lands runs
  the _old_ `start.sh`, which still reads `KOPIA_REPOSITORY=s3` and reconnects straight
  to the old bucket — recreating the exact file just deleted.

- [ ] **5. Converge.** Merge this branch to master. Once CI is green, `stable`
      fast-forwards and the host's deploy timer picks it up within five minutes —
      nothing further to run by hand. `start.sh` finds `repository.config` absent,
      takes the `filesystem` branch, and because `/pool/backups/devices/StorageBaby/kopia`
      is empty, **creates** the repository there rather than connecting to one, then
      registers every client. The converge also starts every pod stopped in step 4: the
      `service` role's "Start units that are not up" task (`ansible/roles/service/tasks/units.yml`)
      starts any unit of a placed service that is not `active`, whether or not that
      service's own files changed in this run.

- [ ] **6. First full snapshot.** Not an operator action to start — each client
      sidecar's own scheduler takes it, at that service's `KOPIA_SNAPSHOT_TIME`. Local
      disk, no network, roughly 500 GB; Immich's 323 GB `upload` is the long pole and
      the whole point of this change. Verify, as root on the host:

  ```
  podman-as svc-kopia podman exec kopia kopia snapshot list --all
  ```

  shows `immich@storagebaby:/data/upload`. It never has.

- [ ] **7. First sync.** Also not an operator action to start — `kopia-sync.timer`
      fires hourly on its own (`RandomizedDelaySec=300`), and it will very likely fire
      before step 6's first full snapshot finishes. That is fine: a Kopia repository on
      disk is append-and-delete only, so a sync mid-snapshot just copies a blob set
      that is a little behind and picks up the rest next hour — never wrong, only late.

  `sync_must_exist` is already `false` in the committed `service.yml`, which is what
  lets this first push create the replica's repository format blob. Watch it succeed:

  ```
  journalctl _SYSTEMD_USER_UNIT=kopia-sync.service
  ```

  or wait for the nightly mail's failed-unit list to stay empty.

  Once it has succeeded once: flip `sync_must_exist` to `true` in
  `hosts/StorageBaby/services/kopia/service.yml`, commit, push to master — same path as
  step 5. From then on a mistyped bucket fails loudly instead of quietly starting a
  second, empty repository.

- [ ] **8. First snapraid sync.** Automatic, at 02:00. It picks up the new repository
      as roughly 500 GB of new files and writes parity for all of it in one run —
      expect it to take noticeably longer than usual, once.

- [ ] **9. Delete the old bucket.** Operator, manual, and last — only once step 6 has
      verified a complete set in `kopia snapshot list --all`. The old bucket still
      holds the old repository until this point, which is what the rollback below
      depends on.

## Verification

- `snapraid sync` ends `exit:ok` with zero soft errors.
- `kopia snapshot list --all` lists a source for every `backup.paths` entry of every
  service that declares one — Immich's `upload` above all.
- `kopia maintenance info` shows full cycles with no `cap exceeded`.
- Backblaze's bucket report shows Class B downloads at effectively zero over a week,
  and Class C within the free allowance at 24 syncs a day.
- The nightly mail lists failed units; forcing one unit into failure makes it appear.
- `tests/static` passes, and the `test-ci` Molecule scenario converges with
  `sync: none` rendering the timer and doing nothing.

## Rollback

After step 5: converge the previous commit (revert the merge, or point `stable` back)
and repeat step 4 — stop kopia and the nine client pods, wipe `config`, `cache` and
every `backup-cache` again. The old bucket still holds the old repository until step 9,
which is why step 9 is last and not step 6.
