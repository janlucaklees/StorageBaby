#!/bin/bash

#
# Settings
#
# The recipient and the sender come from `maintenance.env`, which the `storage` role
# renders from `storage.mail` in hosts/<host>/host.yml; the SMTP relay and its password
# live in /etc/msmtprc, which the same role renders. Nothing about the mail is baked into
# this script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/maintenance.env" ]]; then
	# shellcheck source=/dev/null
	source "${SCRIPT_DIR}/maintenance.env"
fi

EMAIL="${MAINTENANCE_MAIL_TO:?maintenance.env declares no MAINTENANCE_MAIL_TO}"
MAIL_FROM="${MAINTENANCE_MAIL_FROM:?maintenance.env declares no MAINTENANCE_MAIL_FROM}"
LOG_FILE_DIR="/var/log/storage-maintenance"
LOG_FILE_BASE_NAME="storage-maintenance"
LOG_FILE="${LOG_FILE_DIR}/${LOG_FILE_BASE_NAME}.log"

#
# Define functions
function log_with_timestamp() {
	while IFS= read -r line; do
		echo "[$(date '+%Y-%m-%d %H:%M:%S')] $line" | tee -a "$LOG_FILE"
	done
}

function send_email() {
	local subject="$1"
	local message="$2"

	# msmtp is named explicitly: the host runs no MTA of its own, so mutt's default
	# /usr/sbin/sendmail does not exist and a report would be lost with the run still
	# green. The envelope sender has to be set too -- the relay rejects anything else.
	echo -e "$message\n\nLog Content:\n$(cat "$LOG_FILE")" \
		| mutt -e 'set sendmail="/usr/bin/msmtp"' -e "set from=\"${MAIL_FROM}\"" \
			-s "$subject" -- "$EMAIL"
}

#
# Code

# Ensure log directory exists
mkdir -p "$LOG_FILE_DIR"

# Clear the log file
echo "" > $LOG_FILE

# Run the storage-maintenance script
{
	echo "=== Starting Storage Maintenance Job ==="
	echo "Log File: $LOG_FILE"

	# Execute the main script and log output with timestamps
	if bash "${SCRIPT_DIR}/storage-maintenance.sh"; then
		STATUS="SUCCESS"
		echo "SnapRAID Job completed successfully."
	else
		STATUS="FAILURE"
		echo "SnapRAID Job failed."
	fi

	echo
	echo
	echo "=== Job Finished with Status: $STATUS ==="

} | stdbuf -oL awk '!/\r/' | log_with_timestamp

STATUS=$(sed -n 's/\[.*\] === Job Finished with Status: \(.*\) ===/\1/p' "${LOG_FILE}")

send_email "[${STATUS}] SnapRAID Sync Report" "The SnapRAID job finished with status: ${STATUS}. Logs are attached."

exit 0
