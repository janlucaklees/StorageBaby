# Cradle: derived loopback ports (stage 3 of the split)

Status: design, approved in conversation 2026-10-19 (JLK's four answers below); implementation authorised to run overnight on a Cradle branch, nothing merged before the morning
Date: 2026-10-19
Builds on: `2026-10-16-cradle-split-design.md` (stage 1), `2026-10-17-cradle-built-ins-design.md` (stage 2, rolled out 2026-10-19 22:41–22:49), `docs/ownership.md` in Cradle (§ 3 "Distributing host facts", § 1.1 the plugin test)

## 1. Goal

A service stops choosing the loopback port Traefik reaches it on. Cradle derives it,
deterministically, from the service and route name, keeps no state, and refuses a
collision at converge. What a service still says is the one thing only it knows: the
port its container listens on. Port bookkeeping across services and repositories, which
today is a per-host namespace checked by nothing a host repository runs, disappears.

Decisions (JLK, 2026-10-19):

1. **The service declares the container port, nothing else.** One dedicated key, explicit:
   `container_port`. The loopback port is derived and there is no setting for it.
2. **Derived, never stored.** A deterministic function of service name and route name into
   a fixed range; no state file; a collision is refused at converge, naming both services.
3. **The built-ins are no exception.** Traefik's dashboard and kopia's UI get derived ports
   like every other route.
4. **The rollout cost is accepted:** every service restarts once and every route file is
   rewritten in one converge, with the operator present.

Non-goals: plain-TCP ports (`tcp_ports` stays declared — a port like 21 is meaningful to
the client that dials it, and two services claiming one is still refused); container
ports inside a pod (the pod's parts reach each other on `127.0.0.1:<container port>` as
today); any change to how Traefik is fed (stage 2 left item 9 of the drift list as is).

## 2. The contract change

```yaml
# before                                   # after
port: 8080                                 container_port: 8080
domain: jellyfin                           domain: jellyfin

routes:                                    routes:
  - { domain: nextcloud.klees.io, port: 8280 }     - { domain: nextcloud.klees.io, container_port: 80 }
  - { domain: collabora.klees.io, port: 9980 }     - { domain: collabora.klees.io, container_port: 9980 }
```

`port` is **refused**, in `service.yml` and in `routes[]`, by the static suite and by the
role: a folder written for the old contract fails loudly rather than publishing the wrong
port. `container_port` is required wherever a `domain` is declared; a service with no
route (`build-echo`) declares neither.

A template never writes a port number for a route. It reads what the role hands down:

| Variable                   | Meaning                                                |
| -------------------------- | ------------------------------------------------------ |
| `publish_port`             | the derived loopback port of the first (or only) route |
| `container_port`           | the first route's container port, as declared          |
| `routes[n].publish_port`   | per route, in declaration order                        |
| `routes[n].container_port` | per route                                              |

So a single-container unit says `PublishPort=127.0.0.1:{{ publish_port }}:{{ container_port }}`
and a pod with two routes says one such line per route. The Traefik route file targets
`127.0.0.1:{{ route.publish_port }}`; the health check and the built-ins' own flags read
the same variables. `service.port` and `route.port` cease to exist as template variables
and a template that names them fails the render (undefined variable), which the static
suite turns into a named failure.

## 3. Derivation

```
publish_port = 10000 + (crc32("<service name>/<route domain>") mod 10000)
```

- Range 10000–19999: below Linux's ephemeral range (32768 and up), below the FTP passive
  range paperless declares (21100–21109) and above every port a container image
  listens on by convention; `tcp_ports` are refused inside the range. (The first draft
  said 20000–29999, which the passive range sits inside; corrected 2026-10-19 before
  implementation.)
- `crc32` over the UTF-8 bytes of `<name>/<domain>` where `<domain>` is the route's
  declared `domain` string (the label or the full name, as written). It is stable across
  hosts and Cradle versions and needs no library beyond Python's `zlib`. The function
  lives in Cradle's filter plugin, and the static suite and the integration verifiers
  import it from there rather than restating it.
- **Collisions are refused, not resolved.** Two routes on one host mapping to the same
  port, or a derived port equal to a declared TCP port or TCP target, fail the converge
  naming both sides. Expected frequency: with 20 routes in 10,000 slots the chance of any
  collision is about 2 %; the remedy is renaming the route's label, which is the service's
  own fact. No probing, no "next free port": a port that depended on what else was placed
  would not be deterministic.

The function and the range are **part of Cradle's interface**: changing either restarts
every service on every host, so they are named in the role README and pinned by a static
test with known vectors.

## 4. What moves where

| Was                                                                        | Is                                                                                   |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `port:` / `routes[].port` in `service.yml`                                 | `container_port:` / `routes[].container_port`                                        |
| hardcoded `PublishPort=127.0.0.1:8000:8000`                                | `PublishPort=127.0.0.1:{{ publish_port }}:{{ container_port }}` (per route in a pod) |
| `{{ service.port }}` in single-container units                             | `{{ publish_port }}` and `{{ container_port }}`                                      |
| route file `url: http://127.0.0.1:{{ route.port }}`                        | `{{ route.publish_port }}`                                                           |
| `test_ports.py`: uniqueness of declared ports                              | uniqueness of derived ports, refusal of `port`, known vectors of the function        |
| playbook: "No two placed services claim the same port" over declared ports | the same assert over derived ports plus TCP ports and targets                        |

The role derives `routes[n].publish_port` where it derives `routes[n].fqdn` today, from a
filter plugin `cradle_publish_port(name, domain)` in `ansible/filter_plugins/` (the one
directory Ansible loads beside the playbook without configuration; `ansible/ansible.cfg`
is never the working directory of any entry point). The
playbook's host-wide collision assert reads the derived values.

## 5. Tests

- **Static:** `test_ports.py` is rewritten: known vectors for the function (three
  name/domain pairs with their expected ports, so a changed function fails a test and not
  a host); every placed route on every fixture host derives into the range; no two
  derived ports collide on a host and none equals a declared TCP port or target; `port`
  anywhere is refused with a message naming the key and the file; `container_port`
  required beside `domain`. `test_render.py` asserts every rendered `PublishPort=` line
  starts with `127.0.0.1:<derived>:` and every route file targets the derived port.
- **Fixtures:** every fixture service folder and both built-ins are converted; a fixture
  with `port:` is kept as a refusal case in a temporary copy (the way stage 2 proved the
  `backup: none` refusal).
- **Integration:** the existing route verifiers already request through Traefik; they
  learn to read `publish_port` for the loopback probes they make; `test_ports`-style
  verification on the VM asserts `ss -ltn` shows each derived port bound on loopback by
  the right user.

## 6. Rollout on StorageBaby

This is a contract break for every service folder, in this repository and in the
external one (renaissance). Three repositories change together:

1. **Cradle** (on a branch, merged in the morning).
2. **StorageBaby**: fourteen `service.yml` files (`port` → `container_port`), every
   template's publish lines, both built-ins live in Cradle already. A PR, opened and not
   merged.
3. **renaissance**: its `deploy/storagebaby/service.yml` and template. The operator's
   repository; the exact diff is written into the rollout runbook for him to apply and
   promote to its `stable` in the same window.

The window, with the timer stopped throughout: merge Cradle and wait for its `stable`;
merge StorageBaby and wait for `stable`; promote renaissance's `stable`; `--check` (every
unit's publish line and every route file changed, nothing else); start the timer. Every
service restarts once; Traefik's route files are rewritten in place, so the only outage
per service is its own restart. Rollback is the reverse: pin `CRADLE_REF` to the branch
left at the pre-stage-3 commit and revert the two host-side changes.

Stage 2's lesson is a precondition: **Cradle's `stable` does not move until the host PRs
are ready**, and the three merges happen inside one window with the timer stopped.

## 7. Ownership doc edits

Platform table gains "Which loopback port a route is published on — derived from the
service and route name, stable, stateless; a collision is refused". Service table: "Its
domains, in full" row gains "and the port its container listens on"; the row that today
reads as the service's loopback port is removed. Drift list: nothing new; item 9 stays.

## 8. Acceptance

- Cradle: static and integration suites green on both fixture hosts on the branch; CI on
  the branch green.
- StorageBaby PR renders with `mise run render` against the branch: every unit's
  publish lines and every route file differ from the stage 2 render by the port number
  only; nothing else differs.
- The live rollout ends with every service active and reachable through its route, and
  the renaissance pointer converged on its updated folder.

## 9. Open

- The `adding-storagebaby-deployment` skill (the operator's) and Cradle's
  `docs/external-service.md` describe the old `port` key; both change with this stage,
  the skill by the operator.
- Stage 2's open list (a `backup: none` fixture host, a rendered replica block, a label
  override, the dashboard 401 verifier, password rotation on an existing repository) is
  carried, not closed, here.
