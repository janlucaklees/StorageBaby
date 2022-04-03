# Updating packages and installing dependencies for this script
sudo pacman -Syu --needed --noconfirm base-devel git stow

# installing all kinds of stuff
yay -S --needed --noconfirm \
  mergerfs mergerfs-tools-git snapraid \
  samba elasticsearch fscrawler-bin \
  plex-media-server \
  jellyfin aur/aspnet-runtime-5.0-bin
  syncthing

# install configuration
sudo stow -vv -t / snapraid
sudo stow -vv -t / samba
sudo stow -vv -t / fstab

# Enable and start services
systemctl enable smb nmb plexmediaserver jellyfin syncthing@jlk
systemctl start  smb nmb plexmediaserver jellyfin syncthing@jlk
systemctl status smb nmb plexmediaserver jellyfin syncthing@jlk

