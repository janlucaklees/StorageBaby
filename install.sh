#
# Installing required packages
yay -S --needed --noconfirm \
  mergerfs fuse mergerfs-tools-git snapraid smartmontools \
  samba elasticsearch-bin fscrawler-bin \
  syncthing \
  docker docker-compose \
  cups cups-pdf avahi kyocera-cups kyocera-ecosys-m552x-p502x

#
# Setup Snapraid configuration and tooling
sudo stow -vv -t / snapraid
sudo chmod +x /usr/local/bin/snapraid-scrub.sh
sudo systemctl enable snapraid-scrub.timer

#
# Setup Samba configuration
sudo stow -vv -t / samba
sudo systemctl enable --now smb nmb

#
# Setup Printer Sharing
sudo stow -vv -t / cups

#
# Setup drive mounts
# Install configuration
sudo stow -vv -t / mounts
# Enable and mount data drives
sudo systemctl enable --now mnt-data-data1.mount
sudo systemctl enable --now mnt-data-data2.mount
# Enable and mount parity drives
sudo systemctl enable --now mnt-parity-parity1.mount
# Enable and mount the pool
sudo systemctl enable --now pool.mount

#
# Enable and start Syncthing
sudo systemctl enable --now syncthing@jlk

# TODO: Start Docker services
#
