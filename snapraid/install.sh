#
# Installing required packages
yay -S --needed --noconfirm mergerfs fuse mergerfs-tools-git snapraid smartmontools mutt

#
# Setup the Mounts

# Enable and mount data drives
doas cp config/etc/systemd/system/mnt-data-data1.mount /etc/systemd/system
doas systemctl enable --now mnt-data-data1.mount
doas cp config/etc/systemd/system/mnt-data-data2.mount /etc/systemd/system
doas systemctl enable --now mnt-data-data2.mount
doas cp config/etc/systemd/system/mnt-data-data3.mount /etc/systemd/system
doas systemctl enable --now mnt-data-data3.mount

# Enable and mount parity drives
doas cp config/etc/systemd/system/mnt-parity-parity1.mount /etc/systemd/system
doas systemctl enable --now mnt-parity-parity1.mount

# Enable and mount the pool
doas cp config/etc/systemd/system/pool.mount /etc/systemd/system
doas systemctl enable --now pool.mount


#
# Setup Snapraid configuration and tooling

# Copy Snapraid configuration
doas cp config/etc/snapraid.conf /etc

# Install my Snapraid Scrub-Service
doas mkdir /opt/scripts
doas cp config/opt/scripts/storage-maintenance-unattended.sh /opt/scripts
doas chmod +x /opt/scripts/storage-maintenance-unattended.sh
doas cp config/etc/systemd/system/storage-maintenance.service /etc/systemd/system
doas cp config/etc/systemd/system/storage-maintenance.timer /etc/systemd/system
doas systemctl enable storage-maintenance.timer

#
# Setup Mutt
# This is for now just a reminder to install and setup mutt, until I found a way to automate it.
