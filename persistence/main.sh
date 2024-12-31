#!/bin/bash

#
# Settings
EMAIL='email@janlucaklees.de'
LOG_FILE_DIR="/var/log/persistence"
LOG_FILE_BASE_NAME="persistence"
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

    # Send the email with the attachments that exist
    echo -e "$message\n\nLog Content:\n$(cat "$LOG_FILE")" | mutt -s "$subject" -- "$EMAIL"
}

#
# Code

# Ensure log directory exists
mkdir -p "$LOG_FILE_DIR"

# Clear the log file
echo "" > $LOG_FILE

# Run the persistence script
{
    echo "=== Starting Persistence Job ==="
    echo "Log File: $LOG_FILE"

    # Execute the main script and log output with timestamps
    if bash "./persistence.sh"; then
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
