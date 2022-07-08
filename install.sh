#
# Installing required packages
yay -S --needed --noconfirm \
  mergerfs fuse mergerfs-tools-git snapraid smartmontools \
  samba elasticsearch fscrawler-bin \
  jellyfin aur/aspnet-runtime-5.0-bin \
  syncthing

#
# install configuration
sudo stow -vv -t / snapraid
sudo stow -vv -t / samba

#
# Drives

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
sudo systemctl enable --now smb nmb jellyfin syncthing@jlk

