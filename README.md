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
five folders by symlink. Still on `docker-compose.yml` and waiting: `nextcloud/`
and `paperless/` (tracked) plus `immich/` and `openarchiver/` (untracked working
copies) in Phase 3, then `samba/` and `snapraid/` in Phase 4.

Kopia is pinned on purpose — a kopia upgrade can carry a repository format
upgrade, which is not a decision for a nightly timer.

## Operator steps before and right after the first storagebaby deploy

Three things have to be done by hand **before** converging storagebaby the first
time, because they are secrets. The fourth can only be done **after** that first
converge, because it needs groups the converge creates:

1. **Put the real Backblaze credentials into kopia's secrets** — both are
   `REPLACE_ME` in git — and confirm `s3_endpoint` and `s3_bucket` in
   `hosts/storagebaby/services/kopia/service.yml` against the live B2 account.
   They were written from the old README, not read off the account.

   ```bash
   make sops FILE=hosts/storagebaby/services/kopia/secrets.sops.yaml
   ```

   ☐ **Check whether that bucket already holds a repository.** `start.sh` connects
   if it can and creates if it cannot, so a fresh bucket needs nothing more. But a
   bucket that already holds the old stack's repository needs its **existing**
   password put into `repository_password` in the same `secrets.sops.yaml`, in
   place of the one generated for the migration — otherwise both the connect and
   the create fail and the unit restart-loops.
   `hosts/storagebaby/services/kopia/README.md` spells the two cases out.

2. **Back up kopia's repository password outside this repository.** If the bucket
   is fresh, the generated one in `secrets.sops.yaml` is what the repository will
   be encrypted with, and it lives nowhere else. Kopia derives the repository's
   encryption keys from it: no reset, no escrow, no way into the snapshots
   without it.

3. **Replace the paperless-upload token.** `secrets.sops.yaml` holds
   `REPLACE_ME_paperless_api_token` — the file it was to be carried over from was
   empty. Uploads fail until Phase 3 delivers Paperless either way.

4. **Converge once, then open the shared trees to the services that read them.**
   This one is deliberately after the first converge: the `media` and `scans`
   groups do not exist on the host until the `service` role creates them, so a
   `chgrp` run before it has nothing to chgrp to. Expect jellyfin to come up with
   an empty library and paperless-upload to restart-loop (it cannot create
   `processed/` in a tree it may not write) until these run:

   ```bash
   doas chgrp -R media /pool/shared/media
   doas chmod -R o+rX /pool/shared/media
   doas chgrp -R scans /pool/shared/scans
   doas chmod -R g+rwX /pool/shared/scans
   doas find /pool/shared/scans -type d -exec chmod g+s {} +
   ```

   The setgid bit is per directory and is not inherited by anything that already
   exists, so a plain `chmod g+s /pool/shared/scans` would fix the share root and
   leave every subdirectory already under it — `processed/`, and whatever the
   scanner made — without it. Hence the `find`.

   The role creates a bind directory only when it is missing and never touches
   the permissions of one that exists, so both trees stay the operator's — which
   is also why these are not one-shot: anything dropped into them later by
   another writer inherits whatever that writer gives it.

   For the media tree the `o+rX` is what Jellyfin actually reads through: the
   linuxserver image drops its supplementary groups when s6 switches to its own
   user, so group access never reaches the app process —
   `hosts/storagebaby/services/jellyfin/README.md` has the measurement. The
   `chgrp` still matters anyway, because the `media` group is what Samba and the
   rest of the host use, and it is what the role itself would set on a tree it
   creates. The uploader keeps its groups, so for the scans tree the group is the
   whole mechanism — and the `g+s` is what makes the existing tree match the
   `2775` the role would have given a fresh one, so files the scanner and the
   uploader drop there stay group-`scans` instead of falling back to the writer's
   own group.

Two more things are not steps but expectations about that first converge:

- **Existing volume directories keep their owner and mode.** A converge creates
  the ones that are missing and leaves the rest alone, so anything already on the
  pool stays exactly as it is.
- **GPU transcoding is unverified until real hardware runs it.** Jellyfin reaches
  `/dev/dri` through a udev rule `host_base` installs on a `gpu: true` host. The
  test VM has no GPU, so nothing in this repo proves it works — check it after
  cutover.

## Migrating existing service data

**Nothing in this repository moves the old stack's data.** The role creates a
volume directory when it is missing, leaves an existing one alone, and never
repairs ownership afterwards — it is create-only, by design, because images chown
their own data tree and an enforced mode would fight them on every converge. So
carrying the data over is the operator's job, done once, by hand, per service.
**A service whose data is not moved simply starts empty** — new library, new
settings — and moving it later means stopping the service and redoing the two
steps below.

### The two layouts

|                              | Path                                                   |
| ---------------------------- | ------------------------------------------------------ |
| old (rootful Docker Compose) | `/pool/apps/<svc>/volumes/<name>`                      |
| new (this repo)              | `<storage root for the volume's class>/<svc>/<volume>` |

`storage_roots` in `hosts/storagebaby/host.yml` resolves the classes: `pool` →
`/pool/apps`, `fast` → `/var/lib/storagebaby/fast`. Which volume is which class is
in each service's `service.yml`. Concretely:

| Service      | old                                                                | new                                                        |
| ------------ | ------------------------------------------------------------------ | ---------------------------------------------------------- |
| jellyfin     | `/pool/apps/jellyfin/volumes/jellyfin_config`                      | `/pool/apps/jellyfin/config`                               |
| kopia        | `/pool/apps/kopia/volumes/config`                                  | `/pool/apps/kopia/config`                                  |
| kopia        | `/pool/apps/kopia/volumes/cache`                                   | `/var/lib/storagebaby/fast/kopia/cache`                    |
| kopia        | `/pool/apps/kopia/volumes/logs`                                    | `/var/lib/storagebaby/fast/kopia/logs`                     |
| stirling-pdf | `/pool/apps/stirling-pdf/volumes/{configs,logs,pipeline,tessdata}` | `/pool/apps/stirling-pdf/{configs,logs,pipeline,tessdata}` |

`stirling-pdf`'s old folder is not a compose stack — it is the untracked Quadlet
attempt that preceded this repo, with the same four names under `volumes/`.
Kopia's fourth volume, `repo`, is not in the table: it holds a repository only
under `repository: filesystem`, which is the test hosts' backend, so on
storagebaby it stays empty and there is nothing to migrate into it.
`yuzukam` and `paperless-upload` declare no volumes at all: yuzukam is stateless,
and paperless-upload's only state is the `scans` bind, which stays where it is and
is handled by operator step 4 above.

### The recipe, per service

1. Stop the old stack (`cd <old dir> && docker compose down`, or the old Quadlet
   unit for stirling-pdf) and stop the new unit if it already ran:
   `make stop SERVICE=<svc>`.
2. `mv` each old tree onto its new path. `mv` and not `cp`: both sides are on the
   pool, so it is a rename and costs nothing, and there is no second copy to
   forget about afterwards.
3. Fix the ownership for the rootless mapping. This is the step that is easy to
   skip and impossible to skip, and the owner it has to be fixed _from_ differs per
   service: jellyfin's old tree is owned by host **uid 1000**, because rootful
   Docker ran the linuxserver image under `PUID=1000` — and under rootless Podman
   that uid is the operator's login user, not the service. Kopia's old tree is
   **root-owned**, because that image runs as root and Docker ran it rootful.
   Neither is what the new mapping needs.

**Which chown depends on what uid the image runs as _inside_ the container**, because
that is what the user namespace maps. `podman unshare` is what translates a
container-side uid into the host uid it actually lands on, and it has to run as the
service user — with that user's runtime directory, the same way every other rootless
command in this repo is invoked:

- **jellyfin** — the linuxserver image drops to `PUID=1000`, so the tree has to end
  up owned by container uid 1000, which is a subuid of `svc-jellyfin` on the host:

  ```bash
  uid=$(id -u svc-jellyfin)
  cd /tmp # rootless podman cannot chdir back into root's 0700 home
  doas runuser -u svc-jellyfin -- env XDG_RUNTIME_DIR=/run/user/$uid \
  	podman unshare chown -R 1000:1000 /pool/apps/jellyfin/config
  ```

- **stirling-pdf** — its in-container uid is **not** established anywhere here;
  nothing in this repo measured it. Two ways out, both fine: read it once after the
  first start with `podman exec stirling-pdf id -u` and put that number into the
  command above, or chown the migrated trees to `0:0` under `podman unshare` —
  container root, which _is_ `svc-stirling-pdf` on the host — and let the image's own
  start-time chown finish the job.

- **kopia** — runs as root inside, so container uid 0 maps straight onto the service
  user itself and a plain chown says it:

  ```bash
  doas chown -R svc-kopia:svc-kopia /pool/apps/kopia/config
  doas chown -R svc-kopia:svc-kopia /var/lib/storagebaby/fast/kopia/cache
  ```

  (`runuser -u svc-kopia -- env XDG_RUNTIME_DIR=/run/user/$(id -u svc-kopia) podman unshare chown -R 0:0 <path>`
  is the same thing said the other way round.)

- **paperless-upload** — nothing to do. It declares no volumes, and its unit runs
  `UserNS=keep-id:uid=1000,gid=1000`, so container uid 1000 **is**
  `svc-paperless-upload`'s own host uid: no subuid, nothing for `podman unshare` to
  translate. Its only state is the `/pool/shared/scans` bind, and that one is opened
  by group and setgid in operator step 4 — never by a chown.

Then `make start SERVICE=<svc>` and check `make ps SERVICE=<svc>`. If the ownership
is wrong the container comes up and fails — nothing on the host will quietly fix it
on the next converge.

Two service-specific notes:

- **kopia's `cache` belongs to whichever repository `config` points at.** Move both
  or neither; a cache from a different repository fails at startup with
  `cipher: message authentication failed`, which reads like a wrong password and is
  not one. `cache` is rebuildable, so leaving it behind is always safe.
- **A migrated kopia `config` carries the old `repository.config`**, and `start.sh`
  skips the connect whenever that file is present — which is the point, the
  connection is already made. To force a fresh connect from `service.yml` and the
  secrets instead, delete `repository.config` before starting.

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
    make test-integration      # Molecule scenario test-ci in a KVM VM, converging hosts/test-a
    MOLECULE_HOST=test-ci make test-integration   # same scenario, the smaller CI placement
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
