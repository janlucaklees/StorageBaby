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
doas cp config/usr/local/bin/snapraid-scrub.sh /usr/local/bin
doas chmod +x /usr/local/bin/snapraid-scrub.sh
doas cp config/etc/systemd/system/snapraid-scrub.service /etc/systemd/system
doas cp config/etc/systemd/system/snapraid-scrub.timer /etc/systemd/system
doas systemctl enable snapraid-scrub.timer

# Install my Snapraid Sync-Service
doas cp config/usr/local/bin/snapraid-sync.sh /usr/local/bin
doas chmod +x /usr/local/bin/snapraid-sync.sh
doas cp config/etc/systemd/system/snapraid-sync.service /etc/systemd/system
doas cp config/etc/systemd/system/snapraid-sync.timer /etc/systemd/system
doas systemctl enable snapraid-sync.timer

#
# Setup Mutt
# This is for now just a reminder to install and setup mutt, until I found a way to automate it.
