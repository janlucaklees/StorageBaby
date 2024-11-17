#!/bin/bash

# Ensure log directory exists
mkdir -p /var/log/snapraid

# Runs the scrub.
snapraid scrub -p new --log /var/log/snapraid/snapraid-scrub.log
