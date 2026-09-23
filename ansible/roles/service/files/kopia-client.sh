#!/bin/sh
# Kopia client sidecar: connects to the platform's Kopia server through Traefik,
# applies the snapshot policy declared in service.yml, and keeps a scheduler
# running so snapshots happen at KOPIA_SNAPSHOT_TIME.
set -eu

export KOPIA_PASSWORD="$(cat /run/secrets/kopia_password)"
export KOPIA_CONFIG_PATH=/app/config/repository.config
export KOPIA_CACHE_DIRECTORY=/app/cache
export KOPIA_PERSIST_CREDENTIALS_ON_CONNECT=true
export KOPIA_USE_KEYRING=false

server_host="${KOPIA_SERVER_URL#https://}"
server_host="${server_host%%/*}"

if [ ! -f "$KOPIA_CONFIG_PATH" ]; then
	set -- kopia repository connect server \
		--url="$KOPIA_SERVER_URL" \
		--override-username="$KOPIA_CLIENT_USERNAME" \
		--override-hostname="$KOPIA_CLIENT_HOSTNAME"
	if [ "${KOPIA_PIN_SERVER_CERT:-0}" = "1" ]; then
		# Test hosts serve Traefik's self-signed default certificate: pin it.
		fingerprint="$(openssl s_client -connect "$server_host:443" -servername "$server_host" < /dev/null 2> /dev/null \
			| openssl x509 -outform DER | sha256sum | awk '{ print toupper($1) }')"
		set -- "$@" --server-cert-fingerprint="$fingerprint"
	fi
	until "$@"; do
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
