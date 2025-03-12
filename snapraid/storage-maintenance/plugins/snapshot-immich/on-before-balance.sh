#!/bin/bash

set -xe

# TODO: Implement regular snapshots on the server
# Backup Immich database
# rsync \
#     --archive \
#     --delete \
#     --numeric-ids \
#     --no-whole-file \
#     --inplace \
#     --compress \
#     --human-readable \
#     --info=progress2 \
#     --stats \
#     root@ttraefik.janlucaklees.de:/home/jlk/CloudBaby/immich/snapshot.sql \
#     /pool/backups/devices/CloudBaby/immich

# TODO: Move this backup to another location
# Backup Immich Files
mkdir -p /pool/apps/immich/snapshot
rsync \
    --archive \
    --delete \
    --keep-dirlinks \
    --hard-links \
    --numeric-ids \
    --human-readable \
    --info=progress2 \
    --stats \
    root@ttraefik.janlucaklees.de:/var/lib/docker/volumes/immich_upload/_data/. \
    /pool/apps/immich/snapshot/.
