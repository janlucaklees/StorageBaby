# StorageBaby

Git-driven configuration for my self-hosted services and the Arch hosts they
run on. Design: `docs/superpowers/specs/2026-09-21-gitops-podman-platform-design.md`.

## Layout

- `hosts/<host>/host.yml` — everything specific to one host (domain, storage
  roots, later disks/snapraid/samba).
- `hosts/<host>/services/<name>/` — a service placed on that host.
- `hosts/shared/services/<name>/` — a service that runs on every host.
- `ansible/` — the site playbook and roles that turn the above into a running host.
- `tests/` — static checks and the Molecule integration scenario.

Each service folder holds `service.yml` (port, domain, volumes, binds, secrets,
backup policy), `quadlet/*.j2` (Podman Quadlet units), optional `config/`, and
`secrets.sops.yaml`.

## Services

Domains are relative to the host's own domain — `jellyfin.home.klees.io` on
storagebaby. "auto" means `AutoUpdate=registry` plus the service user's
`podman-auto-update.timer`, which rolls back an image that fails its health check.

| Service                              | Domain       | Image and updates                                                 | Phase |
| ------------------------------------ | ------------ | ----------------------------------------------------------------- | ----- |
| traefik (`hosts/shared`, every host) | `traefik.*`  | `docker.io/library/traefik:v3` — auto                             | 1     |
| yuzukam                              | `yuzukam.*`  | `ghcr.io/janlucaklees/yuzukam:latest` — auto                      | 2     |
| stirling-pdf                         | `stirling.*` | `docker.stirlingpdf.com/stirlingtools/stirling-pdf:latest` — auto | 2     |
| jellyfin                             | `jellyfin.*` | `lscr.io/linuxserver/jellyfin:latest` — auto                      | 2     |
| kopia                                | `kopia.*`    | `docker.io/kopia/kopia:0.23.1` — pinned, bumped in git            | 2     |
| paperless-upload                     | none         | host build (`.build` unit from the service's own `config/build/`) | 2     |

Everything but traefik is placed on storagebaby; the test host places the same
five folders by symlink. Still on `docker-compose.yml` and waiting: `nextcloud/`,
`paperless/`, `openarchiver/`, `immich/` (Phase 3), `samba/` and `snapraid/`
(Phase 4).

Kopia is pinned on purpose — a kopia upgrade can carry a repository format
upgrade, which is not a decision for a nightly timer.

## Operator steps before the first storagebaby deploy

Converging storagebaby for the first time takes six things that are not in git,
because they are either a secret or somebody else's data:

1. **Open the shared trees to the services that read them.** The role creates a
   bind directory only when it is missing, and never touches the permissions of
   one that exists — so both of these are the operator's, once:

   ```bash
   doas chmod -R o+rX /pool/shared/media
   doas chgrp -R scans /pool/shared/scans && doas chmod -R g+rwX /pool/shared/scans
   ```

   Jellyfin needs the first because the linuxserver image drops its supplementary
   groups when s6 switches to its own user, so group access never reaches the app
   process — `hosts/storagebaby/services/jellyfin/README.md` has the measurement.
   The uploader keeps its groups, so the `scans` group is enough for it.

2. **Put the real Backblaze credentials into kopia's secrets** — both are
   `REPLACE_ME` in git — and confirm `s3_endpoint` and `s3_bucket` in
   `hosts/storagebaby/services/kopia/service.yml` against the live B2 account.
   They were written from the old README, not read off the account.

   ```bash
   make sops FILE=hosts/storagebaby/services/kopia/secrets.sops.yaml
   ```

3. **Back up kopia's repository password outside this repository.** It was
   generated for the migration and lives only in `secrets.sops.yaml`. Kopia
   derives the repository's encryption keys from it: no reset, no escrow, no way
   into the snapshots without it.

4. **Replace the paperless-upload token.** `secrets.sops.yaml` holds
   `REPLACE_ME_paperless_api_token` — the file it was to be carried over from was
   empty. Uploads fail until Phase 3 delivers Paperless either way.

5. **Expect existing volume directories to keep their owner and mode.** A converge
   creates the ones that are missing and leaves the rest alone, so anything already
   on the pool stays exactly as it is.

6. **Check GPU transcoding after cutover.** Jellyfin reaches `/dev/dri` through a
   udev rule installed by `host_base` on a `gpu: true` host. The test VM has no
   GPU, so nothing in this repo proves it works — the first real converge is the
   first test.

## New host

Copy the script to the fresh Arch install and run it as root:

    scp bootstrap.sh root@<host>:/root/ && ssh root@<host> 'bash /root/bootstrap.sh --repo git@github.com:janlucaklees/StorageBaby.git'

`stable` is created by CI on the first green push to master, so push and let CI
run before bootstrapping the first host.

Protect `stable` on GitHub: no direct pushes, no force pushes, no deletion — it
is moved by CI alone. GitHub Actions must still be allowed to push to it: the
`promote` job fast-forwards `stable` with the workflow token, so if "restrict
who can push" is enabled, add the Actions actor to the allow list or promotion
stops there.

The host's hostname must equal its folder name under `hosts/` — the deploy unit
converges `--limit <hostname>` and fails loudly if no such folder exists.

Add the printed deploy key to the repository, then add the printed age recipient
to `.sops.yaml` in two places: the host's own rule, and the `hosts/shared/**`
rule — every host runs the shared services and has to decrypt their secrets.
Then run `sops updatekeys` on every affected `*.sops.yaml` —
`make sops FILE=...` opens one for editing — then commit and push. The host
pulls `stable` every 5 minutes.

Until the host's age recipient is in `.sops.yaml` and `sops updatekeys` has run
on the secret files it needs, its first converge fails at secret decryption.
That is expected: the host cannot read anything it was not encrypted to.

## Operating a service

Every service runs as its own lingering user `svc-<name>`, so its units belong
to that user's systemd manager, not the system one. The Makefile wraps that —
run these on the host as root (they are the only targets that do not go through
the devtools image):

    make ps SERVICE=traefik
    make start SERVICE=traefik
    make stop SERVICE=traefik
    make restart SERVICE=traefik
    make logs SERVICE=traefik

They are thin wrappers, so the raw forms still work:

    systemctl --user -M svc-traefik@ status traefik.service
    systemctl --user -M svc-traefik@ restart traefik.service

`journalctl` has no `--user -M` equivalent, so read logs by unit name instead:

    journalctl _SYSTEMD_USER_UNIT=traefik.service -f

Nothing is ever changed on a host by hand — this is for looking, and for the
occasional restart. The fix belongs in git.

## Working on the repo

    make devtools              # build the tooling image (once)
    make format                # prettier over the whole repo
    make fmt-check             # check only, no writes
    make test-static           # contract, secrets, render checks
    make test-integration      # Molecule scenario test-ci in a KVM VM
    make molecule CMD=converge # a single Molecule step in that scenario
    make molecule-login        # SSH into the running test VM
    make molecule-exec CMD='podman ps -a'   # one command on it, no TTY needed
    make test-clean            # destroy the VM and drop the Molecule cache
    make sops FILE=hosts/shared/services/traefik/secrets.sops.yaml
    make install-hooks         # once per clone: lefthook's formatting hook

Docker and lefthook are all the workstation needs for everything but the
integration tests. Those drive real KVM machines through the host's libvirt, so
they additionally need `qemu-base libvirt dnsmasq iptables-nft`, `libvirtd`
enabled, and libvirt's `default` network active. On a machine that also runs
Docker, set `firewall_backend = "iptables"` in `/etc/libvirt/network.conf` —
with the nftables backend Docker's rules drop the VM network's traffic.
