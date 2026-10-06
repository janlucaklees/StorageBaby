# Architecture: how the platform is built

Written 2026-10-02 against `podman-platform` at 75e2c36 and re-measured 2026-10-06 against
`master` at 60b402f, where it lives. Read this before the README: the README is the
operator's runbook and the role READMEs are the contract reference; this file is the shape
of the thing, what counts as platform and what does not, where the complexity sits and
which decision put it there. The complexity verdict and the survey of alternatives it was
written for (2026-10-02) were delivered as a report and kept out of the repository; § 5
carries the part of that report that describes this code.

## 1. The loop

```
workstation                 GitHub                          host (Arch, root)
-----------                 ------                          -----------------
git push master  ──────►  CI: static tests (pytest)
                           ├─ Molecule: converge hosts/test-ci
                           │  in a KVM VM, verify, idempotence
                           └─ green on master ──► fast-forward `stable`
                                                              ▲
                                            storagebaby-deploy.timer (boot+2 min, then every 5 min)
                                            └─ ansible-pull --checkout stable --only-if-changed
                                               └─ ansible/playbook.yml --limit <hostname>
                                                  ├─ role storage    (only if host.yml has `storage`)
                                                  ├─ role host_base
                                                  └─ role service × every placed service.yml
                                                     └─ systemctl --user -M svc-<name>@ …
                                                        └─ Quadlet ──► rootless podman
```

Ansible runs as root because it makes users, mounts and per-user unit directories. No
container runs as root on the host, nothing mounts a daemon socket, and the only state a
host holds outside git is two keys under `/etc/storagebaby/` (`bootstrap.sh` makes them).
`stable` is moved by CI alone; hosts never track `master`.

## 2. Layers, and who owns what

| Layer                              | Lives in                                                | Owns                                                                                                    | Code lines |
| ---------------------------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ---------: |
| Host declaration                   | `hosts/<host>/host.yml`                                 | domain, ACME, storage roots, the `storage` block, `gpu`, `packages`, per-host `service_config`          |        115 |
| Placement                          | the folder under `hosts/<host>/services/`               | which services a host runs; test hosts place by symlink                                                 |          — |
| Service contract                   | `hosts/**/services/<name>/`                             | `service.yml` (15 keys), `quadlet/*.j2`, `config/`, `secrets.sops.yaml`, README                         |      2,544 |
| `service` role (the platform)      | `ansible/roles/service/`                                | user, images, dirs and their owners, secrets, units, routes, timers, hooks, backup sidecar, restarts    |      1,606 |
| `host_base` role                   | `ansible/roles/host_base/`                              | packages, sysctl, platform dirs, deploy timer, test-host default cert, GPU udev rule, `storagebaby-svc` |        279 |
| `storage` role (NAS, not platform) | `ansible/roles/storage/`                                | disk/parity mounts, mergerfs pool, snapraid, nightly maintenance, mail                                  |      1,470 |
| Site playbook                      | `ansible/playbook.yml`                                  | discovery of placed specs, host-wide lists (route fqdns, TCP ports), per-service `config` merge         |        193 |
| Entry and glue                     | `bootstrap.sh`, `mise.toml`, `.github/workflows/ci.yml` | one-time host setup, workstation tasks, promotion of `stable`                                           |        395 |
| Static tests                       | `tests/static/`                                         | 46 tests: contract shape, ports, secrets recipients, render + `quadlet -dryrun`, storage, lint          |      1,504 |
| Integration tests                  | `tests/integration/`                                    | Molecule on libvirt/KVM, 67 testinfra tests, custom create/prepare/destroy playbooks                    |      3,245 |
| Test fixtures                      | `hosts/test-a/`, `hosts/test-ci/`                       | two test hosts, `tcp-echo` and `build-echo` fixture services (duplicated per host by design)            |        453 |

Code lines are YAML, Jinja, Python, shell and TOML, measured with `wc -l`. Prose is on top
of that: README 1,004, CLAUDE.md 194, role READMEs 934, service READMEs 2,300, design
specs 764, implementation plans 7,105 (the plans are history, not reference).

The whole platform proper, the thing a service folder is written against, is the
`service` role plus the playbook plus `host_base`: **2,086 lines of code, 28 % of the
role's task lines being comments** that explain an edge case met during rollout. The
tests are 2.3× the platform code. 184 commits landed between 2026-09-21 and 2026-10-05.

## 3. One service, end to end

What the `service` role does with a folder, in order (`tasks/main.yml` → `host.yml` →
`images.yml` → `secrets.yml` → `units.yml` → `timers.yml` → `hooks.yml`):

1. Derive facts: `svc-<name>`, volume paths from `storage_roots[class]` or
   `volume_overrides`, routes (from `routes:` or the `domain:`+`port:` shorthand), which
   units carry the host-gateway drop-in, the two implicit backup volumes.
2. Create the system user with subuid/subgid and linger; add it to bind groups; if a
   membership changed, stop and start `user@<uid>.service`; wait for the user manager.
3. Install `podman-as` and `podman-secret-sync` to `/usr/local/sbin`.
4. **Pre-pull every image** the templates name (rendered once via `lookup('template')`
   only to read `Image=`), three attempts, ten minutes each, stop at the first failure.
   Before anything is written, so a failed converge leaves the old service running.
5. Create missing volume and bind directories. Each may declare an `owner`, the uid the
   process has inside the container, which the role maps onto the service's subuid range;
   a directory whose owner differs is adopted with one `chown -R`, once. Modes are never
   re-permissioned.
6. Copy `config/` to `/etc/storagebaby/<name>/`.
7. Copy `secrets.sops.yaml` to the host, decrypt there with the host age key, sync each
   value into a `podman secret` of the service user; report changed only on a real change.
8. Render `quadlet/*.j2` (except `*.timer.j2`/`*.service.j2`) into
   `/etc/containers/systemd/users/<uid>/`, the generated `<name>-backup.container` if
   `backup:` is a block, the `10-storagebaby-hosts.conf` drop-in (every placed route fqdn
   → `host-gateway`), one Traefik file-provider route per `routes[]` entry, one TCP route
   file if `tcp_ports`. Remove stale drop-ins, the pre-Phase-3 route file, a stale TCP file.
9. Map rendered files to unit names, order build → pod → container, restart only what
   changed (config or secret change restarts all; a changed pod restart covers its
   containers), start what is not active (a `.build` counts as up once it ran
   successfully), enable `podman-auto-update.timer`.
10. Render timer/service pairs into `~svc-<name>/.config/systemd/user/`, enable, restart on change.
11. Run `hooks.after_change` through `podman exec`, each waiting up to ten minutes for the
    container's health check; a container that never gets healthy skips that service's
    hooks, is recorded, and the playbook fails on the list as its last task.

The playbook, not the role, does three host-wide things because the role sees one spec at
a time: it collects every placed route fqdn (for the drop-ins), every placed TCP port (for
Traefik's entrypoints and the unprivileged-port sysctl), and deep-merges
`host.yml`'s `service_config[<name>]` over `service.config`.

## 4. Networking, in one paragraph

Traefik runs rootless as `svc-traefik` with `Network=host` and is the only process on a
routable address. Every other service publishes on `127.0.0.1:<port>` and Traefik's file
provider points at it; there are no labels and no socket. Rootless containers of
different users cannot reach each other's loopback, so service-to-service calls go out
through Traefik on the public name, and because pasta copies the host's own address onto
the container, that name must resolve to podman's `host-gateway` instead: hence the
drop-in. Plain TCP (the scanner's FTP into paperless) is the same door: one Traefik
entrypoint per declared port on the host's concrete address, forwarded to loopback.

## 5. Where the complexity is, and which decision put it there

**Essential, forced by "one Linux user per service, rootless, no sockets"** (a locked
decision, and the one that buys the isolation):

- `podman-as` and the user-manager stop/start/wait dance (`host.yml`): rootless podman
  must be a client of the user's running manager or exec and healthchecks fail.
- Loopback publish + Traefik file provider + host-gateway drop-ins (§4).
- The `owner` mapping onto the subuid range and the one-time adoption of a migrated
  tree (what the hand `chown` recipe used to be), `GroupAdd=keep-groups`, the udev rule
  that opens `renderD*` because an image that calls `setgroups()` drops host groups.
- Pre-pulling images: a first start inside `systemctl start` dies on the 90 s default
  timeout, and the alternative (raising the timeout) was rejected.

**Essential, forced by "root converges every five minutes, unattended"**:

- Every probe carries `check_mode: false`, every write reports `changed` exactly once, and
  directories are create-only, otherwise the nightly run restarts or re-permissions
  something forever.
- Restart-only-changed, the pod-covers-its-containers rule, hooks that defer failure to
  the end of the play so one broken service does not stop the rest from converging.
- The "stale files stay, except configuration that changes behaviour" rule and its two
  named exceptions (drop-ins, Traefik route files).

**Disproportionate to what it serves** (measured; candidates to prune or replace):

| Mechanism                                                                                              | Serves                                                     | Size                                                                                                                                                                                                                                                                                                                                     |
| ------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backup sidecar per pod, reaching the Kopia server as gRPC through Traefik                              | 4 pods                                                     | ~570 code lines: `kopia-client.sh` 110, sidecar template 60, server `start.sh` 177, `test_kopia.py` 92, the test-host default-certificate block in `host_base` 88 (exists only so a sidecar's pinned fingerprint survives a Traefik restart), the re-encrypt route option; plus a 359-line README and the rule "`backup` requires a pod" |
| `tcp_ports` + `tcp_bind_address` + sysctl lowering + Traefik entrypoint re-render                      | one scanner's FTP into paperless                           | ~400 lines: playbook block 43, TCP route template 30, `test_ports.py` 48, `test_ftp.py` 176, `tcp-echo` fixture 52 ×2, three Traefik integration tests, network-fact gathering                                                                                                                                                           |
| `.build` units (host-built images)                                                                     | nothing in production since Phase 4                        | ~200 lines: `build-echo` fixture 65 ×2, the settled-build detection in `units.yml`, the skip list in `images.yml`                                                                                                                                                                                                                        |
| Two spellings for one route (`domain`+`port` vs `routes[]`), a `route:` block plus per-entry overrides | 1 of 9 services uses `routes`                              | contract keys, test branches, README sections                                                                                                                                                                                                                                                                                            |
| `host_secrets`                                                                                         | nothing: declared by no service since 367b3db (2026-10-02) | 76 of `secrets.yml`'s 146 lines, the `prepare.yml` generator, assertions in both test suites, README sections                                                                                                                                                                                                                            |

Contract keys by how many of the nine real services use them: `volumes`, `secrets`,
`backup` 9; `port`, `domain` 8; `config` 6; `hooks`, `route` 2; `routes`, `binds`,
`devices`, `groups`, `tcp_ports` 1; `host_secrets` 0. `owner`, a sub-key of `volumes` and
`binds`, is declared by 5.

## 6. What a service folder really is

Mostly standard Podman. `quadlet/*.j2` are near-raw Quadlet units; the Jinja in them reads
a dozen variables (`volumes.<name>`, `routes`, `fqdn`, `tz`, `config_dir`, `service.config.*`,
the `binds`/`devices`/`keep_groups` idioms). `service.yml` is 20–50 lines of declaration
the role turns into a user, directories, secrets, routes and a backup policy. Nothing in a
folder is an abstraction over containers: a template is the unit file that lands on the
host. That is what keeps the "plug in one folder" requirement met and what makes the
folders portable to any Quadlet host, with or without this role.

## 7. Known drift, as of 2026-10-06 (master 60b402f)

- **The `kopia-clients` set is gone, the contract knows, the prose half does not.**
  367b3db (2026-10-02) retired the per-host set and gave each pod its own
  `kopia_password`; 894ea7a and 60b402f brought `test_contract.py` and CLAUDE.md along.
  `ansible/roles/service/README.md` § Host secrets and the kopia, immich, nextcloud,
  openarchiver and paperless READMEs still present `kopia-clients.<service>` as the live
  example. The `host_secrets` feature itself is supported, tested and unused.
- **Test code in a production spec.** `hosts/StorageBaby/services/paperless/service.yml`
  declares `hooks.after_change: [{container: paperless-app, command: 'touch /tmp/.hook-ran'}]`
  since f787e1e (2026-09-23). Its only consumer is
  `test_deploy_runs_after_change_hooks`, which searches placed services for a `touch` hook.
  It runs on the real host on every converge that restarts paperless.
- **CLAUDE.md § Deployment says bootstrap installs `make`** for host-side Makefile
  targets. `bootstrap.sh` installs no `make`, the Makefile is gone (82c92a3), and
  `storagebaby-svc` replaced the targets.
- The 2026-09-21 spec's §2, §4 and §9 are superseded and say so; the storage half is the
  2026-09-25 spec. The 2026-08-27 spec (symlinked units, a `reconciler/`, Cockpit, Docker
  socket kept for the old fleet) is the design that was replaced, not the one built.

## 8. Requirement gaps the design does not yet cover

- **No local-development story.** `hosts/test-a` is a KVM test host, not a laptop. The
  pieces are there: Quadlet runs under any user's `systemctl --user`, and `render_only`
  already renders every unit, route and timer into a directory without a host. What is
  missing is a "dev" host profile that renders for the operator's own uid and installs
  into `~/.config/containers/systemd/` with no root, no system users and throwaway
  secrets. A feature, not a redesign.
- **One host.** `hosts/` is multi-host by layout and `hosts/shared/` runs on every host,
  but only one real host has ever converged; cross-host service calls (a kopia client on
  host B) are designed through DNS and untested.

## 9. Read next

- `ansible/roles/service/README.md`: the full contract and the reasoning behind every
  task, including the pull-before-write ordering and the restart rules.
- `ansible/roles/storage/README.md`: the NAS half.
- `README.md` § Operator steps and § Migrating existing service data: everything that is
  by hand, once.
- `docs/quickstart.md`: workstation to first converge.
- `docs/superpowers/specs/`: the decision record, newest wins where they disagree.
