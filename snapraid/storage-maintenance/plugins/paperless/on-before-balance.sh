#!/bin/bash

# Shut down service gracefully, to ensure uncorrupted backup and no errors suring the sync.
docker stop paperless-webserver-1
docker stop paperless-broker-1 paperless-tika-1 paperless-db-1 paperless-gotenberg-1

# Backup Paperless data.
# TODO: Backup the database as an SQL file.
mkdir -p /pool/backups/apps/paperless/var/lib/docker/volumes
rsync \
    --archive \
    --delete \
    --keep-dirlinks \
    --hard-links \
    --numeric-ids \
    --human-readable \
    --info=progress2 \
    --stats \
    /var/lib/docker/volumes/paperless_data \
    /var/lib/docker/volumes/paperless_media \
    /var/lib/docker/volumes/paperless_pgdata \
    /var/lib/docker/volumes/paperless_redisdata \
    /pool/backups/apps/paperless/var/lib/docker/volumes/
