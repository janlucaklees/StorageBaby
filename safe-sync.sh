#!/bin/bash

systemctl stop smb nmb rslsync plexmediaserver

snapraid touch

snapraid sync

systemctl start smb nmb rslsync plexmediaserver

