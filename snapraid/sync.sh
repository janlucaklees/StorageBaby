#!/bin/bash

# Ensure log directory exists
mkdir -p /var/log/snapraid

# Suppress the stdout of this command, only show errors.
snapraid touch 1>/dev/null

# Run the sync
snapraid sync --log /var/log/snapraid/snapraid-sync.log
