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
                echo "Executing ${script} failed. Aborting."
                exit 1
            fi
        done
    else
        echo "Nothing to execute."
    fi
}

#
# Code
run_hook "on-start"

print_snapraid_status "Initial Snapraid Status"

run_hook "on-before-balance"

echo_section "Balancing disks"
if ! bash "${SCRIPT_DIR}/balance_disks.sh"; then
    echo "Balancing disks failed. Aborting."
    exit 1
fi

run_hook "on-before-sync"

echo_section "Syncing"
if ! bash "${SCRIPT_DIR}/sync.sh"; then
    echo "Syncing failed. Aborting."
    exit 1
fi

run_hook "on-after-sync"

echo_section "Scrubbing"
if ! bash "${SCRIPT_DIR}/scrub.sh"; then
    echo "Scrubbing failed. Aborting."
    exit 1
fi

run_hook "on-after-scrub"

print_snapraid_status "Final Snapraid Status"

print_snapraid_smart

run_hook "on-finish"
