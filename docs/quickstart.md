# Quickstart

From a clean workstation to a host that runs one service from this repository. Every
step is one command block; the README has the depth behind each one.

## 1. Workstation

```sh
sudo pacman -S --needed docker lefthook mise sops age
git clone git@github.com:janlucaklees/StorageBaby.git && cd StorageBaby
mise trust && mise run install-hooks && mise run devtools
mise tasks # every task with its arguments; there is no Makefile
```

Your age key is the one that opens every secret file. If you have none yet:

```sh
age-keygen >> ~/.config/sops/age/keys.txt # prints the public key; back the file up
```

Put the public key on the `&jlk` line of `.sops.yaml` and run `mise run updatekeys`
(README → "Secrets, in one minute").

## 2. Bootstrap the host

Copy `bootstrap.sh` anywhere on the Arch host and run it as root. `--local` keeps the
deploy timer off so nothing converges until you say so; `--branch` makes the host
follow a branch other than `stable`, which is how a rollout is tested.

```sh
doas bash bootstrap.sh --repo git@github.com:janlucaklees/StorageBaby.git --branch storagebaby-rollout --local
```

It runs `pacman -Syu` once: reboot afterwards if the kernel changed, or no container
starts. It prints two public keys:

- the **deploy key** → GitHub, Settings → Deploy keys, read-only
- the **age key** → `.sops.yaml`, under a new `&<Hostname>` anchor, listed in the
  host's own rule **and** the `hosts/shared/**` rule; then `mise run updatekeys`, commit,
  push

The host folder (`hosts/<Hostname>/`) and the inventory entry must spell the hostname
exactly as the machine does, case included (README → "New host").

## 3. Secrets

```sh
mise run sops hosts/StorageBaby/services/paperless/secrets.sops.yaml # opens nvim, re-encrypts on save
```

Replace every `REPLACE_ME` the README's operator table lists for the services you place.
Kopia's `client_<service>` and that service's `kopia_password` are the same string, kept
in two files on purpose. A B2 bucket name is global and public; Object Lock and lifecycle
rules off, Kopia manages retention itself.

## 4. Place one service and converge

Placement is the folder. On the branch the host follows, keep one folder under
`hosts/<Hostname>/services/` (traefik from `hosts/shared/` always runs) and park the
others in `hosts/<Hostname>/unplaced/`; bring them back one commit at a time. Fixes go to
the real branch and get merged in. The static suite is red by construction on such a
branch; that is fine.

On the host, with whatever used to own ports 80 and 443 stopped:

```sh
doas systemctl start storagebaby-deploy.service # does nothing on an unchanged checkout
journalctl -u storagebaby-deploy.service -n 50
doas storagebaby-svc ps yuzukam # also: start stop restart logs
curl -sI https://yuzukam.home.klees.io | head -1
```

`storagebaby-svc logs` follows; for a tail use
`journalctl _SYSTEMD_USER_UNIT=yuzukam.service -n 30` (`paperless-pod.service` for a pod).

## 5. Bring existing data

Before the service's first deploy, copy the old volume's **contents** into
`<class root>/<service>/<volume>`: pool class `/pool/apps/<svc>/<vol>`, fast class
`/var/lib/storagebaby/fast/<svc>/<vol>`. A postgres data directory copies as is when the
major version matches; otherwise dump and restore (README → "Databases"). Redis, Valkey,
Meilisearch and model caches are not copied.

Then deploy, hand each tree to the uid the container runs as, restart:

```sh
base=$(awk -F: '$1=="svc-paperless"{print $2}' /etc/subuid)
doas chown -R $((base + 69)):$((base + 69)) /var/lib/storagebaby/fast/paperless/database           # postgres uid 70
doas chown -R $((base + 999)):$((base + 999)) /pool/apps/paperless/data /pool/apps/paperless/media # app uid 1000
doas storagebaby-svc restart paperless
```

The first deploy of a service with copied data fails its health wait once, because of
exactly this ownership; expected (README → "Migrating existing service data").

## 6. Backups

Kopia's UI is `https://kopia.<domain>`, login `jlk` with `server_password`; it never asks
for the repository password, the server already holds it. A sidecar snapshots its paths
when it starts and nightly at 03:00; a manual `kopia snapshot create` that reports "no
files have been changed" is Kopia skipping an identical snapshot, not a failure:

```sh
doas /usr/local/sbin/podman-as svc-paperless podman exec paperless-backup kopia snapshot list
```

Restore from anywhere with the bucket credentials and `repository_password`:
`kopia repository connect s3 ...`, `kopia snapshot list --all`,
`kopia snapshot restore <id> <dir>`.

## 7. When everything is placed

Switch the host to `stable`: re-run bootstrap without `--branch`, or edit `BRANCH` in
`/etc/storagebaby/deploy.conf`; `doas systemctl enable --now storagebaby-deploy.timer`.
From then on CI moves `stable` and the host follows it every five minutes.
