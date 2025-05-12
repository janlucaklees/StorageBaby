#!/bin/bash

set -xe


# Backup NextCloud database
mkdir -p /pool/backups/devices/CloudBaby/paperless
ssh cloud.janlucaklees.de "cd /home/jlk/CloudBaby/paperless && make database_snapshot"
rsync \
    --archive \
    --delete \
    --numeric-ids \
    --no-whole-file \
    --inplace \
    --human-readable \
    --info=progress2 \
    --stats \
    root@cloud.janlucaklees.de:/home/jlk/CloudBaby/paperless/snapshot.sql \
    /pool/backups/devices/CloudBaby/paperless

# Backup NextCloud Files
mkdir -p /pool/backups/devices/CloudBaby/paperless/volumes
rsync \
    --archive \
    --delete \
    --keep-dirlinks \
    --hard-links \
    --numeric-ids \
    --human-readable \
    --info=progress2 \
    --stats \
    root@cloud.janlucaklees.de:/var/lib/docker/volumes/paperless_data \
    root@cloud.janlucaklees.de:/var/lib/docker/volumes/paperless_media \
    root@cloud.janlucaklees.de:/var/lib/docker/volumes/paperless_broker \
    /pool/backups/devices/CloudBaby/paperless/volumes
