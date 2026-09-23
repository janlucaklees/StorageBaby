#!/bin/bash

# Shut down service gracefully, to ensure uncorrupted backup and no errors suring the sync.
# Jellyfin is a rootless Podman service now: its unit lives in svc-jellyfin's own
# systemd manager, which root reaches only through `--user -M svc-jellyfin@`.
systemctl --user -M svc-jellyfin@ stop jellyfin.service

# Backup Jellyfin data. Database is included.
# mkdir -p /pool/backups/apps/jellyfin/config
# rsync \
#     --archive \
#     --delete \
#     --keep-dirlinks \
#     --hard-links \
#     --numeric-ids \
#     --human-readable \
#     --info=progress2 \
#     --stats \
#     /pool/apps/jellyfin/config/. \
#     /pool/backups/apps/jellyfin/config/.
