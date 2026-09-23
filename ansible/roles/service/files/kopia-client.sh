#!/bin/sh
# Kopia client sidecar: connects to the platform's Kopia server through Traefik,
# applies the snapshot policy declared in service.yml, and keeps a scheduler
# running so snapshots happen at KOPIA_SNAPSHOT_TIME.

# pipefail so that a failed `openssl s_client` in the fingerprint pipeline below is a
# failed fetch, and not the sha256 of the empty stream it left behind.
set -eu
set -o pipefail

export KOPIA_PASSWORD="$(cat /run/secrets/kopia_password)"
export KOPIA_CONFIG_PATH=/app/config/repository.config
export KOPIA_CACHE_DIRECTORY=/app/cache
export KOPIA_PERSIST_CREDENTIALS_ON_CONNECT=true
export KOPIA_USE_KEYRING=false

server_host="${KOPIA_SERVER_URL#https://}"
server_host="${server_host%%/*}"

# sha256 of nothing at all: belt and braces behind pipefail, because a fingerprint of
# the empty stream would be pinned forever and every later connection would be rejected.
EMPTY_DIGEST=E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855

# Test hosts serve Traefik's self-signed default certificate, which no CA vouches for:
# fetch it and pin it. Fetched on every attempt, not once before the retry loop -- the
# first attempts are expected to fail, because this runs while Traefik is still starting.
server_fingerprint() {
	fingerprint="$(openssl s_client -connect "$server_host:443" -servername "$server_host" < /dev/null 2> /dev/null \
		| openssl x509 -outform DER | sha256sum | awk '{ print toupper($1) }')" || return 1
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
