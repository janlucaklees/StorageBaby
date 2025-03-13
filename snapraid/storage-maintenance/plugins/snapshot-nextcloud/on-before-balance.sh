#!/bin/bash

set -xe

# Backup NextCloud database
ssh cloud.janlucaklees.de "cd /home/jlk/CloudBaby/nextcloud && make database_snapshot"
rsync \
    --archive \
    --delete \
    --numeric-ids \
    --no-whole-file \
    --inplace \
    --human-readable \
    --info=progress2 \
    --stats \
    root@cloud.janlucaklees.de:/home/jlk/CloudBaby/nextcloud/snapshot.sql \
    /pool/backups/devices/CloudBaby/nextcloud

# TODO: Move this backup to another location
# Backup NextCloud Files
mkdir -p /pool/apps/nextcloud/volumes/nextcloud
rsync \
    --archive \
    --delete \
    --keep-dirlinks \
    --hard-links \
    --numeric-ids \
    --human-readable \
    --info=progress2 \
    --stats \
    root@cloud.janlucaklees.de:/var/lib/docker/volumes/nextcloud_nextcloud/_data/. \
    /pool/apps/nextcloud/volumes/nextcloud/.
