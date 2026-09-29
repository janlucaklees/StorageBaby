#!/bin/bash

set -xe

# Shut down OpenArchiver gracefully so its archive data stays unchanged during maintenance.
docker stop openarchiver_openarchiver

# Back up the local database into the pool before balancing and syncing it.
BACKUP_DIR=/pool/backups/devices/StorageBaby/openarchiver
mkdir -p "$BACKUP_DIR"
make \
	-C /home/jlk/StorageBaby/openarchiver \
	DATABSE_SNAPSHOT_PATH="$BACKUP_DIR/snapshot.sql" \
	database_snapshot
