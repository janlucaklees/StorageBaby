---
name: adding-a-service
description: Use when adding, changing or reviewing a service in this repository — a new application folder under hosts/<host>/services/, a Quadlet unit, a service.yml key, a pod, a volume, a secret, a route or a test-host placement.
---

# Adding a service

## Overview

Placement is the folder. `ansible/roles/service/` turns `hosts/<host>/services/<name>/` into a
rootless Podman service: a `svc-<name>` user, volumes, podman secrets synced from sops,
Quadlet units, Traefik routes, a backup sidecar. You write a contract, not a script.

**A service folder is a plugin.** The platform is the receiver: it offers everything a plugin
needs in order to run, and the plugin ships every fact about itself. **Placing one never
requires touching the receiver.** So when something does not fit, the question is not "is this
platform or service?" but **"would adding the next service have to edit this?"** If yes, it is
in the wrong place — and that applies to the test suite as much as to the role. The inverse
holds too: a service may not ship a fact belonging to the _host_ (a storage path, a
certificate zone, another service's port), because that is someone else's decision.
`docs/ownership.md` § 1.1.

**`tests/static/` is the contract.** It is not a safety net bolted on afterwards — it is where
the conventions are actually written down, with the measurement that produced each one in the
comment above it. Read it before inventing anything.

## Read first, in this order

1. `ansible/roles/service/README.md` — what a template may use, and every `service.yml` key.
2. `tests/static/test_contract.py` — `SPEC_KEYS` is the whole allowed key set. A misspelled
   key is otherwise accepted and silently ignored.
3. `tests/static/test_quadlet_conventions.py` — what every unit must declare, and why.
4. The existing service closest in shape to yours:

| Shape                           | Copy from                                |
| ------------------------------- | ---------------------------------------- |
| one container, no state         | `yuzukam`                                |
| one container, state, backed up | `mcp` (its `data` volume is the pattern) |
| app + database + dump + backup  | `mealie`                                 |
| many parts, plain-TCP ports     | `paperless`                              |
| two hostnames, one service      | `nextcloud`                              |
| image built on the host         | `mcp` (and the `build-echo` fixture)     |
| a unit that runs once and exits | `paperless-dump.service.j2`              |

5. `docs/ownership.md` § 7 — **before** proposing any change to the role or to a
   `service.yml` key. That section is the list of places the repository already disagrees
   with its own target state; your idea may be on it.

## Checklist

1. **Pick the space.** `<app>.klees.io` is public; `<app>.home.klees.io` is VPN-only. A full
   hostname goes in `service.yml` as it stands; a bare label is completed by the host's
   `domain`. The dot is what tells them apart. The zone must be in the host's `cert_zones`.
2. **Pick a free port.** `rg '^port:' hosts/*/services/*/service.yml`. Route ports, TCP
   entrypoints and TCP targets are one namespace per host.
3. **Verify the image tag exists** before pinning it — a GitHub release tag is not always a
   published image tag, and the two have been seen to use different numbering entirely.
   `docker manifest inspect` fails anonymously against ghcr, so ask the registry directly.

   ghcr.io:

   ```sh
   curl -s -H "Authorization: Bearer $(curl -s 'https://ghcr.io/token?scope=repository:<owner>/<name>:pull' | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')" \
   	-o /dev/null -w '%{http_code}\n' 'https://ghcr.io/v2/<owner>/<name>/manifests/<tag>'
   ```

   Docker Hub:

   ```sh
   curl -s -o /dev/null -w '%{http_code}\n' 'https://hub.docker.com/v2/repositories/<namespace>/<name>/tags/<tag>'
   ```

   200 means it exists. A library image (`postgres`, `redis`) has namespace `library`.

4. **Write `service.yml`.** Only `SPEC_KEYS`. `volumes` are `{class: pool|fast, owner: <uid>}`.
5. **Write the units** in `quadlet/`. One `<name>.pod.j2` if there is more than one container
   or any `backup:`.
6. **Write `README.md`** in the service folder: the decisions and their measurements, the
   operator steps, the restore command.
7. **Secrets.** Write the file with `REPLACE_ME` values and encrypt it in place:
   ```sh
   printf 'database_password: REPLACE_ME\nkopia_password: REPLACE_ME\n' > hosts/StorageBaby/services/mealie/secrets.sops.yaml
   sops --encrypt --in-place hosts/StorageBaby/services/mealie/secrets.sops.yaml
   ```
   Encryption needs public keys only. **Never decrypt an existing file**, and never put a real
   secret value through a tool call — not `sops -d`, not hashing, not reading one off a host.
   Adding a key to a file that already exists (kopia's `client_<name>`, traefik's htpasswd) is
   the operator's `mise run sops`, not yours.
8. **If the service declares `backup:`, tell kopia about it.** Add `client_<name>` to
   **kopia's own** `hosts/StorageBaby/services/kopia/service.yml` `secrets:` list. Its unit
   renders one `Secret=client_*` per such key and `config/start.sh` registers
   `<name>@<host>` from the files that appear; a service missing from that list gets no
   server-side user, and its sidecar hangs unauthenticated instead of failing loudly. The
   matching value in kopia's **encrypted** file is the operator's `mise run sops` — so
   `test_declared_secrets_exist_in_file` is red until they add it. That red test is the
   to-do list, not a mistake; say so rather than reverting the declaration.
9. **Place it on a test host**, as a symlink, never a copy:
   ```sh
   ln -s ../../StorageBaby/services/mealie hosts/test-a/services/mealie
   ```
   `prepare.yml` generates throwaway test secrets generically from your `secrets:` list and
   pairs `kopia_password` with kopia's `client_<name>` by name. No harness edit needed.
10. **Verify.** `mise run format`, then `mise run test-static`, then
    `mise run test-integration`. Check `pgrep -af molecule` first — one run at a time on this
    machine, the VM and libvirt names are shared.

    A **newly placed** service needs the full `test-integration`, not
    `mise run molecule converge`. The VM cannot decrypt a service folder's own
    `secrets.sops.yaml` — that one is encrypted to the operator and the real host — and works
    from a per-host override under `hosts/test-a/secrets/` that **`prepare` generates and
    `converge` does not**. Converge against a VM prepared before the service was placed fails
    with "sops failed to decrypt". Re-running `prepare` on a live VM is the wrong fix: it
    regenerates every service's passwords under already-connected backup sidecars, which
    `prepare.yml` warns is state the VM cannot repair. Start a fresh one.

    `mise run molecule converge` is for iterating on a service already placed when the VM was
    prepared, and it leaves the VM up — which is what makes
    `mise run molecule-exec '<command>'` worth having.

## Rules a test enforces

| Rule                                                                                                                                         | Test                                                                       |
| -------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Every container declares `ContainerName=`, `HealthCmd=`, `HealthOnFailure=kill`, `Restart=always`                                            | `test_container_declares_health_and_restart`                               |
| Every `PublishPort=` is on the `.pod` and starts `127.0.0.1:`                                                                                | `test_published_ports_are_loopback_only`                                   |
| A pod publishes **exactly** its route ports plus its `tcp_ports` targets                                                                     | `test_pod_publishes_exactly_the_route_ports`                               |
| Every `*.container.j2` of a pod service carries `Pod=<name>.pod`                                                                             | same                                                                       |
| No template writes `AddHost=<fqdn>:host-gateway`, or reaches the host by `host.containers.internal` / `host.docker.internal` / `169.254.1.2` | `test_no_template_writes_a_host_gateway_addhost`                           |
| An `Environment=` value that renders with whitespace is quoted whole                                                                         | `test_environment_values_with_whitespace_are_quoted`                       |
| No `*.image.j2` unit                                                                                                                         | `test_no_service_declares_an_image_unit`                                   |
| One folder per service across hosts — symlink, or byte-identical copy                                                                        | `test_a_service_placed_on_several_hosts_is_one_folder_or_identical_copies` |

## Gotchas

- **Never put a `<placeholder>` inside a ` ```sh ` block.** `prettier-plugin-sh` parses the
  angle brackets as a redirect and rewrites the line — `services/<name>/x` comes back as
  `services/ < name > /x`, which is a silently wrong command in a document people copy from.
  Use a real service name in shell examples; keep placeholders in prose.
- **`config/` is copied, `quadlet/` is templated.** `config/` is `ansible.builtin.copy`, so no
  Jinja runs in it. A value needed both in a unit and in a config file is duplicated by hand —
  say so in a comment.
- **A one-shot cannot be a `*.container.j2`.** The health-and-restart test demands
  `Restart=always` and `HealthCmd=`, which a process that runs once and exits has neither of.
  Write a plain `<name>-<thing>.service.j2` doing `podman run --rm --pod <name> …`.
- **The role renders `*.service.j2` but only enables `*.timer.j2`.** `tasks/timers.yml`
  builds `timer_units` from the timer glob alone, so a one-shot with no timer sibling is
  written to the user's unit directory and never started. Pull it in from the containers
  that need it — `Requires=` plus `After=` in their `[Unit]` section, which systemd honours
  by activating it as a dependency. Leave `RemainAfterExit` **off**: a successfully-exited
  one-shot is inactive, so every restart of a dependent re-runs it, which is what an
  application that migrates its database on upgrade needs. With `RemainAfterExit=yes` the
  migration would run once, ever, and a version bump would start the new code against the
  old schema.
- **`owner` is the uid _inside_ the container**, which the role maps to
  `subuid_start + owner - 1`. Read it off the image rather than guessing
  (`podman exec <name>-app id -u`); a wrong one hands a whole tree to the wrong uid, once.
  Leaving it out means container uid 0, which is the service user.
- **Nothing inside a container may bind a port below 1024 unless it is container-root.** A
  rootless container's network namespace keeps `net.ipv4.ip_unprivileged_port_start` at 1024.
  An image that defaults to port 80 and then drops to its own unprivileged user exits on
  bind, and the only symptom is `podman healthcheck run <name>` printing `unhealthy`. Set the
  application's port above 1024 — the route port is the obvious value, so the number means
  one thing from Traefik to the process. (`mcp-hub` cost a VM cycle on exactly this.)
- **Prefer a `_FILE` variable over `type=env`.** With the file form the value never appears in
  the container's environment at all. `Secret=<name>,type=env,target=<VAR>` is for images that
  offer no file variant.
- **Reach another service by its public route name**, never by loopback: rootless containers
  of different users cannot see each other's loopback. The role renders the host-gateway map
  for every placed route name; your template must not.
- **Inside a pod, parts reach each other on `127.0.0.1:<port>`** — not by container name. An
  image whose config hardcodes a compose service name needs `AddHost=<that name>:127.0.0.1`,
  which is allowed and is what immich does.
- **A pinned tag gets no `AutoUpdate=`; a floating tag gets `AutoUpdate=registry`.**
- **A database is backed up as a dump, never as its data directory.** Dump timer at 02:30 into
  a `backups` volume of class `pool`; `backup.paths` lists the data volumes and `backups`, and
  never the database volume. Plain uncompressed SQL, restored with `psql`, not `pg_restore`.
- **`backup:` requires a pod**, so a single-container service that needs backups still gets a
  `<name>.pod.j2`.
- **An unmounted path is not a directory to create.** Never add a path under `/pool` or
  `/mnt` as a bind without reading `ansible/roles/storage/README.md` first.

## Red flags

- About to add a key to `service.yml` that `SPEC_KEYS` does not contain → it is a role change,
  and `docs/ownership.md` § 7 comes first.
- About to run `sops -d`, `sops set`, or anything that reads a real secret value → stop, it is
  an operator step.
- About to copy a service folder onto a second host → it is a symlink.
- About to write `AddHost=…:host-gateway` → the role already did.
- About to guess a `HealthCmd=` → read the image's own `HEALTHCHECK` in its Dockerfile, and
  use a tool the image actually ships.
- Static suite green and about to call it done → it proves the contract, not that the service
  starts. The Molecule converge is the evidence.
