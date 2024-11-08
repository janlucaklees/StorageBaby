#!/bin/bash

# Constants
# Settings
EMAIL='email@janlucaklees.de'
SYSTEMD_SERVICES='smb nmb syncthing@jlk'
# Thesholds (-1 means no limit)
THRESHOLD_DELETE=0
THRESHOLD_ADD=-1
THRESHOLD_MOVE=-1
THRESHOLD_COPY=-1
THRESHOLD_UPDATE=-1
BALANCE_TARGET=3
# Log File Locations
# TODO: Clean up logs after a while.
LOG_FILE_DIR="/var/log/snapraid-sync"
LOG_FILE_BASE_NAME="snapraid_sync"
LOG_FILE="${LOG_FILE_DIR}/${LOG_FILE_BASE_NAME}.log"
LOG_FILE_DIFF="${LOG_FILE_DIR}/${LOG_FILE_BASE_NAME}_diff.log"
LOG_FILE_BALANCE="${LOG_FILE_DIR}/${LOG_FILE_BASE_NAME}_balace.log"

function main() {
    log "SnapRAID Sync Job started"
    log ""

    print_status "Initial Status"
    log ""

    # TODO: Adapt Scrub Service to match

    log "Stopping Services..."
    stop_services
    log ""

    log "Checking Diff..."
    snapraid diff &>> "$LOG_FILE_DIFF"

    # Get some statistics from the diff.
    COUNT_DELETE=$(grep -w '^ \{1,\}[0-9]* removed' "$LOG_FILE_DIFF" | sed 's/^ *//g' | cut -d ' ' -f1)
    COUNT_ADD=$(grep -w '^ \{1,\}[0-9]* added' "$LOG_FILE_DIFF" | sed 's/^ *//g' | cut -d ' ' -f1)
    COUNT_MOVE=$(grep -w '^ \{1,\}[0-9]* moved' "$LOG_FILE_DIFF" | sed 's/^ *//g' | cut -d ' ' -f1)
    COUNT_COPY=$(grep -w '^ \{1,\}[0-9]* copied' "$LOG_FILE_DIFF" | sed 's/^ *//g' | cut -d ' ' -f1)
    COUNT_UPDATE=$(grep -w '^ \{1,\}[0-9]* updated' "$LOG_FILE_DIFF" | sed 's/^ *//g' | cut -d ' ' -f1)

    # Display summary in any case, helps with debugging, when values were not extracted correctly.
    # TODO: Show warnings.
    log "Deleted: $COUNT_DELETE (Threshold: $THRESHOLD_DELETE)"
    log "Added:   $COUNT_ADD (Threshold: $THRESHOLD_ADD)"
    log "Moved:   $COUNT_MOVE (Threshold: $THRESHOLD_MOVE)"
    log "Copied:  $COUNT_COPY (Threshold: $THRESHOLD_COPY)"
    log "Updated: $COUNT_UPDATE (Threshold: $THRESHOLD_UPDATE)"
    log ""

    # Make sure we could extract all values correctly.
    if [ -z "$COUNT_DELETE" -o -z "$COUNT_ADD" -o -z "$COUNT_MOVE" -o -z "$COUNT_COPY" -o -z "$COUNT_UPDATE" ]; then
        log "**ERROR** - Failed to get one or more values from the diff. Unable to proceed."
        send_email "[Aborted] SnapRAID Sync Report" "The sync was aborted as statistics could not be extracted from the diff commands output."
        restore_services
        exit 1;
    fi

    local can_proceed=true

    # TODO: This does not work!
    if [ "$THRESHOLD_DELETE" -gt 0 ] && [ "$COUNT_DELETE" -gt "$THRESHOLD_DELETE" ]; then
        log "**ERROR** - Too many files deleted. Unable to proceed."
        can_proceed=false
    fi

    if [ "$THRESHOLD_ADD" -gt 0 ] && [ "$COUNT_ADD" -gt "$THRESHOLD_ADD" ]; then
        log "**ERROR** - Too many files added. Unable to proceed."
        can_proceed=false
    fi

    if [ "$THRESHOLD_MOVE" -gt 0 ] && [ "$COUNT_MOVE" -gt "$THRESHOLD_MOVE" ]; then
        log "**ERROR** - Too many files moved. Unable to proceed."
        can_proceed=false
    fi

    if [ "$THRESHOLD_COPY" -gt 0 ] && [ "$COUNT_COPY" -gt "$THRESHOLD_COPY" ]; then
        log "**ERROR** - Too many files copied. Unable to proceed."

        can_proceed=false
    fi

    if [ "$THRESHOLD_UPDATE" -gt 0 ] && [ "$COUNT_UPDATE" -gt "$THRESHOLD_UPDATE" ]; then
        log "**ERROR** - Too many files updated. Unable to proceed."
        can_proceed=false
    fi

    if [[ "$can_proceed" = false ]]; then
        send_email "[Aborted] SnapRAID Sync Report" "The sync was aborted as some thresholds were exceeded."
        restore_services
        exit 1;
    fi

    # TODO: Backup Cloud Storage.
    # TODO: Backup Jellyfin.

    log "Fixing files with zero sub-second timestamps..."
    # Only logging errors to file and console.
    snapraid touch 1>/dev/null 2> >(log_with_timestamp >&2)
    log ""

    log "Balancing the array..."
    mergerfs.balance -p $BALANCE_TARGET /pool &>> "$LOG_FILE_BALANCE"
    log ""

    log "Syncing..."
    # Run the sync; log errors to file and console; filter output lines and log them to file and console.
    snapraid sync 2> >(log_with_timestamp >&2) | awk '/100% completed/,/Everything OK/' | log_with_timestamp

    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
        log "Sync failed. Aborting."
        restore_services
        send_email "[Failure] SnapRAID Sync Report" "There was an error during the sync."
        exit 1
    fi
    log ""

    log "Restoring Services..."
    restore_services
    log ""

    print_status "Final Status"

    log ""
    log ""
    log ""

    log "=== SMART Report Start ==="
    log ""
    snapraid smart 2>&1 | log_with_timestamp
    log ""
    log "=== SMART Report End ==="

    log ""

    log "Done."

    send_email "[Success] SnapRAID Sync Report" "SnapRAID sync completed successfully. Check $LOG_FILE for details."
}

function formatted_date() {
    echo "$(date '+%Y-%m-%d_%H-%M-%S')"
}

function log() {
    local message="$1"
    echo "[$(formatted_date)] $message" | tee -a "$LOG_FILE"
}

function log_with_timestamp() {
    while IFS= read -r line; do
        echo "[$(formatted_date)] $line" | tee -a "$LOG_FILE"
    done
}

function print_status() {
    local label="$1"

    log "=== ${label} Start ==="
    log ""

    snapraid status | sed -n '6,11p;30,31p;33,$p' | log_with_timestamp

    log ""
    log "=== ${label} End ==="
}

function pause_docker_services() {
	docker pause $(docker ps -qa) 1>/dev/null 2> >(log_with_timestamp >&2)
}

function resume_docker_services() {
	docker unpause $(docker ps -qa) 1>/dev/null 2> >(log_with_timestamp >&2)
}

function stop_systemd_services() {
	systemctl stop "$SYSTEMD_SERVICES" 1>/dev/null 2> >(log_with_timestamp >&2)
}

function start_systemd_services() {
	systemctl start "$SYSTEMD_SERVICES" 1>/dev/null 2> >(log_with_timestamp >&2)
}

function stop_services {
    pause_docker_services
    stop_systemd_services
}

function restore_services {
    resume_docker_services
    start_systemd_services
}

function send_email() {
    local subject="$1"
    local message="$2"

    # Build the attachment list by checking if each file exists
    local attachments=""
    for file in "$LOG_FILE" "$LOG_FILE_DIFF" "$LOG_FILE_BALANCE"; do
        if [[ -f "$file" ]]; then
            attachments+=" -a $file"
        fi
    done

    # Send the email with the attachments that exist
    echo -e "$message\n\nLog Content:\n$(cat "$LOG_FILE")" | mutt -s "$subject" $attachments -- "$EMAIL"
}


# Ensure log directory exists
mkdir -p "$LOG_FILE_DIR"

# Make sure to restore services if this script is terminated unexpectedly.
trap restore_services INT TERM

main
