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

connect_to_server() {
	set -- kopia repository connect server \
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
if [ -f "$KOPIA_CONFIG_PATH" ] && ! kopia repository status > /dev/null 2>&1; then
	echo "kopia: the stored connection does not open, reconnecting" >&2
	rm -f "$KOPIA_CONFIG_PATH"
fi

if [ ! -f "$KOPIA_CONFIG_PATH" ]; then
	until connect_to_server; do
		echo "kopia server not reachable yet, retrying in 15s" >&2
		sleep 15
	done
fi

for path in $KOPIA_PATHS; do
	kopia policy set "/data/$path" \
		--inherit=false \
		--no-manual \
		--snapshot-time="$KOPIA_SNAPSHOT_TIME" \
		--run-missed=true \
		--ignore-identical-snapshots=true \
		--keep-latest="$KOPIA_KEEP_LATEST" \
		--keep-hourly=0 \
		--keep-daily="$KOPIA_KEEP_DAILY" \
		--keep-weekly="$KOPIA_KEEP_WEEKLY" \
		--keep-monthly="$KOPIA_KEEP_MONTHLY" \
		--keep-annual="$KOPIA_KEEP_ANNUAL"
done

exec kopia server start \
	--address='http://127.0.0.1:51516' \
	--insecure \
	--without-password \
	--no-ui \
	--no-grpc
