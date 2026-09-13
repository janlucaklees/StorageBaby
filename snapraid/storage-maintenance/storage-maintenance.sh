#!/bin/bash

#
# Set some variables
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# TODO: Make this react to changes in file-location.
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

#
# Define functions
function echo_section() {
	local title=$1

	echo
	echo
	echo "=== ${title} ==="
}

function print_snapraid_status() {
	local label="$1"

	echo_section "${label}"
	echo

	# Filter out unimportant lines
	snapraid status | sed -n '6,11p;30,31p;33,$p'
}

function print_snapraid_smart() {
	echo_section "SMART Report"
	echo

	snapraid smart
}

function run_hook() {
	local hook=$1

	echo_section "Excuting ${hook} hook"

	if compgen -G "${SCRIPT_DIR}/plugins/*/${hook}.sh" > /dev/null; then
		for script in ${SCRIPT_DIR}/plugins/*/${hook}.sh; do
			echo
			echo "Running ${script} hook..."
			if ! bash "$script"; then
				if [[ "$hook" == "on-failure" ]]; then
					# Best-effort: one broken recovery script must not stop
					# the others (e.g. samba should still restart even if
					# jellyfin's restart script fails).
					echo "Executing ${script} failed. Continuing with remaining on-failure hooks."
				else
					fail "Executing ${script} failed."
				fi
			fi
		done
	else
		echo "Nothing to execute."
	fi
}

FAILURE_HANDLED=0

function fail() {
	local message=$1

	# Guard against running on-failure twice (e.g. a signal arriving while
	# fail() is already running the hook).
	if [[ "$FAILURE_HANDLED" -eq 1 ]]; then
		exit 1
	fi
	FAILURE_HANDLED=1

	echo "${message} Aborting."
	run_hook "on-failure"
	exit 1
}

#
# Code

# Run the on-failure hook if the script is killed (e.g. systemd stop/timeout
# during the run), so services stopped by on-before-balance aren't left down.
trap 'fail "Received signal, terminating."' INT TERM

run_hook "on-start"

print_snapraid_status "Initial Snapraid Status"

run_hook "on-before-balance"

echo_section "Balancing disks"
if ! bash "${SCRIPT_DIR}/balance_disks.sh"; then
	fail "Balancing disks failed."
fi

run_hook "on-before-sync"

echo_section "Syncing"
if ! bash "${SCRIPT_DIR}/sync.sh"; then
	fail "Syncing failed."
fi

run_hook "on-after-sync"

echo_section "Scrubbing"
if ! bash "${SCRIPT_DIR}/scrub.sh"; then
	fail "Scrubbing failed."
fi

run_hook "on-after-scrub"

print_snapraid_status "Final Snapraid Status"

print_snapraid_smart

run_hook "on-finish"
