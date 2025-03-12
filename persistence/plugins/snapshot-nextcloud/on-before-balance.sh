#!/bin/bash

set -xe

# TODO: Implement regular snapshots on the server
# Backup NextCloud database
# rsync \
#     --archive \
#     --delete \
#     --compress \
#     --human-readable \
#     --info=progress2 \
#     --stats \
#     root@ttraefik.janlucaklees.de:/home/jlk/CloudBaby/nextcloud/snapshot.sql \
#     /pool/backups/devices/CloudBaby/nextcloud

# TODO: Move this backup to another location
# Backup NextCloud Files
mkdir -p /pool/apps/nextcloud/volumes/nextcloud
rsync \
    --archive \
    --delete \
    --human-readable \
    --info=progress2 \
    --stats \
    root@ttraefik.janlucaklees.de:/var/lib/docker/volumes/nextcloud_nextcloud/_data/. \
    /pool/apps/nextcloud/volumes/nextcloud/.
