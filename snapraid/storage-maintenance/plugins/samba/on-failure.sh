#!/bin/bash

# Start Samba back up if maintenance aborted after it was stopped.
systemctl start smb nmb
