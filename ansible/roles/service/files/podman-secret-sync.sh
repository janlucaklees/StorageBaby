#!/bin/bash
# usage: podman-secret-sync <user> <secret-name>   (value on stdin)
# Creates or replaces a rootless Podman secret for <user>. Prints CHANGED or UNCHANGED.
set -euo pipefail
user=$1
name=$2
uid=$(id -u "$user")
value=$(cat)

# runuser keeps the caller's cwd, and rootless podman re-execs itself inside the
# user namespace, where the service user cannot chdir back into root's 0700 home.
cd /tmp

run() { runuser -u "$user" -- env XDG_RUNTIME_DIR="/run/user/$uid" "$@"; }

if run podman secret exists "$name"; then
	current=$(run podman secret inspect --showsecret --format '{{.SecretData}}' "$name")
	if [ "$current" = "$value" ]; then
		echo UNCHANGED
		exit 0
	fi
fi
# stdout of the create goes to /dev/null: podman prints the new secret's id there,
# and the caller's `changed_when` compares this script's whole stdout to CHANGED --
# with the id in front it never matched, so a changed secret never restarted anything.
printf '%s' "$value" | run podman secret create --replace "$name" - > /dev/null
echo CHANGED
