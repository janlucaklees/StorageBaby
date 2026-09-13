#!/bin/bash

set -xe

# Backup OpenProject database
mkdir -p /pool/backups/devices/CloudBaby/openproject
ssh cloud.janlucaklees.de "cd /home/jlk/CloudBaby/openproject && make database_snapshot"
rsync \
	--archive \
	--delete \
	--numeric-ids \
	--no-whole-file \
	--inplace \
	--human-readable \
	--info=progress2 \
	--stats \
	root@cloud.janlucaklees.de:/home/jlk/CloudBaby/openproject/snapshot.sql \
	/pool/backups/devices/CloudBaby/openproject

# Backup OpenProject Files
mkdir -p /pool/backups/devices/CloudBaby/openproject/volumes
rsync \
	--archive \
	--delete \
	--keep-dirlinks \
	--hard-links \
	--numeric-ids \
	--human-readable \
	--info=progress2 \
	--stats \
	root@cloud.janlucaklees.de:/var/lib/docker/volumes/openproject_data \
	root@cloud.janlucaklees.de:/var/lib/docker/volumes/openproject_database \
	/pool/backups/devices/CloudBaby/openproject/volumes
