#!/bin/bash
#
# Installed by the `storage` role as `plugins/<service>/on-after-scrub.sh` **and** as
# `plugins/<service>/on-failure.sh`, one directory per name in
# `storage.snapraid.maintenance.stop_services`. The failure copy is the half that matters:
# it is what keeps a service stopped for a run that then aborted from staying down.
#
# The service name is this script's own directory name -- see `stop-service.sh`.
set -euo pipefail

service="$(basename "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")")"

unit="${service}.service"
if systemctl --user -M "svc-${service}@" list-unit-files "${service}-pod.service" 2> /dev/null \
	| grep -q "^${service}-pod.service"; then
	unit="${service}-pod.service"
fi

echo "Starting ${unit} in svc-${service}'s systemd manager."
systemctl --user -M "svc-${service}@" start "$unit"
