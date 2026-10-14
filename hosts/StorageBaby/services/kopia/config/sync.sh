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
