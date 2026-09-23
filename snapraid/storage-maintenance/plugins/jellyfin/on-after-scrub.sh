#!/bin/bash

# Start up Jellyfin again as sync is over and it is safe again to change data.
systemctl --user -M svc-jellyfin@ start jellyfin.service
