#
# Installing required packages
yay -S --needed --noconfirm \
  mergerfs fuse mergerfs-tools-git snapraid smartmontools \
  samba elasticsearch fscrawler-bin \
  jellyfin aur/aspnet-runtime-5.0-bin \
  syncthing

#
# Setup Snapraid configuration and tooling
sudo stow -vv -t / snapraid
sudo chmod +x /usr/local/bin/snapraid-scrub.sh
sudo systemctl enable snapraid-scrub.service snapraid-scrub.timer

#
# Setup Samba configuration
sudo stow -vv -t / samba
sudo systemctl enable --now smb nmb

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
# Enable and start services
sudo systemctl enable --now jellyfin syncthing@jlk

