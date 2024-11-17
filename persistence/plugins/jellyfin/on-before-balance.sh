#!/bin/bash

# Shut down service gracefully, to ensure uncorrupted backup and no errors suring the sync.
docker stop jellyfin-jellyfin-1

# Backup Jellyfin data. Database is included.
rsync \
    --archive \
    --delete \
    --human-readable \
    --stats \
    /home/jlk/StorageBaby/jellyfin/mounts \
    /pool/backups/apps/jellyfin/home/jlk/StorageBaby/jellyfin/
