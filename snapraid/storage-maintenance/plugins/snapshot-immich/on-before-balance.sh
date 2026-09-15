#!/bin/bash

set -xe

# Backup Immich database
mkdir -p /pool/backups/devices/CloudBaby/immich
ssh cloud.janlucaklees.de "cd /home/jlk/CloudBaby/immich && make database_snapshot"
rsync \
	--archive \
	--delete \
	--numeric-ids \
	--no-whole-file \
	--inplace \
	--human-readable \
	--info=progress2 \
	--stats \
	root@cloud.janlucaklees.de:/home/jlk/CloudBaby/immich/snapshot.sql \
	/pool/backups/devices/CloudBaby/immich

# Backup Immich Files
mkdir -p /pool/backups/devices/CloudBaby/immich/volumes
rsync \
	--archive \
	--delete \
	--keep-dirlinks \
	--hard-links \
	--numeric-ids \
	--human-readable \
	--info=progress2 \
	--stats \
	root@cloud.janlucaklees.de:/var/lib/docker/volumes/immich_upload \
	/pool/backups/devices/CloudBaby/immich/volumes
