## Add new Data disk

### Prepare the disk
- Create GPT partition on disk using `fdisk /dev/sxx`
  - Size: Whole disk
  - Type: Linux Filesystem
- Create filesystem `mkfs.ext4 /dev/sxx1/`

### Create a systemd unit for mounting the disk on boot
- Create systemd unit, just copy one of the existing disks.
  - Find partition uuid by running `lsblk -o NAME,SIZE,TYPE,MOUNTPOINTS,PARTUUID`
- Copy the systemd unit to `/etc/systemd/system`.
- Enable the new unit with `systemctl enable --now mnt-data-dataX.mount`
- Verify the mount with `lsblk`
- Add instructions to copy and enable the systemd unit to the `install.sh`

### Update the snapraid conf
- Copy the `snapraid.content` file to the new disk.
- Update the `snapraid.conf` in this repo and copy it to `/etc/snapraid.conf`.
- Run `snapraid sync`.

### Adapt the mergerfs mount
- Add the systemd unit to the list of required units in `pool.mount`
- The disk is added automatically to the pool.
