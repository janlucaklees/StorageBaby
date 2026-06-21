#!/bin/bash

# Ensure log directory exists
mkdir -p /var/log/snapraid

# Runs the scrub.
snapraid scrub --plan new --log /var/log/snapraid/snapraid-scrub-new.log
snapraid scrub --plan 8 --older-than 12 --log /var/log/snapraid/snapraid-scrub.log
