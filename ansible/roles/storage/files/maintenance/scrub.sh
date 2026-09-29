#!/bin/bash

#
# Settings
#
# The plan and the age come from `maintenance.env`, which the `storage` role renders from
# `storage.snapraid.maintenance` in hosts/<host>/host.yml. Sourced rather than rendered
# into this file: a parameter is changed in git and converged, never by editing a script,
# and a hand-run `bash /opt/storagebaby/maintenance/scrub.sh` then behaves exactly like
# the timer's run. The fallbacks are today's deployed values, so the script is still
# correct on its own.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/maintenance.env" ]]; then
	# shellcheck source=/dev/null
	source "${SCRIPT_DIR}/maintenance.env"
fi

SCRUB_PERCENT="${SCRUB_PERCENT:-8}"
SCRUB_OLDER_DAYS="${SCRUB_OLDER_DAYS:-12}"

# Ensure log directory exists
mkdir -p /var/log/snapraid

# Runs the scrub.
snapraid scrub --plan new --log /var/log/snapraid/snapraid-scrub-new.log
snapraid scrub --plan "$SCRUB_PERCENT" --older-than "$SCRUB_OLDER_DAYS" \
	--log /var/log/snapraid/snapraid-scrub.log
