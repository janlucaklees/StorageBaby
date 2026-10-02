#!/bin/bash
#
# Installed by the `storage` role as `plugins/<service>/on-before-balance.sh`, one
# directory per name in `storage.snapraid.maintenance.stop_services`. The service is
# stopped before the balance moves its files between branches and started again after the
# scrub -- and on failure, so a run that aborts never leaves one down.
#
# The service name is this script's own directory name, which is why the file carries no
# rendered value at all and is byte-identical on every host: `stop_services` decides which
# directories exist, and nothing else.
set -euo pipefail

service="$(basename "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")")"

# A pod service has no `<name>.service` at all, so the unit is asked for rather than
# guessed -- the same resolution `storagebaby-svc` does on the host. grep and not
# the exit status of list-unit-files, because that command is happy to list nothing.
unit="${service}.service"
if systemctl --user -M "svc-${service}@" list-unit-files "${service}-pod.service" 2> /dev/null \
	| grep -q "^${service}-pod.service"; then
	unit="${service}-pod.service"
fi

echo "Stopping ${unit} in svc-${service}'s systemd manager."
systemctl --user -M "svc-${service}@" stop "$unit"
