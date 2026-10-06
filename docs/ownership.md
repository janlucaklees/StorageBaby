# Ownership: who decides what

Written 2026-10-06. **This describes the target state, not the current one.** Where the
code disagrees with this document the code is wrong, and § 7 is the list of places it
does. `docs/architecture.md` describes the platform as it exists today; this one says
what it is for.

## 1. The rule

The question is always **who decides**, never who touches. The platform writes a
service's Traefik router, syncs its secrets, creates its directories and restarts its
units — and decides none of it. Something belongs to the platform only where the
platform makes a choice the service would otherwise have made.

> The platform is the vessel. It owns deployment and lifecycle. Every decision about how
> a service runs belongs to the service.

The platform may **refuse** a service's request — a declaration it considers unsafe, a
name already claimed, a cross-service connection that looks wrong — and it fails loudly
when it does. What it never does is silently substitute a decision of its own.

## 2. The three owners

**Service** — one folder under `hosts/**/services/<name>/`. Owns every decision about how
it runs: its containers and images, its domains and how a certificate for them is
obtained, its mounts, its secrets, what of it is backed up, when its images update, and
the configuration of every piece of software inside it. A service folder is the one place
to read to know how that service works.

**Platform** — `ansible/`, `bootstrap.sh`, CI, and the contract the static tests enforce.
Owns the loop (when, what and where to deploy), the lifecycle of what it deployed
(create, start, stop, restart, remove, restore), the interface a service declares
against, and the _execution_ of every decision a service makes.

**Administrator** — the person, and the machine as they prepared it. Owns the hardware,
the operating system and the currency of its packages, the disks and their mounts, the
network and its addresses, and which host a service is placed on. This is not a
decision bucket so much as a fact bucket: things the platform measures or the
administrator states once per host.

`host.yml` is where the administrator states those facts. It is **not** a second place to
configure a service.

## 3. Platform

| Decision                                           | Note                                                                                                                                                                                                    |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| When to deploy                                     | Boot + 2 min, then every 5 minutes, only when the checkout changed                                                                                                                                      |
| What to deploy                                     | The `stable` branch, moved by CI alone                                                                                                                                                                  |
| Where a service runs                               | The folder, or a symlink to it, under `hosts/<host>/services/`. Strictly the administrator's act — placing a folder is a human decision — but it is expressed as placement and the platform executes it |
| Discovering what is placed                         | Including through symlinks, so one folder serves several hosts                                                                                                                                          |
| Adding a service                                   | A new folder converges on the next run                                                                                                                                                                  |
| Removing a service                                 | An unplaced service is removed from the host entirely: units, user, subordinate ids, podman secrets, its routes and — on an explicit instruction, never implicitly — its data                           |
| Restoring a service to an earlier point            | Stop, materialise the data, start. See § 6.2                                                                                                                                                            |
| Service identity on the host                       | `svc-<name>`, subuid/subgid, linger, the user's unit directory                                                                                                                                          |
| Unit lifecycle                                     | Ordering (build → pod → container), which change restarts which unit, starting what is down, enabling what is declared                                                                                  |
| Fetching images before anything is written         | Three attempts, ten minutes each; a failure leaves the running service alone                                                                                                                            |
| Executing a service's declarations                 | Directories, secrets, units, routes, timers, hooks — all of it, none of it decided here                                                                                                                 |
| Granting or refusing a declaration                 | A service asks; the platform may say no, loudly. It never answers differently without saying so                                                                                                         |
| Overriding a service                               | The platform has the last word and may override any service decision, per host. Meant for testing, not for configuration                                                                                |
| The interface                                      | Which keys a `service.yml` may carry, the unit conventions, and the static tests that hold them                                                                                                         |
| Defaults for what a service may but need not state | Health timeouts and the like. A service may always override one                                                                                                                                         |
| Distributing host facts to services                | Time zone, addresses, storage roots — the platform hands them down; no service overrides one                                                                                                            |

## 4. Service

| Decision                                                                                  | Note                                                                                                                                                                 |
| ----------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Which containers and pods exist, their images, environment, health checks, restart policy |                                                                                                                                                                      |
| Its domains, in full                                                                      | The complete hostname, not a label the host completes                                                                                                                |
| **How** a certificate for each of them is obtained                                        | Challenge method, DNS provider and its API credentials, or none at all. The platform _performs_ the issuance; the service states how it is to be done                |
| Whether it is served over TLS at all                                                      | A service that wants HTTPS and says nothing about how fails the deploy. There is no host-level fallback certificate                                                  |
| Its mounts: names, storage class, owner uid                                               | The class is a statement of intent — fast or roomy — and the platform resolves it to a path. A service never needs to know the path                                  |
| Its plain-TCP ports                                                                       |                                                                                                                                                                      |
| Its secrets                                                                               | Encrypted in its own folder; the platform decrypts at converge time and makes them available at runtime                                                              |
| Where its configuration files must land                                                   | Dictated by the software inside it, so the service states it and the platform puts them there. The platform keeps no lookup table of its own                         |
| The configuration of every piece of software it runs                                      | Database names and users, cache settings, OCR languages, image versions                                                                                              |
| What is backed up, how often, and how long it is kept                                     | See § 6.2 for who carries it out                                                                                                                                     |
| When its images update                                                                    | Its own auto-update cadence                                                                                                                                          |
| Its after-change hooks                                                                    | And, where the default is wrong, how long it may take to become healthy                                                                                              |
| Which other services it may reach                                                         | Default deny: a service sees its own containers and nothing else. A cross-service connection is an exception the service declares and the platform grants or refuses |
| What it needs from the host                                                               | Host packages, device access, kernel features. The service declares the need; the platform provides it or refuses                                                    |

## 5. Administrator

| Decision                                              | Note                                                                                                                    |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| The hardware                                          | A GPU that is not in the machine is not something a service can declare its way around                                  |
| The operating system and keeping its packages current | No role upgrades or reboots a host                                                                                      |
| Package repositories and keys                         |                                                                                                                         |
| Disks, parity, the pool, and the mounts beneath them  | `storage` in `host.yml`                                                                                                 |
| What the storage classes resolve to                   | `storage_roots`                                                                                                         |
| Network addresses                                     | `tcp_bind_address`, and the DNS records a domain needs                                                                  |
| Time zone                                             |                                                                                                                         |
| Which host runs which service                         | By placing the folder                                                                                                   |
| Log retention, and anything that wants alerting       | The platform writes to the journal and fails loudly. Watching it is a service's job, or a person's — not the platform's |

## 6. Open

### 6.1 Absolute host paths in a service folder

A `binds` entry names a path on the host (`/pool/shared/media`). That is machine
knowledge inside a service folder, and it is the one place the three-owner split leaks.
The service must stay in charge of _what it mounts_; the open question is whether the
_path_ has to cross the boundary.

The shape to beat is the one already used for volume classes: the administrator
publishes **named** shares in `host.yml`, the service claims one by name. No path in a
service folder, the mapping stays with the administrator, and a service placed on another
host works as soon as that host publishes the same name. Rejected alternative: leaving
the path in the service folder and asking the administrator to make it exist — the
coupling survives, it is just undocumented.

### 6.2 Backups and restores

Settled: the platform owns the backup and restore **contract and lifecycle**; it does not
own a backup **program**. Kopia is a service like any other.

Open: the shape of the split. The sketch to work from —

- a service declares what to back up, how often, how long to keep it, and which provider;
- a provider is a service that publishes a snapshot agent and two verbs: list the restore
  points of a service, and materialise one into a directory;
- the platform renders the provider's agent into the consumer, and owns restore, because
  stopping a service and replacing its data underneath it is lifecycle and nothing a
  service can do to itself.

The two version axes stay separate and both are named explicitly in a restore: **git
owns what should run**, the backup repository owns **what the data was**. Nothing writes
a snapshot id back into the repository — the deploy loop runs one way, and that is worth
more than a single coordinate.

### 6.3 Shared configuration

Several services will want the same ACME credentials, and duplicating them per service is
silly. The sketch: a provider is a folder shaped like a service that runs nothing and
publishes a named block of configuration and secrets; a consumer names it; the platform
resolves the name at converge time and fails loudly when it is not placed on the host.

Hard limits, or this becomes a package manager: no versions, no transitive dependencies,
no ordering beyond providers-before-consumers, and a provider may supply **data only**.
The moment a provider needs to run something it is a service, and the consumer reaches it
over the network like any other.

### 6.4 Services that are not containers

Letting a service declare host packages implies a service that is only host packages,
units and configuration — snapraid, were it a service rather than a role. That is a
widening of the contract, not an application of it, and it is not designed yet.

## 7. Where the platform does not do this yet

The work list, measured against this document at 60b402f.

1. **Domains are split.** A service owns the leftmost label, `host.yml` owns the rest,
   and the role joins them (`ansible/roles/service/tasks/main.yml:44`).
2. **TLS is entirely the platform's.** One ACME resolver hardwired into Traefik's unit
   (`hosts/shared/services/traefik/quadlet/traefik.container.j2:34-37`), one wildcard for
   the host's domain requested by Traefik's own router
   (`ansible/roles/service/templates/traefik-route.yml.j2:14-19`), `acme`/`acme_email` in
   `host.yml`, credentials in Traefik's folder. A service can say nothing.
3. **A host-level fallback certificate exists** (`ansible/roles/host_base/tasks/main.yml:84-160`)
   and both test hosts run on it. Replacing it means a self-signed method a service can
   _declare_, not a host that quietly supplies one.
4. **The platform contains a backup implementation**, not a mechanism:
   `kopia/kopia:0.23.1` pinned in `ansible/roles/service/templates/kopia-client.container.j2:5`,
   the server URL assembled from the host domain at `:14`, and
   `ansible/roles/service/files/kopia-client.sh`. `backup:` also dictates that the service
   be a pod.
5. **There is no restore.**
6. **There is no removal.** Deleting a service folder removes nothing: the user, its
   units, volumes, podman secrets and its **live Traefik router** all stay on the host.
7. **Every container resolves every route name placed on the host**
   (`ansible/roles/service/templates/storagebaby-hosts.conf.j2`), and a static test
   forbids a service from declaring its own. The target is the opposite: deny by default,
   and the service names the exceptions.
8. **A service's host dependencies live in `host.yml`** — `mesa`, `vulkan-radeon` and the
   rest are there for Jellyfin and Immich, and the render-node udev rule is in
   `host_base`.
9. **Traefik is fed two opposite ways.** Its TCP entrypoints are rendered into its own
   unit from a list the playbook collected; its HTTP routers are written by the platform
   straight into its configuration directory. One of the two is the pattern.
10. **One service's requirement sits in another's unit**: `readTimeout=0` on Traefik's
    `websecure` entrypoint exists because Kopia's upload is one long gRPC request.
11. **Platform constants a service cannot override**: the hook health wait, the image
    pull retries, and the auto-update timer's cadence.
12. **Bind mounts carry absolute host paths** — § 6.1.
