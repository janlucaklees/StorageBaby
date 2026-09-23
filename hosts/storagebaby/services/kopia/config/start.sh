#!/bin/sh
# Entrypoint of the kopia container: connect (or create) the declared repository
# once, then hand the process over to the server. The unit overrides the image's
# /bin/kopia entrypoint with /bin/sh and points Exec= at this file.
set -eu

# Both passwords come from podman secrets, never from the unit, and both reach kopia
# through the environment: KOPIA_PASSWORD is the repository password, and
# KOPIA_SERVER_PASSWORD is the `--server-password` flag's envar (kopia 0.23,
# cli/command_server.go). Environment, not argv, because argv is world-readable in
# `ps` and /proc/<pid>/cmdline.
#
# Each secret is read in its own plain assignment so that `set -e` aborts on a
# missing or unreadable file -- an assignment carries the command substitution's
# exit status, but `export x="$(...)"` carries export's, which is always 0.
KOPIA_PASSWORD="$(cat /run/secrets/repository_password)"
export KOPIA_PASSWORD
KOPIA_SERVER_PASSWORD="$(cat /run/secrets/server_password)"
export KOPIA_SERVER_PASSWORD

# Set by the unit, and the same path the image defaults to. The file lives on the
# `config` volume, so it survives a restart -- its presence is what tells a later
# start that the repository connection is already established.
#
# The connect/create calls below also persist the password next to it, because the
# unit sets KOPIA_PERSIST_CREDENTIALS_ON_CONNECT and KOPIA_USE_KEYRING -- see the
# comment there for why that matters to `podman exec`.
config_path="${KOPIA_CONFIG_PATH:-/app/config/repository.config}"

if [ ! -f "$config_path" ]; then
	case "$KOPIA_REPOSITORY" in
		s3)
			# Read first, pass second: inside the command substitution of an argument a
			# failing `cat` would only blank the flag, and kopia would report a rejected
			# key instead of a missing secret.
			b2_key_id="$(cat /run/secrets/b2_key_id)"
			b2_application_key="$(cat /run/secrets/b2_application_key)"
			kopia repository connect s3 \
				--bucket="$KOPIA_S3_BUCKET" \
				--endpoint="$KOPIA_S3_ENDPOINT" \
				--access-key="$b2_key_id" \
				--secret-access-key="$b2_application_key"
			;;
		filesystem)
			# `create` fails on a repository that already exists, `connect` on one that
			# does not, and the two cases are told apart by the volume being non-empty:
			# a repository directory always holds at least kopia.repository.f.
			if [ -n "$(ls -A /app/repo 2> /dev/null)" ]; then
				kopia repository connect filesystem --path=/app/repo
			else
				kopia repository create filesystem --path=/app/repo
			fi
			;;
		*)
			echo "unknown KOPIA_REPOSITORY: $KOPIA_REPOSITORY" >&2
			exit 2
			;;
	esac
fi

# No --server-password: it is KOPIA_SERVER_PASSWORD above. --server-username stays a
# flag -- it is not a secret, and spelling it out here is what says basic auth is on.
exec kopia server start \
	--insecure \
	--address='http://0.0.0.0:51515' \
	--server-username="$KOPIA_SERVER_USERNAME"
