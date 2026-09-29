#!/bin/bash

#
# The one mail the maintenance run itself cannot send.
#
# storage-maintenance.service `Requires=` the pool, and that dependency has to stay: a
# `snapraid sync` over a branch that is not mounted reads an empty directory and writes
# that into parity. But `Requires=` also means that a pool which cannot come up makes the
# unit fail *to start* -- ExecStart= never runs, so the unattended wrapper never runs, so
# the nightly report simply does not arrive. Silence is the worst possible report for the
# one failure that matters most, so `OnFailure=` hands that case to this script instead.
#
# Everything about the mail comes from `maintenance.env` and /etc/msmtprc, exactly as in
# the wrapper: no address, relay or credential is baked in here.
#
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/maintenance.env" ]]; then
	# shellcheck source=/dev/null
	source "${SCRIPT_DIR}/maintenance.env"
fi

EMAIL="${MAINTENANCE_MAIL_TO:?maintenance.env declares no MAINTENANCE_MAIL_TO}"
MAIL_FROM="${MAINTENANCE_MAIL_FROM:?maintenance.env declares no MAINTENANCE_MAIL_FROM}"

# The failed unit is named by the unit file rather than guessed, so this stays usable for
# anything else that wants to report a run it could not start.
UNIT="${1:-storage-maintenance.service}"

# No `exit 0` at the end: the exit status is mutt's, so a notification that could not be
# sent is itself a failed unit in the journal instead of a second silence.
#
# The lines below are kept short on purpose. mutt encodes a body with long lines as
# quoted-printable and soft-wraps it around column 76, and the integration test reads the
# message out of the sink as it was sent.
{
	echo "${UNIT} could not start."
	echo
	echo "So the storage maintenance did not run: no balance, no sync, no"
	echo "scrub, and the array's parity is not up to date."
	echo
	echo "The usual cause is a branch or the parity disk that is not"
	echo "mounted. The unit Requires= the pool on purpose -- a sync over an"
	echo "unmounted branch writes an empty disk into parity."
	echo
	echo "systemctl status ${UNIT}:"
	echo
	systemctl status --full --no-pager --lines=50 -- "$UNIT" 2>&1
} | mutt -e 'set sendmail="/usr/bin/msmtp"' -e "set from=\"${MAIL_FROM}\"" \
	-s "[FAILURE] Storage maintenance could not start" -- "$EMAIL"
