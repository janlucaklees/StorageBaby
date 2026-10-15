# Kopia's repository moves onto the pool, and `/apps` leaves the snapraid array

**Status:** design, approved 2026-10-14, **implemented in full**. It was rolled out in
two stages (decided 2026-10-15, at the whole-branch review): stage 1 shipped § 3.2,
§ 3.3 and § 3.4 — the repository onto the pool, the hourly replica, the failed-unit
report — and stage 2, § 3.1's exclude, was committed on 2026-10-15 once the repository
held Immich's library. Everything below describes the end state, which is now what the
repository declares; the runbook records how each half reached the host.

**Supersedes:** nothing. It changes decisions taken in
`2026-09-23-phase-3-pods-and-backups-design.md` (kopia's repository lives in Backblaze)
and in `2026-09-25-phase-4-storage-and-ftp-design.md` (the whole pool is one snapraid
array).

## 1. Why

Two faults, one cause each, and the same change fixes both.

**Backblaze reads.** Kopia's daily full maintenance walks the repository, works out which
contents no snapshot still references, and deletes the pack blobs they lived in. Two
parts of that need the blob data itself: rewriting a pack that is only partly dead, and
recovering a content that was marked deleted but turns out to still be referenced. With
the repository _in_ Backblaze, that is a download. Measured on storagebaby, the full
cycle aborted on five of the eight mornings before 2026-10-14, every time on

```
Cannot download file, download bandwidth or transaction (Class B) cap exceeded
```

and on 2026-10-11 on a _write_ instead (`PutBlob … StatObject: Access Denied`), which is
the same cap from the other side. The daily cap is 1 GB and the repository holds ~178 GB
of live content.

The visible consequence is worse than a failed cron job: **Immich's photo library has
never been backed up.** It declares `upload` (323 GB) for backup and the repository holds
no snapshot of it — only its 636 MB database dump. The first full upload has never got
through.

**Snapraid soft errors.** The nightly `snapraid sync` ends in `exit:warning` with four
soft errors, all of them files that are rewritten in place under a running service:

```
apps/paperless/data/celerybeat-schedule.db                  Unexpected time change
apps/nextcloud/data/appdata_…/richdocuments/…/capabilities  Unexpected time change
```

Snapraid cannot parity live application state, and this host has no snapshotting
filesystem to hand it a frozen view instead.

## 2. The shape

```
                       /pool
                         |
   +---------------------+------------------------+
   |                     |                        |
 /pool/apps          /pool/shared, /pool/jlk,   /pool/backups/devices/
 live app state      /pool/public, /pool/mk       StorageBaby/kopia
   |                     |                        |
 EXCLUDED from        in the array,             the kopia repository:
 the array             unchanged                 in the array
   |                                               ^
   | kopia sidecars snapshot it, as today  --------+
   |                                               |
   +-------- parity now protects the *backup* ------+
                                                    |
                                      hourly `repository sync-to`
                                                    v
                                         Backblaze bucket GTMyXab9-jlkkb
                                         (a complete, connectable replica)
```

The pivot that makes this work: **a Kopia repository on disk is append-and-delete only.**
Blobs are named after their own content, written once, and from then on only removed —
maintenance writes new packs rather than editing old ones. So `snapraid sync` running
while Kopia uploads sees files appear and files vanish, never a file changing under its
hands. Nothing has to stop for anything, and no second copy or staging directory is
needed.

What recovery looks like afterwards:

| failure                               | before                                               | after                                                                                                                                                           |
| ------------------------------------- | ---------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| one data disk dies                    | `snapraid fix` restores everything including `/apps` | `snapraid fix` restores media, `/pool/jlk`, `/pool/backups` **and the kopia repository**; application state is restored from the repository, back to last night |
| the house burns down                  | the Backblaze repository, up to 24 h old             | the Backblaze replica, up to 1 h old                                                                                                                            |
| ransomware / bad delete inside an app | kopia snapshot                                       | unchanged                                                                                                                                                       |

The accepted trade is the middle column's first row: a dead disk now means restoring
applications from a snapshot rather than repairing them in place. In exchange, snapraid
stops being asked to do a job it was never built for.

## 3. Changes

### 3.1 `/apps` leaves the array

**This section shipped a day after the rest, as stage 2**, because of the order it
implies: at the converge `/apps` leaves the array, at the next 02:00 snapraid drops the
parity it held over it, and had the first Immich snapshot not already existed, the photo
library would have spent roughly a day with exactly one copy, on one mergerfs branch,
with neither parity nor a snapshot behind it. Holding the exclude back until the
repository held that snapshot kept the four nightly soft errors for a few more nights,
which was the status quo rather than a regression, and cost nothing else. The conditions
that released it are the cutover runbook's "Stage 2" section.

The change itself:

`hosts/StorageBaby/host.yml`, `storage.snapraid.excludes`, carries one anchored entry:

```yaml
- /apps/
```

Paths in that list are relative to a _disk's_ root, which for a branch of this pool is
the same as relative to `/pool`. The nextcloud-specific exclude that used to sit below it
(`/apps/nextcloud/data/jlk/files/Projects/Kannji/…`) is dead once the whole tree above it
is excluded, and was removed in the same change.

Nothing else leaves the array. `/pool/shared` (4.9 TB), `/pool/jlk`, `/pool/public`,
`/pool/mk` and `/pool/backups` stay exactly as they are.

**What this leaves unprotected.** Everything in `/pool/apps` without a `backup:` block is
covered by neither parity nor Kopia:

|                                         | size   | decision                                                                                             |
| --------------------------------------- | ------ | ---------------------------------------------------------------------------------------------------- |
| Jellyfin metadata, artwork, watch state | 32 GB  | accepted as disposable — rebuilt by a library re-scan. Revisit when the mount structure is reworked. |
| Stirling-PDF                            | 700 KB | stateless by design, nothing to lose                                                                 |
| Kopia's own `config` volume             | 28 KB  | regenerated on start from `service.yml` and the sops secrets                                         |

### 3.2 The repository moves onto the pool

`hosts/StorageBaby/services/kopia/service.yml`:

- `config.repository` becomes `filesystem`. It was `s3`.
- `volumes.repo` becomes `class: pool`. It was `fast` — correct while the volume was an
  empty stub, wrong now that it is the repository.

`hosts/StorageBaby/host.yml`:

```yaml
volume_overrides:
  kopia/repo: /pool/backups/devices/StorageBaby/kopia
```

The path follows the operator's existing structure — `/pool/backups/devices/<device>/`
already holds `CloudBaby`, `KAIZN-vps` and `StorageBaby`. It sits outside `/apps`, so it
stays in the array. The override is in `host.yml` and not in the service folder because a
filesystem path on this machine is a host fact (`docs/ownership.md` § 4).

`hosts/test-a/host.yml` and `hosts/test-ci/host.yml` drop their
`service_config.kopia.repository: filesystem` override, which is now the default they
were overriding to.

Consequence worth stating: `repository.config` stops holding Backblaze credentials in the
clear. Under `s3` the connect wrote the key id and application key into it; under
`filesystem` it holds a path. The `config` volume stops being a directory that must not
be copied off the host casually.

### 3.3 Hourly push to Backblaze

A timer and its one-shot service in `hosts/StorageBaby/services/kopia/quadlet/`, in the
shape the eight `*-dump.timer.j2` units already in this repository use — plain systemd
user units, not Quadlet, running `podman exec` into the running container.

```ini
# kopia-sync.timer.j2
[Timer]
OnCalendar=hourly
Persistent=true
RandomizedDelaySec=300
```

```ini
# kopia-sync.service.j2
[Unit]
After=kopia.service
Requisite=kopia.service

[Service]
Type=oneshot
ExecStart=/usr/bin/podman exec kopia {{ config_dir }}/sync.sh
```

`Requisite=` and not `Requires=`, for the reason the dump units give: a `podman exec`
into a container that is not running fails anyway, and refusing to run is a louder,
truer failure than starting the service to satisfy a dependency.

**`config/sync.sh`**, deployed with the rest of `config/`. `kopia.container.j2` mounts
`start.sh` as a **single file**, not the directory, so the new script needs a line of its
own beside it — without it `podman exec` finds nothing to run:

```ini
Volume={{ config_dir }}/sync.sh:{{ config_dir }}/sync.sh:ro
```

What the script does:

- Exits 0 immediately when `KOPIA_SYNC` is `none`. That is how a test host, which has no
  Backblaze account, renders the same units and does nothing with them.
- Exports `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` from `/run/secrets/b2_key_id`
  and `/run/secrets/b2_application_key`. **The keys are never command-line arguments** —
  `kopia repository sync-to s3` reads both from the environment, which is the exposure
  `server users add` could not avoid and this one can.
- Runs:

  ```sh
  kopia repository sync-to s3 \
  	--bucket="$KOPIA_S3_BUCKET" \
  	--endpoint="$KOPIA_S3_ENDPOINT" \
  	--delete \
  	--parallel=8 \
  	${KOPIA_SYNC_MUST_EXIST:+--must-exist}
  ```

`--delete` propagates removals, without which the replica grows forever while the source
stays trim. An hourly run overlaps Kopia's own 06:24 full maintenance roughly once a day:
the sync then copies a set of blobs that is already a little stale, and a blob deleted
between the listing and the copy is skipped. Both are corrected by the next run an hour
later. This is the same property that lets `snapraid sync` read the repository live —
blobs are immutable, so a race can only ever mean _missing_, never _wrong_. `--parallel=8` because the flag defaults to **1**, which would make the first
~500 GB push take days. `--must-exist` is off for the first run — the destination has no
repository format blob yet — and on from then on, so a mistyped bucket fails loudly
instead of quietly starting a second, empty repository. It is driven by a `config` key so
the switch is one line of git rather than an edit on the host.

New `config` keys in kopia's `service.yml`:

| key               | value on storagebaby                    | value on a test host          |
| ----------------- | --------------------------------------- | ----------------------------- |
| `sync`            | `s3`                                    | `none` (via `service_config`) |
| `s3_endpoint`     | `s3.eu-central-003.backblazeb2.com`     | unused                        |
| `s3_bucket`       | `GTMyXab9-jlkkb`                        | unused                        |
| `sync_must_exist` | `false` for the first run, `true` after | unused                        |

The two existing `s3_*` keys keep their names and change meaning — from "where the
repository is" to "where the replica goes". The two B2 secrets keep their names and
change reader: `start.sh` no longer touches them, `sync.sh` does.

**What this costs at Backblaze.** Uploads are free on ingress. A sync lists the
destination to decide what is missing, which is Class C, billed per 1000 file names
returned; 24 of those a day against a repository of this size stays inside the free
allowance, but it is the number to watch after the first week — the agreed fallback is to
dial the interval back. Downloads, the thing this whole change exists to remove, become
the format blob and nothing else.

### 3.4 Failed units are reported in the nightly mail

The sync runs in `svc-kopia`'s systemd. The host's mail relay is `/etc/msmtprc`, mode
0600 root, and the `service` role only ever installs units into a service user's own
manager — so a sync that fails at 04:00 today would tell nobody, and an offsite copy
nobody notices failing is the failure mode that matters.

The nightly maintenance run gains a final step, in the orchestrator's existing status
block: ask every service manager for its failed units and print the list. Root already
reaches into those managers — that is what `storagebaby-svc` does — so this needs no
cooperation from any service:

```sh
for u in $(... list of svc-* users ...); do
	systemctl --user -M "$u@" list-units --state=failed --no-legend
done
```

It is deliberately **not** Kopia-aware. It belongs to the platform, it catches a crashed
pod, a failed database dump and a failed sync alike, and the next service that fails at
04:00 is covered without anybody editing the storage role again. This is the one part of
this change that lives outside a service folder, and it earns that by knowing nothing
about any service.

It reports; it does not fail the maintenance run. A failed unit somewhere is news, not a
reason to abort parity.

### 3.5 What is explicitly _not_ changing

- **`mergerfs.balance` and the `stop_services: [jellyfin]` hook.** The balance physically
  moves files between branches and is unaffected by snapraid's filters, so excluding
  `/apps` does not touch it. It is a separate, pre-existing exposure — Nextcloud's and
  Immich's live trees are already moved under running containers today — and it is out of
  scope here. Recorded in § 6. **Superseded 2026-10-15, by a separate change:** the
  nightly balance step is gone (`category.create=pfrd` already spreads new files by free
  space) and `stop_services` is empty on storagebaby, because with `/apps` out of the
  array nothing the run touches is Jellyfin's.
- **The sidecars.** No change to `kopia-client.sh`, the generated sidecar unit, any
  service's `backup:` block, any schedule or any retention.
- **Kopia's internal maintenance cycles.** Hourly quick, daily full, unchanged — they
  simply stop costing anything.

## 4. Cutover

The repository is new, so this is a clean start, not a migration. The order below is not
cosmetic — see step 4.

**The runbook is authoritative on the order, and it differs from this list.** It stops
the host's deploy timer first, merges and waits out CI with every service still
running, and only then stops the pods and wipes — because a deploy tick between the
wipe and the right commit silently undoes the wipe, and because a red CI would
otherwise leave nine services down with no way forward. This section keeps the shape;
`docs/runbooks/2026-10-14-kopia-local-repository-cutover.md` is what an operator
follows.

1. **Free the space.** Delete the stale application backups under `/pool/backups` and
   `/pool/jlk` (operator; ~500 GB). The pool is at 87% and the new repository will hold
   roughly 500 GB once Immich is actually in it.
2. **The bucket and its credentials are the operator's.** Bucket `GTMyXab9-jlkkb`
   created; `b2_key_id` and `b2_application_key` set in
   `hosts/StorageBaby/services/kopia/secrets.sops.yaml`. Nothing in this work reads,
   writes or echoes a credential value.

   **Superseded before rollout: no lifecycle rule, and no `--delete`.** This step used
   to say the bucket must be set to keep only the last version before the first sync.
   It is not: `--delete` was dropped from `config/sync.sh` for the initial rollout
   (§ 3.3's flag list is the target state, not what ships), because a replica that
   mirrors removals hourly carries a local fault off-site within the hour, and a bucket
   that keeps only the last version leaves nothing to roll back to. With no `--delete`
   nothing is hidden at Backblaze and no rule has anything to expire. The runbook and
   `docs/ownership.md` § 7 carry the current instruction and the conditions for
   bringing the flag back.

3. **Optionally rotate `repository_password`.** Not required: a fresh repository derives
   fresh keys from whatever password it is given, and the bucket that the current value
   opens is being deleted anyway. Rotate it if the operator prefers a clean break.
   Either way — **back the value up outside this repository before anything is written.**
   There is no reset and no escrow.
4. **Stop kopia and wipe everything that belongs to the old repository, _before_ the
   converge.** This is the step that cannot move. `start.sh` connects only when
   `repository.config` is absent, and that file exists today on the `config` volume
   pointing at the old bucket with the old keys. Converge first and kopia restarts
   straight back onto Backblaze — the `filesystem` branch never runs and the change
   appears to do nothing.

   With kopia and the client pods stopped:

   - kopia's `config` volume — the stale connection
   - kopia's `cache` volume — a cache belonging to a different repository fails the
     first start with `cipher: message authentication failed`, which reads like a wrong
     password and is not one
   - every service's `backup-cache` volume, for the same reason

   Each sidecar's `backup-config` is left alone deliberately: the server URL and the
   client passwords are unchanged, so the stored connection reopens against the new
   repository — and `kopia-client.sh` already discards and remakes one that does not.

5. **Converge.** The override lands, `start.sh` takes the `filesystem` branch and
   **creates** the repository at `/pool/backups/devices/StorageBaby/kopia`, then
   registers every client. The exclude of § 3.1 did **not** land here; it was stage 2.
6. **First full snapshot.** Local disk, no network, ~500 GB. Immich's 323 GB is the long
   pole and the whole point. Verify with `kopia snapshot list --all` that
   `immich@storagebaby:/data/upload` exists — it never has.
7. **First sync**, with `sync_must_exist: false`. ~500 GB of upload. Then flip the key to
   `true` and converge.
8. **First snapraid sync** picks up the repository as ~500 GB of new files and writes
   parity for them. Expect a long run once. It is also stage 2's second gate: the sync
   that counts is one that _started after_ step 6 finished, otherwise parity covers a
   half-written repository.
9. **Delete the old bucket** (operator), once step 6 has verified a complete set.
10. **Stage 2** — the § 3.1 exclude, once 6 and 8 are both behind. Operator's call; the
    runbook has it.

A rollback after step 5 is a converge of the previous commit plus step 4 again; the old
bucket still holds the old repository until step 9, which is why step 9 is last.

## 5. Verification

- `snapraid sync` reports **zero soft errors under `/apps`** — and that is stage 2's
  criterion, not stage 1's. Stage 1 leaves `/apps` in the array, so the four known soft
  errors stay; what must not appear there is a fifth.

  Not "ends `exit:ok` with zero soft errors", which is what this said and is very
  likely unreachable once the repository is in the array: kopia's hourly maintenance
  deletes blobs while a multi-hour sync reads, and snapraid reports every file that
  vanished under it. Correctness is unaffected — a race on an append-and-delete
  repository can only ever mean _missing_, never _wrong_, which is § 2's pivot — but the
  exit code will not be clean. Removals under the repository path are expected and
  benign.

- `kopia snapshot list --all` lists a source for every `backup.paths` entry of every
  service that declares one — Immich's `upload` above all.
- `kopia maintenance info` shows full cycles with no `cap exceeded`.
- Backblaze's bucket report shows Class B downloads at effectively zero over a week, and
  Class C within the free allowance at 24 syncs a day.
- The nightly mail lists failed units; forcing one unit into failure makes it appear.
- `tests/static` passes, and the `test-ci` Molecule scenario converges with
  `sync: none` rendering the timer and doing nothing.

## 6. Known gaps this leaves

- `mergerfs.balance` still moves live application files under running containers. Only
  Jellyfin is stopped for it. Unchanged by this work, and worth its own look.
  **Resolved 2026-10-15, partly:** the nightly balance is removed, so this is only true
  for a balance the operator runs by hand — and then with every service up.
- Jellyfin's 32 GB is protected by nothing. Accepted; revisit with the mount restructure.
- The hourly interval is a starting point, to be judged against the Backblaze Class C
  figure after a week.
