# Kopia Local Repository Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Kopia's repository from Backblaze onto the pool, take `/pool/apps` out of the snapraid array, mirror the repository to Backblaze hourly, and report failed service units in the nightly maintenance mail.

**Architecture:** Kopia's `repository` backend flips from `s3` to `filesystem`; the host points that volume at `/pool/backups/devices/StorageBaby/kopia` through `volume_overrides`, which is outside `/apps` and therefore still in the array. A new `config/sync.sh` plus a timer pair in Kopia's own service folder runs `kopia repository sync-to s3` hourly. One service-agnostic script added to the storage role's maintenance run prints failed units from every service manager into the mail.

**Tech Stack:** Ansible, Jinja2 Quadlet/systemd templates, POSIX `sh` (the kopia image's shell is dash — **no `set -o pipefail`, no bashisms**), bash for the storage role's maintenance scripts, pytest for the static and Molecule suites.

**Spec:** `docs/superpowers/specs/2026-10-14-kopia-local-repository-design.md`

## Global Constraints

- **Never read, write, echo or decrypt a secret value.** `b2_key_id`, `b2_application_key` and `repository_password` are set by the operator. Tasks here change only which code _reads_ them.
- The kopia container's shell is **dash**. `config/sync.sh` is POSIX `sh`: no `pipefail`, no arrays, no `[[`.
- **Credentials never go through argv.** `kopia repository sync-to s3` reads `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` from the environment — use those, never `--access-key`/`--secret-access-key`.
- `hosts/test-a/services/kopia` and `hosts/test-ci/services/kopia` are **symlinks** to `hosts/StorageBaby/services/kopia`. Every change to that folder lands on all three hosts; test hosts are steered through `service_config` in their `host.yml` only.
- Bucket name: `GTMyXab9-jlkkb`. Endpoint: `s3.eu-central-003.backblazeb2.com`.
- Repository path on storagebaby: `/pool/backups/devices/StorageBaby/kopia`.
- Sync parallelism: `8`. The flag defaults to 1.
- The project's check command is `mise run test-static`. Never `--no-verify`.
- Every commit ends with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- Tasks 1–4 are code and config only. **No converge against storagebaby happens in this plan** — that is Task 5's runbook, executed by the operator.

---

### Task 1: `/pool/apps` leaves the snapraid array

**Files:**

- Modify: `hosts/StorageBaby/host.yml` (`storage.snapraid.excludes`)
- Test: `tests/static/test_storage.py`

**Interfaces:**

- Consumes: nothing.
- Produces: the invariant `kopia_repo_is_not_snapraid_excluded`, which Task 2 relies on being already present and passing once it adds the override.

- [ ] **Step 1: Write the failing test**

Append to `tests/static/test_storage.py`. It is deliberately generic — it asks "is any host's kopia repository inside an excluded tree?", so it holds for a second host and for a later change of either value:

```python
@pytest.mark.parametrize("host", STORAGE_HOSTS)
def test_the_kopia_repository_is_not_excluded_from_the_array(host):
    """The whole design rests on parity covering the kopia repository.

    `/apps` is excluded because live application state cannot be parityed; the
    repository is what makes that acceptable, so putting it under an excluded
    tree would silently remove the only protection `/apps` has left. Written
    against `volume_overrides` rather than a literal path: the path is a host's
    decision and may move, the invariant may not.

    Snapraid's excludes are relative to a *disk's* root, not to the pool mount --
    but every branch of this pool is a disk root, so a path under `/pool` is the
    same path under each branch. That is why comparing against `pool.mount` here
    is correct and not an approximation.
    """
    repo = host_cfg(host).get("volume_overrides", {}).get("kopia/repo")
    if repo is None:
        pytest.skip(f"{host} does not override kopia's repo volume")
    storage = storage_of(host)
    pool = storage["pool"]["mount"]
    assert repo.startswith(pool + "/"), f"{host}: {repo} is not under {pool}"
    relative = repo[len(pool) :]
    for exclude in storage["snapraid"]["excludes"]:
        # Only an anchored directory rule can swallow a whole tree; the glob rules
        # (`*.bak`, `*.log`) match single files and cannot hide the repository.
        if exclude.startswith("/") and exclude.endswith("/"):
            assert not relative.startswith(exclude), f"{host}: {repo} is under exclude {exclude}"
```

`STORAGE_HOSTS`, `host_cfg` and `storage_of` are already defined and imported at the top of that file; add nothing to the imports.

- [ ] **Step 2: Run it to see it pass vacuously**

```bash
mise run test-static
```

Expected: PASS, because `volume_overrides` is still `{}` on every host. This is the guard being put in place _before_ Task 2 can violate it — note it in the commit message so a later reader does not mistake it for a test that never fails.

- [ ] **Step 3: Add the exclude**

In `hosts/StorageBaby/host.yml`, under `storage.snapraid.excludes`, add `/apps/` and delete the now-dead nextcloud entry beneath it. Replace the existing comment above the list so it explains the new shape:

```yaml
snapraid:
  block_size: 256
  # Paths are relative to a *disk's* root, which for a branch of this pool is the
  # same as relative to /pool.
  #
  # `/apps/` is the whole of live application state, and it is excluded because
  # snapraid cannot parity a file that is rewritten while it reads it: the nightly
  # run ended in `exit:warning` on four soft errors every night, all of them a
  # SQLite or appdata file under a running service. What protects that tree instead
  # is the kopia repository, which lives at /pool/backups/devices/StorageBaby/kopia
  # -- outside this exclude, and therefore still covered by parity. A service there
  # without a `backup:` block (jellyfin, stirling-pdf) is covered by nothing, which
  # is a decision and not an oversight: see the design spec, section 3.1.
  excludes:
    - '*.bak'
    - '*.unrecoverable'
    - /tmp/
    - /lost+found/
    - /apps/
    - .AppleDouble
    # ... the rest unchanged, minus the Kannji line
```

Remove this line entirely — it is inside `/apps/` and now dead:

```yaml
- /apps/nextcloud/data/jlk/files/Projects/Kannji/_app_workspace/app/build/
```

- [ ] **Step 4: Verify the rendered snapraid.conf carries it**

```bash
mise run test-static
```

Expected: PASS. `test_storage.py` already asserts `f"exclude {exclude}" in conf` for every entry, so the new line is covered by the existing render test.

- [ ] **Step 5: Commit**

```bash
git add hosts/StorageBaby/host.yml tests/static/test_storage.py
git commit -m "$(
	cat << 'EOF'
feat: exclude /pool/apps from the snapraid array

Live application state cannot be parityed on this host: there is no
snapshotting filesystem to hand snapraid a frozen view, so the nightly sync
ended in exit:warning on four soft errors every night -- paperless's
celerybeat schedule and nextcloud's richdocuments appdata, both rewritten in
place under a running service. Excluding /apps/ removes exactly those.

What replaces parity for that tree is the kopia repository, which moves onto
the pool in the next commit and stays inside the array. The test added here
is the guard for that: it fails if a host ever points kopia/repo at a path
under an excluded tree, which would leave /apps protected by nothing without
anything visibly breaking. It passes vacuously today and starts doing work
once the override lands.

The nextcloud Kannji exclude is removed as dead -- it sits inside /apps.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: The repository moves onto the pool

**Files:**

- Modify: `hosts/StorageBaby/services/kopia/service.yml`
- Modify: `hosts/StorageBaby/host.yml` (`volume_overrides`)
- Modify: `hosts/test-a/host.yml`, `hosts/test-ci/host.yml` (`service_config.kopia`)
- Modify: `hosts/StorageBaby/services/kopia/README.md`
- Test: `tests/static/test_storage.py` (the Task 1 test now does real work)

**Interfaces:**

- Consumes: `test_a_hosts_kopia_repository_is_not_excluded_from_the_array` from Task 1.
- Produces: `service.config.repository == "filesystem"` as the default on every host; `volumes.repo` is class `pool`; `volume_overrides["kopia/repo"]` exists on storagebaby. Task 3 reads `service.config.s3_endpoint` and `service.config.s3_bucket`, which keep their names here and change meaning.

- [ ] **Step 1: Flip the backend and the volume class**

In `hosts/StorageBaby/services/kopia/service.yml`:

```yaml
volumes:
  config: { class: pool }
  cache: { class: fast }
  logs: { class: fast }
  # The repository itself, and the reason this is `pool` and not `fast`: it was an
  # empty stub while the repository lived in Backblaze, and it is now the primary
  # copy of every backup on this host. storagebaby overrides the path to
  # /pool/backups/devices/StorageBaby/kopia; a test host gets its pool root.
  repo: { class: pool }
config:
  server_username: jlk
  # `filesystem` on every host. The repository lives on local storage and Backblaze
  # is a replica fed by config/sync.sh -- which is what took kopia's daily garbage
  # collection off the metered link. `s3` is still accepted by start.sh and is what
  # this said until 2026-10-14; see the service README.
  repository: filesystem
```

- [ ] **Step 2: Point the host at the operator's path**

In `hosts/StorageBaby/host.yml`, replace `volume_overrides: {}`:

```yaml
# The kopia repository, deliberately outside `storage_roots.pool` (= /pool/apps):
# /apps is excluded from the snapraid array and the repository must not be. The
# path follows the operator's existing structure -- /pool/backups/devices/ already
# holds CloudBaby, KAIZN-vps and StorageBaby. A filesystem path on this machine is
# a host fact, which is why it is here and not in the service folder
# (docs/ownership.md section 4).
volume_overrides:
  kopia/repo: /pool/backups/devices/StorageBaby/kopia
```

- [ ] **Step 3: Leave the test-host overrides alone for now**

`hosts/test-a/host.yml` and `hosts/test-ci/host.yml` still carry
`service_config.kopia.repository: filesystem`. It is now redundant — it overrides the
default to the default — but harmless, and Task 3 step 7 replaces the whole block with
`sync: none`. Editing those two lines twice across two commits buys nothing, so this
task does not touch them.

- [ ] **Step 4: Run the tests**

```bash
mise run test-static
```

Expected: PASS, and the Task 1 test is now doing real work — `/pool/backups/devices/StorageBaby/kopia` is under `/pool` and matches no `/…/` exclude.

- [ ] **Step 5: Prove the guard actually bites**

Temporarily change the override to `/pool/apps/kopia/repo` and re-run:

```bash
mise run test-static
```

Expected: FAIL with `… is under exclude /apps/`. Revert the override to the real path and re-run to PASS. Do not commit the temporary value.

- [ ] **Step 6: Update the service README**

In `hosts/StorageBaby/services/kopia/README.md`, rewrite "The repository connection is declared, not clicked" and "Volumes":

- The worked example's `repository:` is `filesystem`, not `s3`.
- Delete the "Connect, then create — and what that means for a live bucket" subsection's S3 half and the two placeholder warnings about `s3_endpoint`/`s3_bucket` being unverified; they are now the _sync target_, documented in Task 3.
- `repository.config` no longer holds Backblaze credentials. Say so explicitly and remove the paragraph that warns the `config` volume is not a directory to copy off the host casually — that reason is gone.
- The volumes table: `repo` is `pool`, is the repository on every host, and on storagebaby is overridden to `/pool/backups/devices/StorageBaby/kopia`.
- Keep the `cache` warning at the end intact. It is about to matter more than it ever has (Task 5, cutover step 4).

- [ ] **Step 7: Commit**

```bash
git add hosts/StorageBaby/services/kopia/service.yml hosts/StorageBaby/host.yml \
	hosts/test-a/host.yml hosts/test-ci/host.yml \
	hosts/StorageBaby/services/kopia/README.md
git commit -m "$(
	cat << 'EOF'
feat: kopia's repository moves onto the pool

Kopia's daily full maintenance has to read pack blobs back out of the
repository -- to rewrite packs that are only partly dead, and to recover
contents it had marked deleted that turn out to still be referenced. With the
repository in Backblaze that is a download, and it blew the 1 GB daily cap on
five of the eight mornings before this change. The visible damage was not a
failed cron job: immich's 323 GB photo library has never completed a first
snapshot and was not backed up at all.

With the repository on local disk that work costs disk I/O and nothing else.
Backblaze becomes a replica, fed by the sync added in the next commit.

`filesystem` is now the default on every host rather than a test-host
override, so the two test hosts drop theirs. The repo volume moves from class
`fast` to `pool`: it was an empty stub and is now the primary copy of every
backup on this host. The path itself is a host fact and sits in host.yml, and
it is deliberately outside storage_roots.pool -- /pool/apps left the snapraid
array in the previous commit and the repository must not follow it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Hourly `sync-to` Backblaze

**Files:**

- Create: `hosts/StorageBaby/services/kopia/config/sync.sh`
- Create: `hosts/StorageBaby/services/kopia/quadlet/kopia-sync.timer.j2`
- Create: `hosts/StorageBaby/services/kopia/quadlet/kopia-sync.service.j2`
- Modify: `hosts/StorageBaby/services/kopia/quadlet/kopia.container.j2`
- Modify: `hosts/StorageBaby/services/kopia/service.yml`
- Modify: `hosts/test-a/host.yml`, `hosts/test-ci/host.yml`
- Modify: `hosts/StorageBaby/services/kopia/README.md`
- Test: `tests/static/test_quadlet_conventions.py`, `tests/integration/molecule/test-ci/tests/test_kopia.py`

**Interfaces:**

- Consumes: `service.config.s3_endpoint`, `service.config.s3_bucket` (Task 2 kept the names); the `b2_key_id` and `b2_application_key` podman secrets, already `Secret=` lines on the container.
- Produces: `config.sync` (`s3` | `none`), `config.sync_must_exist` (bool), the env vars `KOPIA_SYNC`, `KOPIA_SYNC_MUST_EXIST` on the kopia container, and `/etc/storagebaby/kopia/sync.sh` inside it.

- [ ] **Step 1: Write the failing static test**

Append to `tests/static/test_quadlet_conventions.py`:

```python
def test_every_config_script_a_unit_execs_is_mounted_into_the_container():
    """kopia.container.j2 mounts start.sh as a single file, not the directory.

    A second script in config/ is therefore invisible inside the container
    unless it gets a Volume= line of its own -- and the failure is a `podman
    exec` that cannot find the file, an hour after the deploy, in a service
    user's journal nobody reads. Checked statically because that is the only
    place it is cheap to catch.
    """
    unit = (REPO / "hosts/StorageBaby/services/kopia/quadlet/kopia.container.j2").read_text()
    for script in ("start.sh", "sync.sh"):
        needle = "{{ config_dir }}/" + script + ":{{ config_dir }}/" + script + ":ro"
        assert needle in unit, f"kopia.container.j2 does not mount {script}"
```

Use whatever the file's existing import of the repo root is called (`REPO` from `conftest`); match it rather than adding a second import style.

- [ ] **Step 2: Run it to verify it fails**

```bash
mise run test-static
```

Expected: FAIL — `kopia.container.j2 does not mount sync.sh`.

- [ ] **Step 3: Write `config/sync.sh`**

```sh
#!/bin/sh
# Push the local repository to its off-site replica. Run by kopia-sync.timer via
# `podman exec`, so it inherits the container's environment -- which is where
# KOPIA_PASSWORD, KOPIA_CONFIG_PATH and the three KOPIA_SYNC*/KOPIA_S3_* values
# below come from, and why this script sets none of them.
#
# dash, not bash: the kopia image's /bin/sh answers `Illegal option -o pipefail`.
set -eu

# A host with no replica configured -- every test host -- renders this unit like
# any other and must do nothing with it. Exiting 0 rather than refusing to install
# the timer keeps the service folder identical on all three hosts, which is what
# the symlinks under hosts/test-*/services require.
if [ "${KOPIA_SYNC:-none}" = "none" ]; then
	echo "kopia: sync is disabled on this host (KOPIA_SYNC=none)"
	exit 0
fi

if [ "$KOPIA_SYNC" != "s3" ]; then
	echo "kopia: unknown KOPIA_SYNC: $KOPIA_SYNC" >&2
	exit 2
fi

# Through the environment and never as flags. kopia reads both of these itself
# (`--access-key` / `--secret-access-key` document them as the fallback), and a
# credential in argv is readable in the container's `ps` and in the host's
# /proc/<pid>/cmdline -- the exposure `kopia server users add` could not avoid
# because it has no envar form, and this one can.
#
# Each read is its own assignment so `set -e` aborts on a missing secret: an
# assignment carries the command substitution's status, `export x="$(...)"`
# carries export's, which is always 0.
AWS_ACCESS_KEY_ID="$(cat /run/secrets/b2_key_id)"
export AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY="$(cat /run/secrets/b2_application_key)"
export AWS_SECRET_ACCESS_KEY

# --delete: without it the replica keeps every blob the local repository's
#   garbage collection has already dropped, and grows forever while the source
#   stays trim. It only does what it says if the bucket's lifecycle rules allow a
#   delete to actually remove the object -- Backblaze keeps all versions by
#   default. See the README.
# --parallel: the flag defaults to 1, which would make the first ~500 GB push
#   take days.
# --must-exist: off for the very first sync, because the destination has no
#   repository format blob yet; on from then on, so a mistyped bucket fails loudly
#   instead of quietly starting a second, empty repository. Driven by a config key
#   so the switch is one line of git and not an edit on the host.
#
#   **Defaults to off, on both sides.** A key left out means "no guard", not
#   "guard on" -- deliberately: the other way round reads safer and would make the
#   very first sync of any new host fail against its own empty bucket. The guard
#   is a hardening step taken once the replica exists, never a prerequisite for
#   creating it.
set -- kopia repository sync-to s3 \
	--bucket="$KOPIA_S3_BUCKET" \
	--endpoint="$KOPIA_S3_ENDPOINT" \
	--delete \
	--parallel=8
if [ "${KOPIA_SYNC_MUST_EXIST:-false}" = "true" ]; then
	set -- "$@" --must-exist
fi

echo "kopia: syncing the repository to $KOPIA_S3_BUCKET"
"$@"
```

- [ ] **Step 4: Mount it and give the container the three new values**

In `hosts/StorageBaby/services/kopia/quadlet/kopia.container.j2`, beside the existing `start.sh` mount:

```ini
Volume={{ config_dir }}/sync.sh:{{ config_dir }}/sync.sh:ro
```

And beside the existing `KOPIA_S3_*` lines, replacing their comment:

```ini
{# Where the off-site replica goes, and the two values that used to say where the
   repository *was*. The repository is local since 2026-10-14; these are read by
   config/sync.sh and by nothing else. `KOPIA_SYNC=none` on a host with no
   Backblaze account makes the hourly unit a no-op. #}
Environment=KOPIA_SYNC={{ service.config.sync | default('none') }}
Environment=KOPIA_SYNC_MUST_EXIST={{ service.config.sync_must_exist | default(false) | lower }}
Environment=KOPIA_S3_ENDPOINT={{ service.config.s3_endpoint | default('') }}
Environment=KOPIA_S3_BUCKET={{ service.config.s3_bucket | default('') }}
```

`| lower` because Jinja renders a YAML boolean as `True`/`False` and the script compares against `true`.

- [ ] **Step 5: Write the timer pair**

`hosts/StorageBaby/services/kopia/quadlet/kopia-sync.timer.j2`:

```ini
[Unit]
Description=Hourly push of the kopia repository to its off-site replica

[Timer]
OnCalendar=hourly
Persistent=true
{# Not on the hour exactly: the four database dumps and the snapshot schedules are
   all on round times, and a sync that starts mid-snapshot copies a set of blobs it
   will have to revisit anyway. Also keeps the first request of each hour off a
   shared minute at Backblaze. #}
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

`hosts/StorageBaby/services/kopia/quadlet/kopia-sync.service.j2`:

```ini
[Unit]
Description=Push the kopia repository to its off-site replica
{# Requisite= and not Requires=, for the reason the dump units give: `podman exec`
   into a container that is not running fails anyway, and refusing to run is a
   louder, truer failure than starting the server to satisfy a dependency. #}
After=kopia.service
Requisite=kopia.service

[Service]
Type=oneshot
ExecStart=/usr/bin/podman exec kopia {{ config_dir }}/sync.sh
```

- [ ] **Step 6: Declare the config keys**

In `hosts/StorageBaby/services/kopia/service.yml`, in the `config:` block:

```yaml
# Where the off-site replica goes. These two keys kept their names through the
# 2026-10-14 change and changed meaning: they said where the repository *was*,
# they now say where it is *copied to*.
s3_endpoint: s3.eu-central-003.backblazeb2.com
s3_bucket: GTMyXab9-jlkkb
# `s3` or `none`. A host without a Backblaze account sets `none` in its own
# `service_config` and the hourly unit becomes a no-op.
sync: s3
# False for the very first sync into a fresh bucket, which has no repository
# format blob yet; true from then on, so a mistyped bucket fails loudly instead
# of quietly starting a second, empty repository. Flip it after the first run.
# It defaults to false where it is read, so a host that omits it gets no guard
# rather than a first sync that cannot succeed.
sync_must_exist: false
```

- [ ] **Step 7: Turn it off on the test hosts**

In both `hosts/test-a/host.yml` and `hosts/test-ci/host.yml`:

```yaml
# A test host has no Backblaze account, so the hourly replica push is a no-op --
# the unit is still rendered and enabled, which is what keeps the path covered.
service_config:
  kopia:
    sync: none
```

- [ ] **Step 8: Write the integration test**

Append to `tests/integration/molecule/test-ci/tests/test_kopia.py`. The file already defines `kopia_case` and imports `run_as` and `placed`; the parametrised signature is `(host, owner, spec_path)` and every case calls `placed(host, owner)` first. `run_as(host, user, cmd)` runs `cmd` as a client of that user's running systemd manager, which is what both commands below need:

```python
@kopia_case
def test_the_sync_unit_is_installed_and_is_a_no_op_without_a_replica(host, owner, spec_path):
    """The timer is rendered and enabled on every host, including one with no
    Backblaze account -- that is what keeps this path covered by CI at all. On
    such a host sync.sh must exit 0 and do nothing; anything else turns an
    hourly unit into an hourly failure in a journal nobody reads.
    """
    placed(host, owner)

    enabled = run_as(host, "svc-kopia", "systemctl --user is-enabled kopia-sync.timer")
    assert enabled.stdout.strip() == "enabled", enabled.stdout + enabled.stderr

    run = run_as(host, "svc-kopia", "podman exec kopia /etc/storagebaby/kopia/sync.sh")
    assert run.rc == 0, run.stdout + run.stderr
    assert "sync is disabled on this host" in run.stdout, run.stdout
```

- [ ] **Step 9: Run both suites**

```bash
mise run test-static
MOLECULE_HOST=test-ci mise run test-integration
```

Expected: both PASS. The static suite now finds the `sync.sh` mount; the Molecule run proves the timer installs and the script no-ops.

- [ ] **Step 10: Document it**

In `hosts/StorageBaby/services/kopia/README.md`, add a section "The off-site replica" covering: what `sync-to` produces (a complete, connectable repository — same password, nothing to restore before reading it); the three config keys and the `sync_must_exist` flip; that credentials go through the environment and why; `--delete` and **the Backblaze lifecycle caveat** — a default bucket keeps all versions, so a delete only hides the blob and the bytes keep being billed, which defeats the flag entirely; and that Class C list transactions are the cost to watch, with the agreed fallback being a longer interval.

- [ ] **Step 11: Commit**

```bash
git add hosts/StorageBaby/services/kopia/ hosts/test-a/host.yml hosts/test-ci/host.yml \
	tests/static/test_quadlet_conventions.py \
	tests/integration/molecule/test-ci/tests/test_kopia.py
git commit -m "$(
	cat << 'EOF'
feat: hourly push of the kopia repository to Backblaze

The repository is local since the previous commit, so Backblaze needs feeding.
`kopia repository sync-to` does it natively and produces a complete,
connectable repository rather than an archive -- point kopia at the bucket
with the same password and everything is there.

Hourly rather than nightly because the interval is now just an answer to "how
much is lost if the house burns down", and the cost is a listing plus whatever
blobs are new. Uploads are free on ingress; the number to watch is Class C
transactions, and the agreed fallback is a longer interval.

Three details are load-bearing and all three are commented where they live.
--delete, or the replica keeps every blob local garbage collection already
dropped and grows forever. --parallel=8, because the flag defaults to 1 and
the first push is ~500 GB. --must-exist off for the first run only, so a
mistyped bucket fails loudly instead of quietly starting a second empty
repository.

Credentials reach kopia through AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY and
never through argv -- the exposure `server users add` could not avoid, because
it has no envar form, and this one can.

The static test guards the thing that would otherwise fail silently an hour
after a deploy: kopia.container.j2 mounts config scripts one file at a time,
so a new script with no Volume= line is simply not there.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Failed service units reach the nightly mail

**Files:**

- Create: `ansible/roles/storage/files/maintenance/report_failed_units.sh`
- Modify: `ansible/roles/storage/files/maintenance/storage-maintenance.sh`
- Modify: `ansible/roles/storage/tasks/maintenance.yml`
- Modify: `ansible/roles/storage/README.md`
- Test: `tests/integration/molecule/test-ci/tests/test_storage.py`

**Interfaces:**

- Consumes: nothing from earlier tasks. Deliberately knows nothing about kopia.
- Produces: `/opt/storagebaby/maintenance/report_failed_units.sh`, 0755, run by the orchestrator between the SMART report and the `on-finish` hook.

- [ ] **Step 1: Write the failing integration test**

Append to `tests/integration/molecule/test-ci/tests/test_storage.py`:

```python
def test_the_failed_unit_report_is_installed_and_runs(host):
    """Service units live in each service user's own systemd, which has no path to
    the host's mail relay (/etc/msmtprc is 0600 root). Without this step an hourly
    kopia sync -- or any failed dump, or a crashed pod -- fails into a journal
    nobody reads. Root can reach those managers; that is what storagebaby-svc does.
    """
    script = host.file("/opt/storagebaby/maintenance/report_failed_units.sh")
    assert script.exists and script.mode == 0o755

    run = host.run("bash /opt/storagebaby/maintenance/report_failed_units.sh")
    assert run.rc == 0, run.stdout + run.stderr
    # It reports; it never fails the run. A healthy VM has no failed units.
    assert "svc-" in run.stdout or "No failed units" in run.stdout, run.stdout
```

- [ ] **Step 2: Run it to verify it fails**

```bash
MOLECULE_HOST=test-ci mise run test-integration
```

Expected: FAIL — the file does not exist.

- [ ] **Step 3: Write the script**

`ansible/roles/storage/files/maintenance/report_failed_units.sh`:

```bash
#!/bin/bash
# Ask every service manager on this host for its failed units.
#
# Why this exists: the service role installs units into `svc-<name>`'s own systemd,
# and that manager has no way to send mail -- /etc/msmtprc is 0600 root. So a unit
# that fails there (an hourly kopia sync, a nightly database dump, a crashed pod)
# writes one line into a journal nobody reads. Root can reach those managers, which
# is exactly what /usr/local/sbin/storagebaby-svc does, so the nightly mail is the
# right place to surface them.
#
# It knows nothing about any particular service, on purpose: the next service that
# fails at 04:00 is covered without anybody editing this role again.
#
# It reports. It never fails the run -- a failed unit somewhere is news, not a
# reason to abort parity -- so every command here is tolerant and the script exits 0.

found=0

for user in $(getent passwd | awk -F: '$1 ~ /^svc-/ { print $1 }' | sort); do
	# `|| true` and a discarded stderr: a service user whose manager is not running
	# (linger off, user mid-removal) is not an error worth failing a nightly run for.
	units="$(systemctl --user -M "${user}@" list-units --state=failed --no-legend --plain 2> /dev/null || true)"

	if [[ -n "$units" ]]; then
		found=1
		echo "${user}:"
		echo "${units}" | sed 's/^/  /'
	fi
done

if [[ "$found" -eq 0 ]]; then
	echo "No failed units in any service manager."
fi

exit 0
```

- [ ] **Step 4: Call it from the orchestrator**

In `ansible/roles/storage/files/maintenance/storage-maintenance.sh`, between `print_snapraid_smart` and `run_hook "on-finish"`:

```bash
echo_section "Failed service units"
echo

# Not wrapped in `fail`: this step reports, it does not gate. A failed unit in a
# service manager is news for the mail, not a reason to abort a run whose parity
# work has already succeeded.
bash "${SCRIPT_DIR}/report_failed_units.sh"
```

- [ ] **Step 5: Install it**

In `ansible/roles/storage/tasks/maintenance.yml`, add `report_failed_units.sh` to the `loop:` of the "The maintenance scripts" copy task, after `balance_disks.sh`.

- [ ] **Step 6: Run the tests**

```bash
mise run test-static
MOLECULE_HOST=test-ci mise run test-integration
```

Expected: both PASS.

- [ ] **Step 7: Document it**

In `ansible/roles/storage/README.md`, in the section listing what the role installs into `/opt/storagebaby/maintenance`, change "six scripts" to seven and add `report_failed_units.sh` with one line on what it does. Add it as step 9 of the orchestrator's step list, before the `on-finish` hook. Also update the same count in the root `CLAUDE.md` under "Storage maintenance pipeline" — it says "six scripts at 0755" and names them.

- [ ] **Step 8: Commit**

```bash
git add ansible/roles/storage/ CLAUDE.md \
	tests/integration/molecule/test-ci/tests/test_storage.py
git commit -m "$(
	cat << 'EOF'
feat: report failed service units in the nightly maintenance mail

The service role installs units into each service user's own systemd, and that
manager has no path to the host's mail relay -- /etc/msmtprc is 0600 root. So
anything that fails there writes one line into a journal nobody reads. The
hourly kopia sync added in the previous commit is the immediate case, and an
off-site copy nobody notices failing is the failure mode that matters, but the
nightly database dumps and every pod have had the same gap all along.

Root can already reach those managers -- that is how storagebaby-svc works --
so the step asks each of them for its failed units and prints the list into
the mail that already arrives every morning.

Deliberately service-agnostic: it discovers svc-* users from passwd and names
nothing. The next service that fails at 04:00 is covered without anybody
editing this role again, which is the only thing that justifies putting a step
here rather than in a service folder.

It reports and never gates. A failed unit somewhere is news, not a reason to
abort a run whose parity work has already succeeded.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: The cutover runbook, and the docs that are now wrong

**Files:**

- Create: `docs/runbooks/2026-10-14-kopia-local-repository-cutover.md`
- Modify: `CLAUDE.md`
- Modify: `docs/ownership.md` (section 7, known drift)

**Interfaces:**

- Consumes: everything from Tasks 1–4.
- Produces: the operator-facing procedure. **Nothing in this task converges a host.**

- [ ] **Step 1: Write the runbook**

Create `docs/runbooks/2026-10-14-kopia-local-repository-cutover.md` from section 4 of the spec, verbatim in substance, as a numbered checklist the operator works through. It must carry, in this order and with the reasoning intact:

1. Free the space — delete the stale trees under `/pool/backups/devices/CloudBaby` (532 GB) and `/pool/jlk`. The pool is at 87% and the new repository reaches ~500 GB once immich is really in it.
2. Operator sets the bucket credentials. **And checks the bucket's lifecycle rules**: Backblaze keeps all versions by default, so `--delete` would only hide a blob and the bytes keep being billed. Set it to keep only the last version, before the 500 GB first push.
3. Optionally rotate `repository_password` — not required, a fresh repository derives fresh keys from whatever it is given. Either way, **back the value up outside this repository before anything is written**; there is no reset and no escrow.
4. **Stop kopia and every client pod, then wipe, _before_ the converge.** This step cannot move: `start.sh` connects only when `repository.config` is absent, and that file exists today pointing at the old bucket — converge first and kopia restarts straight back onto Backblaze, the `filesystem` branch never runs, and the change looks like it did nothing. Wipe kopia's `config` volume, kopia's `cache` volume, and every service's `backup-cache` volume. A cache from a different repository fails as `cipher: message authentication failed`, which reads like a wrong password and is not one. Leave each sidecar's `backup-config` alone: the server URL and client passwords are unchanged, and `kopia-client.sh` discards a stored connection that does not open.
5. Converge. `start.sh` takes the `filesystem` branch, **creates** the repository at `/pool/backups/devices/StorageBaby/kopia`, registers every client.
6. First full snapshot — local disk, no network, ~500 GB. Verify `kopia snapshot list --all` shows `immich@storagebaby:/data/upload`. It never has.
7. First sync with `sync_must_exist: false`, then set it to `true` and converge.
8. First snapraid sync picks up ~500 GB of new files. One long run.
9. Delete the old bucket, once step 6 verified a complete set.

Add the verification list from spec section 5 at the end, and a rollback line: after step 5, converge the previous commit and repeat step 4; the old bucket still holds the old repository until step 9, which is why step 9 is last.

- [ ] **Step 2: Fix the statements in CLAUDE.md that this change falsified**

Search `CLAUDE.md` for each and correct it:

- "That is how the test host gives kopia a filesystem repository while storagebaby uses S3" — both are `filesystem` now; the test host's override is `sync: none`.
- "Storage architecture" — the pool layout block lists `/pool/apps/<service>/<volume>` without saying it is outside the array. Add `/pool/backups/devices/StorageBaby/kopia` to the block and one line that `/apps` is excluded.
- "`volume_overrides: {}`" appears in the service-folder description as the empty default; storagebaby now has one entry. Mention `kopia/repo` as the worked example.
- "six scripts at 0755" under "Storage maintenance pipeline" — seven, done in Task 4 step 7; verify it landed.
- The orchestrator's numbered step list — add the failed-unit report before `on-finish`.

- [ ] **Step 3: Record what is still drifting**

In `docs/ownership.md` section 7, add the two gaps this change knowingly leaves:

- `mergerfs.balance` still moves live application files between branches under running containers; only jellyfin is stopped for it. Pre-existing, untouched here, worth its own look.
- Jellyfin's 32 GB of metadata is in `/pool/apps` with no `backup:` block, so after this change it is protected by neither parity nor kopia. Accepted; revisit with the mount restructure.

- [ ] **Step 4: Run the static suite**

```bash
mise run test-static
```

Expected: PASS. Docs-only, but `fmt-check` and the lint test cover markdown.

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/2026-10-14-kopia-local-repository-cutover.md CLAUDE.md docs/ownership.md
git commit -m "$(
	cat << 'EOF'
docs: cutover runbook for the local kopia repository

The code change is inert until an operator runs this, and one step in it is
load-bearing in a way that is not obvious from the diff: start.sh connects
only when its stored connection is absent, so the old connection and every
cache belonging to the old repository have to be wiped *before* the converge.
Converge first and kopia restarts straight back onto Backblaze while the
change appears to have done nothing.

Also carries the Backblaze lifecycle check -- a default bucket keeps all
versions, so --delete only hides a blob and the bytes keep being billed, which
defeats the flag the design relies on.

CLAUDE.md said storagebaby's kopia uses S3 and the test host overrides to
filesystem; both are filesystem now and the override is `sync: none`. The
storage layout block gained the repository path and the note that /apps is
outside the array.

The two gaps this change knowingly leaves are recorded as drift rather than
quietly accepted: mergerfs.balance still moves live files under running
containers, and jellyfin's metadata is now protected by nothing.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```
