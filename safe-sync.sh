#!/bin/bash

systemctl stop smb nmb plexmediaserver jellyfin syncthing@jlk

snapraid touch

snapraid sync

systemctl start smb nmb plexmediaserver jellyfin syncthing@jlk

