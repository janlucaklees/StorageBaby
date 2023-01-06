#!/bin/bash

running_docker_container=`docker ps -q`

systemctl stop smb nmb syncthing@jlk
docker stop ${running_docker_container}

snapraid touch

snapraid sync

systemctl start smb nmb syncthing@jlk
docker start ${running_docker_container}

