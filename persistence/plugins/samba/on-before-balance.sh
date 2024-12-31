#!/bin/bash

# Shut down service gracefully, to ensure uncorrupted backup and no errors suring the sync.
systemctl stop smb nmb
