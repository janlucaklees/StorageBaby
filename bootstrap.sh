#!/bin/bash
# One-time host setup. Run as root on a fresh Arch host:
#   bootstrap.sh --repo git@github.com:janlucaklees/StorageBaby.git [--branch stable] [--local]
# --local: do not enable the deploy timer (tests and dry runs).
set -euo pipefail

REPO_URL=""
BRANCH="stable"
LOCAL=0
while [ $# -gt 0 ]; do
	case "$1" in
		--repo)
			REPO_URL="$2"
			shift 2
			;;
		--branch)
			BRANCH="$2"
			shift 2
			;;
		--local)
			LOCAL=1
			shift
			;;
		*)
			echo "unknown argument: $1" >&2
			exit 2
			;;
	esac
done
[ -n "$REPO_URL" ] || {
	echo "--repo is required" >&2
	exit 2
}
[ "$(id -u)" -eq 0 ] || {
	echo "run as root" >&2
	exit 2
}

# Chaotic-AUR, before the upgrade below so that the upgrade already knows it. mergerfs
# comes from there and the `storage` role installs it -- but the role never runs a full
# upgrade and never reboots (package upgrades are the operator's job), so a repository it
# adds after this script would first be synced by the *role's* database refresh and its
# packages resolved against a system the operator has not upgraded since. Adding it here
# means the one `-Syu` a host ever gets from this repository covers it.
#
# The steps mirror `ansible/roles/storage/tasks/tools.yml` exactly, marker lines included,
# so the two agree and both are idempotent: the role finds its own `blockinfile` markers
# and reports no change, and `tests/static/test_bootstrap.py` holds the values identical to
# the role's defaults.
CHAOTIC_KEY=3056513887B78AEB
CHAOTIC_KEYSERVER=keyserver.ubuntu.com
CHAOTIC_FETCH_ATTEMPTS=3
CHAOTIC_FETCH_DELAY=30
if ! pacman -Qq chaotic-keyring chaotic-mirrorlist > /dev/null 2>&1; then
	pacman-key --recv-key "$CHAOTIC_KEY" --keyserver "$CHAOTIC_KEYSERVER"
	pacman-key --lsign-key "$CHAOTIC_KEY"
	# Retried, and only this step. The two packages come straight off
	# cdn-mirror.chaotic.cx over HTTPS -- not through a mirrorlist, because they are what
	# makes the mirrorlist exist -- so there is nothing between the CDN and the host to
	# fail over to. It answered 503 for four minutes during a run and took the whole
	# bootstrap with it, on a fetch that has nothing to do with the host being set up.
	# The key import above is a different server and stays a single attempt.
	for attempt in $(seq 1 "$CHAOTIC_FETCH_ATTEMPTS"); do
		if pacman -U --noconfirm \
			https://cdn-mirror.chaotic.cx/chaotic-aur/chaotic-keyring.pkg.tar.zst \
			https://cdn-mirror.chaotic.cx/chaotic-aur/chaotic-mirrorlist.pkg.tar.zst; then
			break
		fi
		if [ "$attempt" -ge "$CHAOTIC_FETCH_ATTEMPTS" ]; then
			echo "chaotic-aur packages could not be fetched in $CHAOTIC_FETCH_ATTEMPTS attempts" >&2
			exit 1
		fi
		sleep "$CHAOTIC_FETCH_DELAY"
	done
fi
# The markers are ansible's, not decoration: `blockinfile` in the role looks for exactly
# these two lines. Without them the role would append a *second* `[chaotic-aur]` section
# on the first converge and report changed on a host that was already correct.
if ! grep -q '^# BEGIN ANSIBLE MANAGED chaotic-aur$' /etc/pacman.conf; then
	# An append assumes the file ends in a newline. Arch's shipped pacman.conf does, but if
	# it ever did not, the `# BEGIN` marker would be concatenated onto the last line -- the
	# grep above would not find it on a re-run and would append a second time, and the
	# role's `blockinfile` would not find its marker either, which is the duplicate section
	# this whole guard exists to prevent. A command substitution strips trailing newlines,
	# so a non-empty result means the last byte is not one.
	if [ -n "$(tail -c1 /etc/pacman.conf)" ]; then
		printf '\n' >> /etc/pacman.conf
	fi
	cat >> /etc/pacman.conf << 'EOF'
# BEGIN ANSIBLE MANAGED chaotic-aur
[chaotic-aur]
Include = /etc/pacman.d/chaotic-mirrorlist
# END ANSIBLE MANAGED chaotic-aur
EOF
fi

pacman -Syu --noconfirm --needed git ansible sops age podman passt openssh

install -d -m 0700 /etc/storagebaby
if [ ! -f /etc/storagebaby/age.key ]; then
	age-keygen -o /etc/storagebaby/age.key 2> /dev/null
	chmod 0600 /etc/storagebaby/age.key
fi
age-keygen -y /etc/storagebaby/age.key > /etc/storagebaby/age.pub
if [ ! -f /etc/storagebaby/deploy_key ]; then
	ssh-keygen -q -t ed25519 -N '' -C "storagebaby-deploy@$(hostname)" -f /etc/storagebaby/deploy_key
fi
cat > /etc/storagebaby/deploy.conf << EOF
REPO_URL=${REPO_URL}
BRANCH=${BRANCH}
EXTRA_ARGS=
EOF
chmod 0600 /etc/storagebaby/deploy.conf

# The deploy tick, identical to ansible/roles/host_base/files/storagebaby-deploy (a static
# test holds them so). Embedded because the first tick runs before host_base ever has.
cat > /usr/local/sbin/storagebaby-deploy << 'DEPLOY_SCRIPT'
#!/bin/bash
# One deploy tick. Run by storagebaby-deploy.service as root.
#
# Installed twice from one text: by the host_base role from this file, and by bootstrap.sh
# from a heredoc, because the first tick of a fresh host happens before host_base has ever
# run. tests/static/test_bootstrap.py holds the two byte-identical.
#
# What it does: fetch the platform repository, then every external repository this host
# points at (hosts/<host>/services/<name>/source.yml and hosts/shared/services/*/source.yml),
# and run the playbook only when any of those checkouts differs from what the last
# *successful* converge saw -- which is also what makes a failed converge retry on the next
# tick instead of waiting for the next commit, as `ansible-pull --only-if-changed` did.
set -euo pipefail

# REPO_URL, BRANCH, EXTRA_ARGS, written by bootstrap.sh.
. /etc/storagebaby/deploy.conf

REPO_DIR=/var/lib/storagebaby/repo
SOURCES_DIR=/var/lib/storagebaby/sources
STATE_FILE=/var/lib/storagebaby/deploy.state
HOST=$(uname -n)

export SOPS_AGE_KEY_FILE=/etc/storagebaby/age.key
# A private repository without a usable token fails here and now, instead of hanging on a
# prompt no one will answer.
export GIT_TERMINAL_PROMPT=0
# Whatever credential helper the host's git might have configured is not consulted: the
# token for an external repository comes from the host's sources file and nowhere else.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0=

# The token reaches git through GIT_ASKPASS and the environment, never through the remote
# URL and never through .git/config -- so it is nowhere on disk in the clear. The askpass
# helper itself is a few lines on tmpfs, removed when this run ends.
ASKPASS=$(mktemp /run/storagebaby-deploy.XXXXXX)
trap 'rm -f "$ASKPASS"' EXIT
cat > "$ASKPASS" << 'ASKPASS_SCRIPT'
#!/bin/sh
case "$1" in
	Username*) printf '%s\n' x-access-token ;;
	*) printf '%s\n' "${STORAGEBABY_GIT_TOKEN:-}" ;;
esac
ASKPASS_SCRIPT
chmod 0700 "$ASKPASS"
export GIT_ASKPASS=$ASKPASS

# sync_checkout <dir> <url> <ref>: clone or fetch, check out origin/<ref> detached, print
# the resulting HEAD. Always a hard reset and a clean: a host never has local changes in
# these checkouts, and a force-push upstream must not wedge the tick.
sync_checkout() {
	local dir=$1 url=$2 ref=$3
	if [ ! -d "$dir/.git" ]; then
		git clone --quiet --no-checkout "$url" "$dir"
	fi
	git -C "$dir" remote set-url origin "$url"
	git -C "$dir" fetch --quiet --prune origin "+refs/heads/$ref:refs/remotes/origin/$ref"
	git -C "$dir" checkout --quiet --detach "origin/$ref"
	git -C "$dir" reset --quiet --hard
	git -C "$dir" clean --quiet -fdx
	git -C "$dir" rev-parse HEAD
}

# One line per pointer of this host: "<name> <repo> <ref> <path>".
list_pointers() {
	python3 - "$REPO_DIR" "$HOST" << 'PY'
import glob
import os
import sys

import yaml

repo, host = sys.argv[1], sys.argv[2]
pointers = sorted(
    glob.glob(f"{repo}/hosts/shared/services/*/source.yml")
    + glob.glob(f"{repo}/hosts/{host}/services/*/source.yml")
)
for pointer in pointers:
    with open(pointer) as fh:
        source = yaml.safe_load(fh)
    print(os.path.basename(os.path.dirname(pointer)), source["repo"], source["ref"], source["path"])
PY
}

# The token of one pointer, or nothing: no entry means an anonymous clone, which is what a
# public repository needs. SOURCE_TOKENS is filled below, once the platform repository --
# which carries the file -- is fetched.
git_token_of() {
	printf '%s' "$SOURCE_TOKENS" \
		| python3 -c 'import json, sys; print(json.load(sys.stdin).get(sys.argv[1], {}).get("token", ""))' "$1"
}

install -d -m 0755 /var/lib/storagebaby
install -d -m 0700 "$SOURCES_DIR"

echo "platform repository: $REPO_URL ($BRANCH)"
repo_sha=$(sync_checkout "$REPO_DIR" "$REPO_URL" "$BRANCH")
if [ ! -d "$REPO_DIR/hosts/$HOST" ]; then
	echo "the repository has no hosts/$HOST folder; nothing to converge" >&2
	exit 1
fi
desired="repo $repo_sha"

# The host's git tokens, decrypted once per tick and held in memory only:
# hosts/<host>/secrets/sources.sops.yaml maps a pointer name to { token }. No file means
# every pointed repository is public.
SOURCE_TOKENS='{}'
if [ -f "$REPO_DIR/hosts/$HOST/secrets/sources.sops.yaml" ]; then
	SOURCE_TOKENS=$(sops --decrypt --output-type json "$REPO_DIR/hosts/$HOST/secrets/sources.sops.yaml")
fi

names=()
# Captured first and not read through a process substitution: under `set -e` a python
# failure inside `<(...)` would end the loop quietly with half the pointers, and the
# state file would then say "converged" about repositories that were never fetched.
pointer_lines=$(list_pointers)
while read -r name url ref path; do
	[ -n "$name" ] || continue
	names+=("$name")
	echo "external repository for $name: $url ($ref, $path)"
	STORAGEBABY_GIT_TOKEN=$(git_token_of "$name")
	export STORAGEBABY_GIT_TOKEN
	sha=$(sync_checkout "$SOURCES_DIR/$name" "$url" "$ref")
	unset STORAGEBABY_GIT_TOKEN
	if [ ! -f "$SOURCES_DIR/$name/$path/service.yml" ]; then
		echo "$name: $url ($ref) has no $path/service.yml" >&2
		exit 1
	fi
	desired+=$'\n'"source:$name $sha"
done <<< "$pointer_lines"

# A checkout nothing points at any more goes, so the sources directory mirrors the placement.
shopt -s nullglob
for existing in "$SOURCES_DIR"/*/; do
	existing=$(basename "$existing")
	keep=0
	for name in "${names[@]}"; do
		[ "$name" = "$existing" ] && keep=1
	done
	if [ "$keep" -eq 0 ] && [[ "$existing" =~ ^[a-z0-9-]+$ ]]; then
		echo "removing the fetched repository of $existing: no pointer names it any more"
		rm -rf "${SOURCES_DIR:?}/$existing"
	fi
done

if [ -f "$STATE_FILE" ] && [ "$(cat "$STATE_FILE")" = "$desired" ]; then
	echo "nothing changed since the last converge; not running the playbook"
	exit 0
fi

cd "$REPO_DIR"
# shellcheck disable=SC2086
/usr/bin/ansible-playbook --inventory ansible/inventory/hosts.yml --limit "$HOST" $EXTRA_ARGS ansible/playbook.yml
printf '%s\n' "$desired" > "$STATE_FILE"
DEPLOY_SCRIPT
chmod 0755 /usr/local/sbin/storagebaby-deploy

cat > /etc/systemd/system/storagebaby-deploy.service << 'EOF'
[Unit]
Description=StorageBaby deploy (fetch the stable branch and every pointed repository, converge on change)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/storagebaby/deploy.conf
Environment="GIT_SSH_COMMAND=ssh -i /etc/storagebaby/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
ExecStart=/usr/local/sbin/storagebaby-deploy
# Without this, a host whose hostname has no hosts/<name> folder converges nothing
# at all -- --limit matches no host and the playbook still exits 0. Fail visibly.
ExecStartPost=/usr/bin/test -d /var/lib/storagebaby/repo/hosts/%H
EOF

cat > /etc/systemd/system/storagebaby-deploy.timer << 'EOF'
[Unit]
Description=Run the StorageBaby deploy periodically

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
if [ "$LOCAL" -eq 0 ]; then
	systemctl enable --now storagebaby-deploy.timer
fi

echo
echo "Add this as a read-only deploy key on the repository:"
cat /etc/storagebaby/deploy_key.pub
echo
echo "Add this age recipient to .sops.yaml under this host's own rule AND under the"
echo "hosts/shared/** rule (every host runs the shared services), then run sops updatekeys"
echo "on every affected *.sops.yaml:"
cat /etc/storagebaby/age.pub
