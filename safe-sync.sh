#!/bin/bash

systemctl stop smb nmb plexmediaserver syncthing@jlk

snapraid touch

snapraid sync

systemctl start smb nmb plexmediaserver syncthing@jlk

