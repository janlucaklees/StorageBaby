#!/bin/bash

# Shut down service gracefully, to ensure uncorrupted backup and no errors suring the sync.
docker stop jellyfin

# Backup Jellyfin data. Database is included.
rsync \
    --archive \
    --delete \
    --human-readable \
    --stats \
    /pool/apps/jellyfin/volumes/jellyfin_config/. \
    /pool/backups/apps/jellyfin/jellyfin_config/.
