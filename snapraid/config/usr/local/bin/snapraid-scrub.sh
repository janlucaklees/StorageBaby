#!/bin/bash

mkdir -p /var/log/snapraid

snapraid scrub --verbose --log /var/log/snapraid/snapraid-scrub.log

