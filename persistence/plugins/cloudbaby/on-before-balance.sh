#!/bin/bash

rsync \
    --archive \
    --delete \
    --compress \
    --human-readable \
    --stats \
    --rsh "ssh -i /home/jlk/.ssh/id_ed25519" \
    --exclude '/cloud-baby/data/nextcloud/nextcloud/data/nextcloud.log' \
    --exclude '/cloud-baby/data/nextcloud/nextcloud/data/nextcloud.log.1' \
    jlk@cloud.janlucaklees.de:/cloud-baby \
    /pool/backups/devices/CloudBaby
