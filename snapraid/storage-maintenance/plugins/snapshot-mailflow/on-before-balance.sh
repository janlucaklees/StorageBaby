#!/bin/bash

set -xe

# Backup NextCloud database
mkdir -p /pool/backups/devices/CloudBaby/mailflow
ssh cloud.janlucaklees.de "cd /home/jlk/CloudBaby/mailflow && make database_snapshot"
rsync \
	--archive \
	--delete \
	--numeric-ids \
	--no-whole-file \
	--inplace \
	--human-readable \
	--info=progress2 \
	--stats \
	root@cloud.janlucaklees.de:/home/jlk/CloudBaby/mailflow/snapshot.sql \
	/pool/backups/devices/CloudBaby/mailflow

# Backup NextCloud Files
mkdir -p /pool/backups/devices/CloudBaby/mailflow/volumes
rsync \
	--archive \
	--delete \
	--keep-dirlinks \
	--hard-links \
	--numeric-ids \
	--human-readable \
	--info=progress2 \
	--stats \
	root@cloud.janlucaklees.de:/var/lib/docker/volumes/mailflow_broker \
	root@cloud.janlucaklees.de:/var/lib/docker/volumes/mailflow_database \
	/pool/backups/devices/CloudBaby/mailflow/volumes
