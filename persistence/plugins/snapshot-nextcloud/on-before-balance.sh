#!/bin/bash

set -xe

# Backup NextCloud database
rsync \
    --archive \
    --delete \
    --compress \
    --human-readable \
    --stats \
    root@ttraefik.janlucaklees.de:/home/jlk/CloudBaby/nextcloud/snapshot.sql
    /pool/backups/devices/CloudBaby/nextcloud

# Backup NextCloud Files
mkdir -p /pool/backups/devices/CloudBaby/nextcloud/nextcloud_nextcloud
rsync \
    --archive \
    --delete \
    --compress \
    --human-readable \
    --stats \
    root@ttraefik.janlucaklees.de:/var/lib/docker/volumes/nextcloud_nextcloud/_data/.
    /pool/backups/devices/CloudBaby/nextcloud/nextcloud_nextcloud/.
