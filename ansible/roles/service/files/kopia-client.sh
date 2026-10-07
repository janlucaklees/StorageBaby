#!/bin/sh
# Kopia client sidecar: connects to the platform's Kopia server through Traefik,
# applies the snapshot policy declared in service.yml, and keeps a scheduler
# running so snapshots happen at KOPIA_SNAPSHOT_TIME.

# No `set -o pipefail`. The unit runs this with the kopia image's own `/bin/sh`, which
# is dash 0.5.11 (Ubuntu 22.04) and answers `Illegal option -o pipefail` -- under
# `set -e` that is the sidecar failing on its third line, before it ever reaches kopia.
# The one pipeline that needed it is written without one instead; see below.
set -eu

# KOPIA_PASSWORD comes from the unit as an env-type podman secret, so it is already in
# this process's environment -- and, unlike an export made here, in the environment of
# every `podman exec` into this container as well. Checked rather than assumed: an
# unset one would otherwise surface much later as an interactive password prompt.
: "${KOPIA_PASSWORD:?kopia_password secret is not in the environment}"
export KOPIA_CONFIG_PATH=/app/config/repository.config
export KOPIA_CACHE_DIRECTORY=/app/cache
export KOPIA_PERSIST_CREDENTIALS_ON_CONNECT=true
export KOPIA_USE_KEYRING=false

# The URL carries an explicit `:443` -- kopia 0.23's gRPC resolver takes the authority
# from it verbatim and rejects a bare host as `invalid target address "<host>:":
# missing port after port-separator colon`. The certificate fetch below needs the two
# halves apart again, and the port is defaulted anyway so that a URL without one still
# works if a later kopia stops needing it.
server_authority="${KOPIA_SERVER_URL#https://}"
server_authority="${server_authority%%/*}"
server_host="${server_authority%%:*}"
server_port="${server_authority#"$server_host"}"
server_port="${server_port#:}"
: "${server_port:=443}"

# sha256 of nothing at all: the last guard, because a fingerprint of the empty stream
# would be pinned forever and every later connection would be rejected.
EMPTY_DIGEST=E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855

PEM=/tmp/kopia-server.pem
DER=/tmp/kopia-server.der

# Test hosts serve Traefik's self-signed default certificate, which no CA vouches for:
# fetch it and pin it. Fetched on every attempt, not once before the retry loop -- the
# first attempts are expected to fail, because this runs while Traefik is still starting.
#
# Written as three statements and not as one pipeline on purpose: without pipefail a
# pipeline reports only its last command, so a failed fetch would arrive at
# `sha256sum` as an empty stream and be pinned as its digest. Here every step that can
# fail is checked where it fails -- the fetch, the decode -- and the digest is taken
# from a file that is already known to hold a certificate.
server_fingerprint() {
	openssl s_client -connect "$server_host:$server_port" -servername "$server_host" < /dev/null 2> /dev/null > "$PEM" || return 1
	[ -s "$PEM" ] || return 1
	openssl x509 -in "$PEM" -outform DER -out "$DER" 2> /dev/null || return 1
	fingerprint="$(sha256sum "$DER" | awk '{ print toupper($1) }')" || return 1
	[ -n "$fingerprint" ] && [ "$fingerprint" != "$EMPTY_DIGEST" ] || return 1
	printf '%s' "$fingerprint"
}

# Every call that opens a session is bounded, and this is the reason: a kopia server
# answers a **wrong client password** by never completing the gRPC session rather than by
# refusing it. The TCP connection is made, TLS succeeds, the stream is opened, and then
# nothing -- no error on the client, and not even a "starting session for user" line in
# the server's own log. Unbounded, that is an indefinite hang.
#
# Measured, on this platform: immich's `kopia_password` and the kopia server's
# `client_immich` had been filled with two different strings, and the sidecar hung on
# every connect for two days without once saying why. With a timeout the same fault is a
# failed attempt and a log line, which is the difference between a bug somebody finds and
# a backup that silently does not exist.
KOPIA_ATTEMPT_TIMEOUT=${KOPIA_ATTEMPT_TIMEOUT:-120}

connect_to_server() {
	set -- timeout "$KOPIA_ATTEMPT_TIMEOUT" kopia repository connect server \
		--url="$KOPIA_SERVER_URL" \
		--override-username="$KOPIA_CLIENT_USERNAME" \
		--override-hostname="$KOPIA_CLIENT_HOSTNAME"
	if [ "${KOPIA_PIN_SERVER_CERT:-0}" = "1" ]; then
		pinned="$(server_fingerprint)" || return 1
		set -- "$@" --server-cert-fingerprint="$pinned"
	fi
	"$@"
}

# An existing connection is verified, not assumed. The fingerprint pinned at connect
# time is frozen in repository.config, and on a test host it goes stale for a reason
# that has nothing to do with this service: Traefik generates a fresh self-signed
# default certificate every time it restarts, and every sidecar that pinned the old one
# is then locked out for good ("can't find certificate matching SHA256 fingerprint").
# A connection that cannot open the repository is worth nothing, so it is thrown away
# and made again -- which re-fetches the certificate and pins the current one.
if [ -f "$KOPIA_CONFIG_PATH" ] && ! timeout "$KOPIA_ATTEMPT_TIMEOUT" kopia repository status > /dev/null 2>&1; then
	echo "kopia: the stored connection does not open, reconnecting" >&2
	rm -f "$KOPIA_CONFIG_PATH"
fi

if [ ! -f "$KOPIA_CONFIG_PATH" ]; then
	until connect_to_server; do
		# `$?` here is the condition's status: `timeout` reports 124 when it had to kill
		# the attempt, and that is a different fault from a server that is not up yet.
		# Named explicitly because the two look identical in a restart loop otherwise,
		# and the credential one is the one nobody guesses.
		if [ "$?" -eq 124 ]; then
			echo "kopia: connect timed out after ${KOPIA_ATTEMPT_TIMEOUT}s -- the server took the connection but never completed the session." >&2
			echo "kopia: the usual cause is a credential mismatch; this service's kopia_password must equal the kopia server's client_${KOPIA_CLIENT_USERNAME} secret." >&2
		else
			echo "kopia server not reachable yet, retrying in 15s" >&2
		fi
		sleep 15
	done
fi

for path in $KOPIA_PATHS; do
	# `--clear-ignore` on every start, before anything is added below: a policy lives in
	# the repository and outlives this container, so a rule dropped from `service.yml`
	# would otherwise keep excluding its tree forever. Cleared and re-added is what makes
	# `backup.exclude` declarative -- git is the whole truth about what is skipped.
	# Its own invocation and not another flag on the one below, because the order kopia
	# applies `--clear-ignore` and `--add-ignore` within a single call is not specified,
	# and the wrong order would clear the rules it had just added.
	kopia policy set "/data/$path" \
		--inherit=false \
		--no-manual \
		--snapshot-time="$KOPIA_SNAPSHOT_TIME" \
		--run-missed=true \
		--ignore-identical-snapshots=true \
		--clear-ignore \
		--keep-latest="$KOPIA_KEEP_LATEST" \
		--keep-hourly=0 \
		--keep-daily="$KOPIA_KEEP_DAILY" \
		--keep-weekly="$KOPIA_KEEP_WEEKLY" \
		--keep-monthly="$KOPIA_KEEP_MONTHLY" \
		--keep-annual="$KOPIA_KEEP_ANNUAL"

	# `KOPIA_EXCLUDE` is one flat `<volume>:<rule>` list rather than a variable per
	# volume: a volume name may carry a `-`, which an environment variable name cannot,
	# so the per-volume form would need mangling on one side and `eval` on the other.
	# Built with `set --` because this is dash and there are no arrays; a rule therefore
	# may not contain whitespace, which the role's README states.
	set --
	for entry in ${KOPIA_EXCLUDE:-}; do
		case "$entry" in
			"$path":*) set -- "$@" --add-ignore="${entry#"$path":}" ;;
		esac
	done
	[ "$#" -eq 0 ] || kopia policy set "/data/$path" "$@"
done

exec kopia server start \
	--address='http://127.0.0.1:51516' \
	--insecure \
	--without-password \
	--no-ui \
	--no-grpc
