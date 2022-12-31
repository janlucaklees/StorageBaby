#!/bin/bash

# TODO: Stop docker services
systemctl stop smb nmb syncthing@jlk

snapraid touch

snapraid sync

systemctl start smb nmb syncthing@jlk
# TODO: Start Docker services again
