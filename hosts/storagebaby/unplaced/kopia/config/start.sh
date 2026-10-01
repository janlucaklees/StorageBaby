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
			# There is no cheap "is there a repository in this bucket" probe the way the
			# filesystem branch below has one, so the two cases are told apart by trying:
			# `connect` first, and `create` only if that failed. A fresh bucket therefore
			# comes up on its own instead of leaving the unit restart-looping until
			# somebody creates the repository by hand.
			#
			# This does not turn a wrong password into a silent second repository: kopia
			# refuses to create over a bucket that already holds one ("repository is
			# already initialized"), so a bad `repository_password` against a live
			# repository still fails both calls and the unit still restart-loops --
			# which is the visible failure it should be.
			if ! kopia repository connect s3 \
				--bucket="$KOPIA_S3_BUCKET" \
				--endpoint="$KOPIA_S3_ENDPOINT" \
				--access-key="$b2_key_id" \
				--secret-access-key="$b2_application_key"; then
				echo "kopia: no repository to connect to in $KOPIA_S3_BUCKET, creating one" >&2
				kopia repository create s3 \
					--bucket="$KOPIA_S3_BUCKET" \
					--endpoint="$KOPIA_S3_ENDPOINT" \
					--access-key="$b2_key_id" \
					--secret-access-key="$b2_application_key"
			fi
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

# Repository users, one per backup client. `kopia server users add` is a repository
# command, not a server one -- it writes a manifest -- so this runs here, before the
# server binds, and needs nothing but the connection established above.
#
# The unit renders one `Secret=client_<service>` per `host_secrets` key that starts
# with `client_`, so the files below are the whole client list: no name is spelled out
# in this script, and adding a client is a line in kopia's service.yml plus the same
# value on the client's side of the set.
#
# `<service>@$KOPIA_CLIENT_HOSTS` is the identity kopia matches a connecting client
# by, and it has to be the pair the sidecar announces: KOPIA_CLIENT_USERNAME is the
# service name, KOPIA_CLIENT_HOSTNAME the host -- the same `hostname` this variable is
# rendered from.
# The one placeholder on this platform that would otherwise *work*. A client
# password is a free choice -- it only has to match on both sides -- so leaving it
# at REPLACE_ME registers a real account, the sidecar connects with the same
# string, backups run, and nothing anywhere reports a problem. The repository
# endpoint is public at https://kopia.<domain>, so that is an internet-reachable
# account whose password is printed in this repository. Refusing here, before any
# client is registered, is what makes it as loud as every other REPLACE_ME: the
# server does not come up and no partial set of accounts is left behind.
for f in /run/secrets/client_*; do
	# An unmatched glob stays literal in POSIX sh, which is the "no clients yet" case.
	[ -e "$f" ] || continue
	if [ "$(cat "$f")" = "REPLACE_ME" ]; then
		echo "kopia: the client password for ${f##*/client_} is still the REPLACE_ME placeholder." >&2
		echo "kopia: fill hosts/<host>/secrets/kopia-clients.sops.yaml (make sops FILE=...)" >&2
		echo "kopia: with the same value on the client's side; refusing to register clients." >&2
		exit 1
	fi
done

for f in /run/secrets/client_*; do
	[ -e "$f" ] || continue
	name="${f##*/client_}"
	user="$name@$KOPIA_CLIENT_HOSTS"
	client_password="$(cat "$f")"
	# kopia 0.23.1 has no --user-password-file: `server users add|set` accept only
	# --user-password, --user-password-hash and the interactive --ask-password
	# (cli/command_user_add_set.go). So the value goes through argv, where it is
	# visible in this container's `ps` and, on the host, in the cmdline of a process
	# owned by svc-kopia's subuid -- readable by root and by svc-kopia, both of which
	# already hold the value in the podman secret store. Hashing it first does not
	# help: `server users hash-password` takes the password through argv too.
	if out="$(kopia server users add "$user" --user-password="$client_password" 2>&1)"; then
		echo "kopia: registered client $user" >&2
	else
		case "$out" in
			# "error getting new user profile: <user>: user already exists" --
			# `add` refuses an existing user, `set` is the update form of the
			# same command, and the pair is idempotent in effect. Output is
			# captured rather than shown so that this expected case is not a
			# scary line in the journal on every restart; every other failure
			# prints what kopia said and takes the container down with it,
			# because a server whose clients cannot authenticate is not up.
			*'user already exists'*)
				kopia server users set "$user" --user-password="$client_password"
				echo "kopia: updated client $user" >&2
				;;
			*)
				echo "$out" >&2
				exit 1
				;;
		esac
	fi
done

# The server's own certificate. It exists because the repository protocol is gRPC, and
# gRPC is HTTP/2: Traefik reaches an HTTP/2 backend only over TLS, so a server running
# `--insecure` behind it can serve the web UI and nothing else -- a client's session
# call is forwarded as HTTP/1.1 and never answered. Traefik re-encrypts to this
# certificate and does not verify it (`insecure_skip_verify` in service.yml); nothing
# could, and the hop is to 127.0.0.1.
#
# Generated once, onto the `config` volume beside repository.config, so it survives a
# restart and a client that pinned it keeps working. Ten years because rotating it
# would invalidate every client's pin for no gain: it is never seen outside this host.
cert=/app/config/server.cert
key=/app/config/server.key

if [ ! -f "$cert" ] || [ ! -f "$key" ]; then
	echo "kopia: generating the server's TLS certificate" >&2
	# Both names: `kopia` is the container's own hostname, KOPIA_PUBLIC_HOST the name
	# Traefik is reached under. Neither is verified today, and having both means a
	# future transport that does verify has something to match.
	openssl req -x509 -newkey rsa:4096 -nodes \
		-keyout "$key" -out "$cert" -days 3650 \
		-subj "/CN=kopia" \
		-addext "subjectAltName=DNS:kopia,DNS:$KOPIA_PUBLIC_HOST"
	chmod 600 "$key"
fi

# No --server-password: it is KOPIA_SERVER_PASSWORD above. --server-username stays a
# flag -- it is not a secret, and spelling it out here is what says basic auth is on.
exec kopia server start \
	--address='https://0.0.0.0:51515' \
	--tls-cert-file="$cert" \
	--tls-key-file="$key" \
	--server-username="$KOPIA_SERVER_USERNAME"
