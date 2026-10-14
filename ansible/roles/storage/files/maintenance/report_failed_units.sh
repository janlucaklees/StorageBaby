#!/bin/bash
# Ask every service manager on this host for its failed units.
#
# Why this exists: the service role installs units into `svc-<name>`'s own systemd,
# and that manager has no way to send mail -- /etc/msmtprc is 0600 root. So a unit
# that fails there (an hourly kopia sync, a nightly database dump, a crashed pod)
# writes one line into a journal nobody reads. Root can reach those managers, which
# is exactly what /usr/local/sbin/storagebaby-svc does, so the nightly mail is the
# right place to surface them.
#
# It knows nothing about any particular service, on purpose: the next service that
# fails at 04:00 is covered without anybody editing this role again.
#
# It reports. It never fails the run -- a failed unit somewhere is news, not a
# reason to abort parity -- so every command here is tolerant and the script exits 0.

found=0

for user in $(getent passwd | awk -F: '$1 ~ /^svc-/ { print $1 }' | sort); do
	# `|| true` and a discarded stderr: a service user whose manager is not running
	# (linger off, user mid-removal) is not an error worth failing a nightly run for.
	units="$(systemctl --user -M "${user}@" list-units --state=failed --no-legend --plain 2> /dev/null || true)"

	if [[ -n "$units" ]]; then
		found=1
		echo "${user}:"
		echo "${units}" | sed 's/^/  /'
	fi
done

if [[ "$found" -eq 0 ]]; then
	echo "No failed units in any service manager."
fi

exit 0
