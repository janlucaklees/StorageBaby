# Updating packages and installing dependencies for this script
sudo pacman -Syu --needed --noconfirm base-devel git stow

# installing all kinds of stuff
yay -S --needed --noconfirm \
  mergerfs snapraid \
  samba elasticsearch fscrawler-bin \
  plex-media-server \
  rsync

# install configuration
sudo stow -vv -t / samba
sudo stow -vv -t / snapraid

