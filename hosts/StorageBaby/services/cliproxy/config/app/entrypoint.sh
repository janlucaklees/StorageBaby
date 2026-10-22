#!/bin/sh
# Start CLIProxyAPI with the half of its configuration this repository owns.
#
# The application keeps one configuration file and its management panel rewrites that file
# whenever anybody changes a setting in the browser. That is how it is designed to be
# operated, and it is the opposite of how every other service in this repository is
# configured -- so the split is drawn here, in one place, explicitly:
#
#   this repository's, re-applied on every start:  access.api-keys, server.port,
#                                                  oauth.auth-dir
#   the panel's, from the first start onwards:     everything else in /data/config.yaml
#   nobody's, until the operator sets it:          either secret, while it still reads
#                                                  as the placeholder below
set -eu

CONFIG=/data/config.yaml
AUTH_DIR=/data/auths

# What both secrets hold in git. A placeholder is not a credential: it is committed, it is
# the same on every host that places this folder, and it must never be accepted as one.
PLACEHOLDER=set-me-see-the-cliproxy-README

if [ ! -f "$CONFIG" ]; then
	cp /config/config.seed.yaml "$CONFIG"
	echo "cliproxy: seeded $CONFIG from the copy in git"
fi

# The client API keys are this repository's, on every start: a key added in the panel does
# not outlive a restart, and sops stays the one place they are written down. While the
# secret is still the placeholder the key becomes a fresh random string instead -- the
# proxy then answers 401 to every caller including whoever read the placeholder out of
# git, which is what makes placing this folder before the secret exists a safe thing to do.
key="${CPA_CLIENT_API_KEY:-}"
if [ -z "$key" ] || [ "$key" = "$PLACEHOLDER" ]; then
	key="unset-$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
	echo "cliproxy: client_api_key is still the placeholder -- the proxy answers only to a random key nobody holds"
fi

# yq and not sed: the application re-serialises this file in its own style on every
# settings change -- four-space indent, quoted scalars -- and the list is only reliably
# addressable through a YAML parser. `config/build/Containerfile` is why yq is in the image.
CPA_KEY="$key" CPA_PORT="$CPA_CONTAINER_PORT" CPA_AUTH_DIR="$AUTH_DIR" yq -i '
	.access.api-keys = [strenv(CPA_KEY)] |
	.server.port = env(CPA_PORT) |
	.oauth.auth-dir = strenv(CPA_AUTH_DIR)
' "$CONFIG"

# MANAGEMENT_PASSWORD and not `management.secret-key`: the environment variable is what
# enables the management routes and lifts the localhost-only restriction, and it leaves no
# credential in a file the panel rewrites -- a plaintext secret-key would be replaced by
# its own bcrypt hash on the next load.
mgmt="${CPA_MANAGEMENT_PASSWORD:-}"
if [ -n "$mgmt" ] && [ "$mgmt" != "$PLACEHOLDER" ]; then
	MANAGEMENT_PASSWORD="$mgmt"
	export MANAGEMENT_PASSWORD
else
	echo "cliproxy: management_password is still the placeholder -- the management API stays disabled"
fi
unset CPA_CLIENT_API_KEY CPA_MANAGEMENT_PASSWORD

exec /CLIProxyAPI/CLIProxyAPI -config "$CONFIG"
