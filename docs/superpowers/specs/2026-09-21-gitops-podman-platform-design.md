# StorageBaby: Git-driven Podman platform

Status: reviewed, approved
Date: 2026-09-21
Supersedes: `2026-08-27-podman-migration-design.md` (its networking and isolation reasoning still applies; its symlinked-units, `reconciler/` and Cockpit parts are replaced here)

## 1. Goal

Everything needed to run a hosted service lives in this repository: host setup, service definition, secrets (encrypted), backup policy, and placement. A host is set up once with `bootstrap.sh`; after that it pulls the `stable` branch on a timer and converges itself. The only per-host state outside git is two keys that never leave the host.

Non-goals: clustering, a web UI, Docker of any kind on a target host. Building images on a host is allowed where a service needs it (Quadlet `.build` units), but not required.

## 2. Repository layout

```
hosts/
  storagebaby/
    host.yml                    everything host-specific (section 4)
    services/<name>/            services placed on this host (section 5)
  shared/
    services/<name>/            services that run on every host (traefik)
ansible/
  ansible.cfg
  playbook.yml                  site playbook: loads hosts/<h>/host.yml, host roles, then the service role per placed service
  inventory/hosts.yml           host names only
  roles/
    host_base/                  packages, sysctl, deploy timer, age key check
    mounts/                     data/parity disk mount units, mergerfs pool (only if host.yml declares disks)
    snapraid/                   config, storage-maintenance scripts, plugins, timer (only if declared)
    samba/, cups/, syncthing/   same pattern
    service/                    generic: user, subids, linger, dirs, secrets, render + install quadlets,
                                traefik snippet, timers, handlers
scripts/                        loose helper scripts (compress-subfolders.sh)
tests/                          section 8
bootstrap.sh                    one-time per host
.github/workflows/ci.yml
.sops.yaml
Makefile                        test, fmt, and service wrappers
docs/
```

Placement is the folder: a service under `hosts/<h>/services/` runs on that host only, a service under `hosts/shared/services/` runs on every host. Duplicate placement is structurally impossible. Moving a service between hosts is a `git mv` plus `sops updatekeys` on its secrets file.

Removed: `odysseus/`, `watchtower/`, `ofelia/`, `reconciler/`, `install.sh`, every `docker-compose.yml`, every per-service `setup.sh` and `Makefile`, `PORTS.md` (ports are declared in `service.yml`, uniqueness is a test).

Placed on storagebaby: kopia, immich, nextcloud, paperless, paperless-upload, openarchiver, jellyfin, yuzukam, stirling-pdf. Shared: traefik. Host components on storagebaby: mounts, snapraid with storage-maintenance, samba, cups, syncthing.

## 3. Bootstrap and deploy

`bootstrap.sh`, run once as root on a fresh Arch host:

1. Installs git, ansible, sops, age, podman via pacman.
2. Generates `/etc/storagebaby/age.key` (0600 root) and `/etc/storagebaby/deploy_key` (ed25519, 0600 root).
3. Prints both public keys.
4. Installs `storagebaby-deploy.service` and `.timer` (system units, root).

The operator then adds the deploy key as a read-only GitHub deploy key, adds the age recipient to `.sops.yaml` under that host's rule, runs `sops updatekeys` over the affected secret files, and pushes. Nothing else is ever done on the host by hand.

`storagebaby-deploy.service` runs `ansible-pull` as root: clone or fetch `/var/lib/storagebaby/repo` (0700 root), check out `stable`, run `ansible/playbook.yml` with `--limit <hostname>`. Timer: 2 min after boot, then every 5 min. `ansible-pull` only runs the playbook when the checkout changed.

Ansible runs as root because it manages users, packages, mounts and per-user unit directories. No container runs as root and no container gets a socket to anything.

## 4. Per-host configuration

`ansible/inventory/hosts.yml` lists host names only. The playbook loads `hosts/<inventory_hostname>/host.yml` and discovers placed services from `hosts/<inventory_hostname>/services/*/service.yml` plus `hosts/shared/services/*/service.yml`.

`hosts/storagebaby/host.yml`:

```yaml
domain: home.klees.io
acme: true
storage_roots:
  pool: /pool/apps
  fast: /var/lib/storagebaby/fast
volume_overrides: {} # e.g. immich/upload: /pool/shared/photos
disks: # presence enables the mounts role
  data: [{ uuid: ..., mount: /mnt/data/data1 }, ...]
  parity: [{ uuid: ..., mount: /mnt/parity/parity1 }]
  pool: { mount: /pool, policy: eplfs }
snapraid: # presence enables the snapraid role
  parity_file: /mnt/parity/parity1/snapraid.parity
  scrub: { percent: 8, older_than_days: 12 }
  balance_threshold: 5
samba: # presence enables the samba role
  shares: [{ name: media, path: /pool/shared/media }]
```

Rules: a role for a host component runs only when its block is present. No role, template or task mentions a hostname; the only place a hostname appears is the `hosts/` folder name and the inventory.

## 5. Service contract

`hosts/<h>/services/<name>/`:

```
service.yml                   contract below
quadlet/                      *.container.j2, *.pod.j2, *.build.j2, *.timer.j2, *.service.j2
config/                       files copied to the host, referenced from templates
secrets.sops.yaml             encrypted key/value pairs
README.md
```

`service.yml`:

```yaml
name: immich
port: 8280 # loopback port the pod publishes on 127.0.0.1
domain: immich # subdomain under the host's domain; omit for services without a route
volumes:
  upload: { class: pool }
  database: { class: fast }
  model-cache: { class: fast }
secrets: [database_password, kopia_password]
backup: # or `backup: none` for stateless services; one of the two is required
  paths: [upload]
  schedule: '03:00'
  retention: { latest: 3, daily: 7, weekly: 4, monthly: 12, annual: 3 }
```

Phase 2 adds the optional keys `binds`, `devices`, `groups`, `config` and the host keys `gpu`, `packages`, `service_config`; see `2026-09-22-phase-2-single-services-design.md`.

Phase 3 adds `routes` (several hostnames for one service), `host_secrets` (a value shared between two services of one host), `hooks.after_change`, plain systemd timer units in `quadlet/`, and makes `backup` a block the role consumes rather than a declaration nothing reads; it also turns the four multi-container stacks into pods. See `2026-09-23-phase-3-pods-and-backups-design.md`, which supersedes this section wherever the two disagree.

The service role, for each placed service:

- creates `svc-<name>` (system user, subuid/subgid via `usermod --add-subids`, linger)
- resolves each volume to `<storage_roots[class]>/<name>/<volume>` unless overridden, and creates it owned by the service user only when it is absent; existing directories are never re-permissioned, because images such as Stirling, Postgres and Immich manage the ownership and mode of their data trees themselves
- syncs secrets into `podman secret` for that user (section 6)
- copies `config/` to `/etc/storagebaby/<name>/`, owned by the service user, read-only
- renders `quadlet/*.j2` into `/etc/containers/systemd/users/<uid>/`
- renders the Traefik file-provider snippet for the service's domain and port
- `daemon-reload` and restarts only the units whose rendered file, config or secret changed (handlers)

Template variables available: `volumes.<name>` (resolved path), `config_dir`, `port`, `fqdn`, `tz`.

Container conventions:

- Multi-container services are one Podman pod: every piece (app, database, cache, backup client) stays its own container with its own image and its own generated systemd unit. The `.pod` unit only groups them into one shared network namespace, so they reach each other on `localhost:<port>`, and publishes the single app port on `127.0.0.1:<port>`. Nothing is merged into one image.
- Containers that run as root inside map to the service user on the host. Images that drop to a fixed uid get `UserNS=keep-id:uid=N,gid=N` so bind-mounted files stay owned by the service user.
- Auto-updated containers: floating tag plus `AutoUpdate=registry`; the service role enables `podman-auto-update.timer` for the user. Manually updated containers: pinned tag, no `AutoUpdate`. Bumping the tag in git is the update.
- Services with their own Containerfile (paperless-upload) use a `.build` unit; the container unit references the built image. A change to the Containerfile or its context triggers a rebuild and restart through handlers.
- Every container has a `HealthCmd`. `HealthOnFailure=kill` with `Restart=always`.
- Timers replace Ofelia: e.g. `nextcloud-cron.timer` running `podman exec nextcloud php cron.php` as the service user.
- Kopia clients are a container in the service's pod, connecting to `https://kopia.<domain>` through Traefik, applying the policy from `service.yml` on start.

Operator wrappers in the root Makefile: `make start|stop|restart|ps|logs SERVICE=x` shelling to `systemctl --user -M svc-x@ ...`.

## 6. Secrets

- Encrypted at rest with sops + age: `<service dir>/secrets.sops.yaml`, flat key/value.
- `.sops.yaml` rules by path: `hosts/<h>/**` encrypts to the operator's editing key plus that host's key only; `hosts/shared/**` encrypts to the operator's key plus every host key. A host can decrypt only what it runs.
- On the host, the service role decrypts with `SOPS_AGE_KEY_FILE=/etc/storagebaby/age.key` and pipes each value into `podman secret create --replace` as the service user, but only when `podman secret inspect --showsecret` shows a different value. A changed secret triggers the unit restart handler.
- Runtime delivery is Podman's `file` driver: plaintext in the service user's storage dir, mode 0600, same exposure as compose file secrets today.
- CI never holds a key. It verifies each file's recipient list equals what `.sops.yaml` prescribes for its path.

## 7. Traefik and networking

Rootless, `Network=host`, unprivileged-port sysctl set to 80. Providers: file only. Dynamic config directory `/etc/storagebaby/traefik/dynamic.d/`, one rendered file per placed service pointing at `http://127.0.0.1:<port>`, plus Traefik's own dashboard and wildcard-cert router. `acme: false` in `host.yml` turns ACME off and Traefik serves its default certificate (used by tests).

Cross-service traffic goes through Traefik and the public FQDN. Rootless containers of different users cannot reach each other's loopback; this is the only supported path. On a multi-host setup a client on host B reaches kopia on host A the same way, via DNS.

## 8. Tests

Run with `make test` from the devtools image; the integration scenario additionally mounts the host's libvirt socket and image directory. Nothing is installed on the operator host.

Static (`tests/static/`, pytest):

- ansible-lint, `ansible-playbook --syntax-check`, prettier check.
- Loopback ports unique per host (across that host's services plus shared).
- Every `Secret=` in a template is in that service's `secrets` list; every listed secret is a key in `secrets.sops.yaml` (key names are plaintext in sops files).
- Every sops file's age recipients equal what `.sops.yaml` prescribes for its path.
- Every service has `backup` or `backup: none`.
- Render every placed service for every host into a temp dir and run `quadlet -dryrun` on it.

Integration (`tests/integration/`, Molecule with libvirt/QEMU VMs):

- Test hosts are KVM virtual machines on the operator's libvirt (`qemu-base libvirt dnsmasq`, `default` NAT network), created from the official Arch cloud image with cloud-init and reached over SSH as root. Molecule's generic driver with custom create/destroy playbooks runs inside the devtools container, which talks to the host's libvirt socket and is never privileged. Privileged systemd containers are forbidden as test hosts: their udev coldplug tears down the operator's desktop session.
- Test hosts are ordinary host folders `hosts/test-a/` etc., so the deploy path is production-identical; they place services by symlinking into `hosts/storagebaby/services/`. `hosts/shared/` applies as on any host. `test-ci` scenario places a light subset; `test-full` places everything.
- A host may override a service's secrets with `hosts/<host>/secrets/<service>.sops.yaml`; tests use this for every placed service, encrypted to the test host's bootstrap key.
- prepare: `bootstrap.sh --local` (no remote, no timer install); a throwaway age key; generated secret values for every declared secret, encrypted to that key into a test overlay.
- converge: the site playbook, `acme: false`, storage roots inside the container.
- verify (testinfra): users and linger, every unit active, every container healthy, every domain answers through Traefik on 443, Kopia clients connected to the server, storage-maintenance hooks stop and start services, `podman ps` shows no root containers.
- idempotence: second converge reports zero changes.
- deploy: push a commit to a local bare repo that changes one service, run the deploy service, assert exactly that unit restarted.

Pipeline (`.github/workflows/ci.yml`): static plus `test-ci` on every push and PR. On master success, fast-forward `stable`. Branch protection keeps humans off `stable`. Hosts only track `stable`.

## 9. Storage maintenance

The snapraid role installs the existing storage-maintenance orchestrator, plugins and timer as root system units. Plugin hooks stop and start services with `systemctl --user -M svc-<name>@ stop|start <unit>`. The jellyfin and samba plugins are updated accordingly; remote snapshot plugins are unchanged.

## 10. Migration order

1. Layout, Makefile, test harness, `bootstrap.sh`, host_base, service role, traefik. Proves the loop end to end, including the nested-Podman risk.
2. kopia, jellyfin, stirling-pdf, yuzukam, paperless-upload (`.build`).
3. immich, paperless, nextcloud, openarchiver.
4. mounts, snapraid, samba, cups, syncthing.
5. CI workflow, docs, removal of compose files and old scripts.

## 11. Risks

- Integration tests need libvirt with KVM on the machine running them: an operator-installed prerequisite on the workstation, an apt install step on the CI runner. No fallback to containers.
- `podman auto-update` rollback needs the previous image still present; `podman image prune` must exclude it. Verified in step 2.
- Nextcloud and paperless pods are the largest translations; their healthchecks and startup ordering (`After=`/`Wants=` on generated unit names) are covered by the full scenario, not CI.
