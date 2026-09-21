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

cat > /etc/systemd/system/storagebaby-deploy.service << 'EOF'
[Unit]
Description=StorageBaby deploy (ansible-pull of the stable branch)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/storagebaby/deploy.conf
Environment=GIT_SSH_COMMAND=ssh -i /etc/storagebaby/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new
ExecStart=/usr/bin/ansible-pull --url ${REPO_URL} --checkout ${BRANCH} --directory /var/lib/storagebaby/repo --inventory ansible/inventory/hosts.yml --limit %H --only-if-changed $EXTRA_ARGS ansible/playbook.yml
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
echo "Add this age recipient to .sops.yaml under this host's rule, then run sops updatekeys:"
cat /etc/storagebaby/age.pub
