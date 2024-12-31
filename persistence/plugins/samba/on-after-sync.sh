#!/bin/bash

# Start up Samba again as sync is over and it is safe again to change data.
systemctl start smb nmb
