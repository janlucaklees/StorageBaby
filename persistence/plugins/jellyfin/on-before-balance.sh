#!/bin/bash

# Shut down service gracefully, to ensure uncorrupted backup and no errors suring the sync.
docker stop jellyfin

# Backup Jellyfin data. Database is included.
mkdir -p /pool/backups/apps/jellyfin/jellyfin_config
rsync \
    --archive \
    --delete \
    --keep-dirlinks \
    --hard-links \
    --numeric-ids \
    --human-readable \
    --info=progress2 \
    --stats \
    /pool/apps/jellyfin/volumes/jellyfin_config/. \
    /pool/backups/apps/jellyfin/jellyfin_config/.
