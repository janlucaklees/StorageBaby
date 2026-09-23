#!/bin/bash

# Start Jellyfin back up if maintenance aborted after it was stopped.
systemctl --user -M svc-jellyfin@ start jellyfin.service
