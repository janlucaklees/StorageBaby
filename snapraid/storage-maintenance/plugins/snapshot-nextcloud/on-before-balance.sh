#!/bin/bash

set -xe

# Backup NextCloud database
mkdir -p /pool/backups/devices/CloudBaby/nextcloud
ssh cloud.janlucaklees.de "cd /home/jlk/CloudBaby/nextcloud && make database_snapshot"
rsync \
	--archive \
	--delete \
	--numeric-ids \
	--no-whole-file \
	--inplace \
	--human-readable \
	--info=progress2 \
	--stats \
	root@cloud.janlucaklees.de:/home/jlk/CloudBaby/nextcloud/snapshot.sql \
	/pool/backups/devices/CloudBaby/nextcloud

# Backup NextCloud Files
mkdir -p /pool/backups/devices/CloudBaby/nextcloud/volumes
rsync \
	--archive \
	--delete \
	--keep-dirlinks \
	--hard-links \
	--numeric-ids \
	--human-readable \
	--info=progress2 \
	--stats \
	root@cloud.janlucaklees.de:/var/lib/docker/volumes/nextcloud_nextcloud \
	/pool/backups/devices/CloudBaby/nextcloud/volumes
