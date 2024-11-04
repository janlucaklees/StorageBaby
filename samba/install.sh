#
# Installing required packages
yay -S --needed --noconfirm \
  samba elasticsearch-bin fscrawler-bin \

#
# Setup Samba configuration
sudo stow -vv -t / config
sudo systemctl enable --now smb nmb

#
# Setup users
sudo smbpasswd -a jlk

useradd -m -G wheel mk
sudo smbpasswd -a mk

useradd -M -G wheel printer
sudo smbpasswd -a printer
