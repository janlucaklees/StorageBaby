# installing all kinds of stuff
yay -S --needed --noconfirm \
  mergerfs fuse mergerfs-tools-git snapraid \
  samba elasticsearch fscrawler-bin \
  jellyfin aur/aspnet-runtime-5.0-bin \
  syncthing

# install configuration
sudo stow -vv -t / snapraid
sudo stow -vv -t / samba

# Drives
sudo stow -vv -t / mounts
sudo systemctl enable --now mnt-data-data1.mount
sudo systemctl enable --now mnt-data-data2.mount
sudo systemctl enable --now mnt-parity-parity1.mount

# Enable and start services
sudo systemctl enable --now smb nmb jellyfin syncthing@jlk

