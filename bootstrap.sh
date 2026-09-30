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
if ! pacman -Qq chaotic-keyring chaotic-mirrorlist > /dev/null 2>&1; then
	pacman-key --recv-key "$CHAOTIC_KEY" --keyserver "$CHAOTIC_KEYSERVER"
	pacman-key --lsign-key "$CHAOTIC_KEY"
	pacman -U --noconfirm \
		https://cdn-mirror.chaotic.cx/chaotic-aur/chaotic-keyring.pkg.tar.zst \
		https://cdn-mirror.chaotic.cx/chaotic-aur/chaotic-mirrorlist.pkg.tar.zst
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

pacman -Syu --noconfirm --needed git ansible sops age podman passt openssh make

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

cat > /etc/systemd/system/storagebaby-deploy.service << 'EOF'
[Unit]
Description=StorageBaby deploy (ansible-pull of the stable branch)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/storagebaby/deploy.conf
Environment="GIT_SSH_COMMAND=ssh -i /etc/storagebaby/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
ExecStart=/usr/bin/ansible-pull --url ${REPO_URL} --checkout ${BRANCH} --directory /var/lib/storagebaby/repo --inventory ansible/inventory/hosts.yml --limit %H --only-if-changed $EXTRA_ARGS ansible/playbook.yml
# Without this, a host whose hostname has no hosts/<name> folder converges nothing
# at all -- --limit matches no host and ansible-pull still exits 0. Fail visibly.
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
