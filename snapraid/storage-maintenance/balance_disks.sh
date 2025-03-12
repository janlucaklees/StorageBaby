#!/bin/bash

#
# Settings
DEFAULT_BALANCE_TARGET_PERCENTAGE=5


BALANCE_TARGET_PERCENTAGE="$DEFAULT_BALANCE_TARGET_PERCENTAGE"

# Check whether to use default value or a given parameter
if [[ -z "$1" ]]; then
    echo "No target percentage supplied. Using default value: $DEFAULT_BALANCE_TARGET_PERCENTAGE%"
else
    BALANCE_TARGET_PERCENTAGE="$1"
fi

# Validate target percentage
if ! [[ "$BALANCE_TARGET_PERCENTAGE" =~ ^[0-9]+$ ]] ||
   [[ "$BALANCE_TARGET_PERCENTAGE" -lt 0 ||
   "$BALANCE_TARGET_PERCENTAGE" -gt 100 ]];
then
    echo "Invalid target percentage: $BALANCE_TARGET_PERCENTAGE. Must be an integer between 0 and 100."
    exit 1
fi

# Ensure log directory exists
mkdir -p /var/log/snapraid

# Perform disk balancing
mergerfs.balance -p "$BALANCE_TARGET_PERCENTAGE" /pool &> /var/log/snapraid/balance_disks.log
