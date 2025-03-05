#!/bin/bash

set -xe

# Backup NextCloud database
rsync \
    --archive \
    --delete \
    --compress \
    --human-readable \
    --stats \
    root@ttraefik.janlucaklees.de:/home/jlk/CloudBaby/immich/snapshot.sql
    /pool/backups/devices/CloudBaby/immich

# Backup NextCloud Files
mkdir -p /pool/backups/devices/CloudBaby/immich/immich_upload
rsync \
    --archive \
    --delete \
    --compress \
    --human-readable \
    --stats \
    root@ttraefik.janlucaklees.de:/var/lib/docker/volumes/immich_upload/_data/.
    /pool/backups/devices/CloudBaby/immich/immich_upload/.
