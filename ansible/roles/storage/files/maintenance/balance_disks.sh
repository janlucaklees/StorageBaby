#!/bin/bash

#
# Settings
#
# The target percentage and the pool path come from `maintenance.env`, which the `storage`
# role renders from host.yml. An explicit argument still wins, because balancing by hand
# with a different threshold is what this script was written for.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/maintenance.env" ]]; then
	# shellcheck source=/dev/null
	source "${SCRIPT_DIR}/maintenance.env"
fi

DEFAULT_BALANCE_TARGET_PERCENTAGE="${BALANCE_TARGET_PERCENTAGE:-5}"
POOL="${STORAGE_POOL:-/pool}"

BALANCE_TARGET_PERCENTAGE="$DEFAULT_BALANCE_TARGET_PERCENTAGE"

# Check whether to use default value or a given parameter
if [[ -z "$1" ]]; then
	echo "No target percentage supplied. Using default value: $DEFAULT_BALANCE_TARGET_PERCENTAGE%"
else
	BALANCE_TARGET_PERCENTAGE="$1"
fi

# Validate target percentage
if ! [[ "$BALANCE_TARGET_PERCENTAGE" =~ ^[0-9]+$ ]] \
	|| [[ "$BALANCE_TARGET_PERCENTAGE" -lt 0 ||
		"$BALANCE_TARGET_PERCENTAGE" -gt 100 ]]; then
	echo "Invalid target percentage: $BALANCE_TARGET_PERCENTAGE. Must be an integer between 0 and 100."
	exit 1
fi

# Ensure log directory exists
mkdir -p /var/log/snapraid

# Perform disk balancing
mergerfs.balance -p "$BALANCE_TARGET_PERCENTAGE" "$POOL" &> /var/log/snapraid/balance_disks.log
