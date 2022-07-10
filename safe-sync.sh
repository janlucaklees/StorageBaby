#!/bin/bash

systemctl stop smb nmb jellyfin syncthing@jlk

snapraid touch

snapraid sync

systemctl start smb nmb jellyfin syncthing@jlk

