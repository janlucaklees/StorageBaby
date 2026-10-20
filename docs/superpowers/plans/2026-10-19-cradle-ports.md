# Cradle Derived Loopback Ports (Stage 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task, inline, in this session. Two repositories, a third one that is the operator's, a live NAS, a KVM test VM and a pre-stage-3 render baseline are context a fresh subagent would not have. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A service stops choosing the loopback port Traefik reaches it on. Cradle derives it from the service name and the route's declared domain, keeps no state, and refuses a collision at converge. What a service still declares is the one port only it knows: `container_port`, the port the process inside the container listens on. `port` is refused — statically and by the role — so a folder written for the old contract fails loudly instead of publishing the wrong number.

**Architecture:** One function, `10000 + (crc32("<name>/<domain>") mod 10000)`, in one file: a filter plugin `ansible/filter_plugins/cradle_ports.py`. The role derives `routes[n].publish_port` in the same loop that derives `routes[n].fqdn`, and hands templates four variables — `publish_port`, `container_port`, `routes[n].publish_port`, `routes[n].container_port`. The playbook's host-wide collision assert reads the derived values beside the declared TCP ports, and refuses a declared TCP port inside the range outright — 10000–19999 is reserved, which the range was chosen to make possible. The static suite and the integration verifiers **import that same module** rather than restating the arithmetic, and a known-vector test pins the function so a changed derivation fails a test and not a host.

**Tech Stack:** Ansible (ansible-core 2.21), Podman Quadlet, Molecule + testinfra in a KVM VM, pytest, sops/age, mise, Docker devtools image, GitHub Actions, `gh`.

**Spec:** `docs/superpowers/specs/2026-10-19-cradle-ports-design.md` (this repository; copied into Cradle in Task 3). Stage 1 and 2: `2026-10-16-cradle-split-design.md` / `2026-10-16-cradle-split.md`, `2026-10-17-cradle-built-ins-design.md` / `2026-10-17-cradle-built-ins.md`.

## Decisions for JLK, before this runs

Three of these were settled by reading the tree and are recorded; the first one is a real fork and the plan takes the safer branch, which is reversible either way.

1. **Resolved: the range is 10000–19999, and § 3's "`tcp_ports` are refused inside the range" is implemented as written.** The design's first range, 20000–29999, swallowed paperless's FTP passive range 21100–21109 (`hosts/StorageBaby/services/paperless/service.yml:29`, `tests/fixtures/host-repo/hosts/test-a/services/paperless/service.yml:29`), so a blanket in-range refusal would have failed every converge on day one. `PORT_BASE = 10000` clears it and every other declared TCP port — 21, 2121, 7777 and 21100–21109 are all above 19999 — so the refusal is real: **a declared `tcp_ports` `port`, `target` or `range` entry inside 10000–19999 fails the converge and a static test**, which is what keeps the one namespace from silently reappearing. Every derived value in this plan is the old one minus 10000; recomputed and still collision-free (decision 3).
2. **The filter plugin goes in `ansible/filter_plugins/`, not `ansible/plugins/filter/` as spec § 4 names.** `ansible/ansible.cfg` is **dead configuration**: every entry point runs `ansible/playbook.yml` with the working directory at the repository root (`tests/static/conftest.py:52` `cwd=str(REPO)`; `ansible/roles/host_base/files/cradle-deploy:201,215` `cd "$CRADLE_DIR"` then `ansible/playbook.yml`; Molecule's `converge.yml` is one `import_playbook: /repo/ansible/playbook.yml`), so Ansible never finds `./ansible.cfg` and `roles_path` works only because `ansible/roles/` is playbook-adjacent anyway. Measured in the devtools image: a plugin at `<playbook_dir>/filter_plugins/` loads in every mode, **including through `import_playbook` from the Molecule scenario directory**; one at `<playbook_dir>/plugins/filter/` is not found ("No filter named 'f2'"). So: no `ansible.cfg` edit, no env var, one file at `ansible/filter_plugins/cradle_ports.py`. Spec § 4's path is wrong and Task 3 corrects the spec copy's wording.
3. **No collisions anywhere.** Computed now, from the real specs: 18 routes on `test-a` and 5 on `test-ci` (built-ins included) derive into 23 distinct ports, none equal to a declared TCP port or target (21, 2121, 7777, 21100–21109); 18 routes across StorageBaby's 13 in-tree services, the two built-ins and renaissance derive into 18 distinct ports, none equal to 21, 2121 or 21100–21109, all inside 10000–19999. **And no declared TCP port anywhere is inside 10000–19999**, which is what makes decision 1's refusal implementable: 21, 2121, 7777 and 21100–21109 are all above it. **No fixture route label is renamed and no real route label is renamed by this plan.** The full tables are in Task 1 Step 11 and Task 5 Step 2.
4. **StorageBaby has 13 in-tree `service.yml` files plus the renaissance pointer**, not fourteen: `calino hermes immich jellyfin mailflow mcp mealie nextcloud openarchiver openproject paperless stirling-pdf yuzukam`, and `renaissance/source.yml`. Fourteen service _folders_.
5. **Traefik's `container_port` is a declaration the unit does not read, and that is accepted.** Traefik runs `Network=host` (`ansible/builtins/traefik/quadlet/traefik.container.j2:8`): there is no `PublishPort=`, the dashboard entrypoint binds `127.0.0.1:<port>` directly, so the two ports are one. Its unit therefore reads `{{ publish_port }}` on both the entrypoint flag and the health check, and the spec's `container_port` is declared because the contract requires it beside a `domain` and left unread, with a comment saying so. A static assertion holds the unit to `publish_port`, so the fiction cannot silently become the thing the route file points at.

## Global Constraints

- Cradle is `/home/jlk/Projects/cradle`. `git pull --ff-only` first; the plan was written against `f8eb03b`, which is also `stable`. **All Cradle work happens on a branch `ports` cut from `master`, and nothing in this plan merges it.** Cradle's CI runs on `pull_request` (`.github/workflows/ci.yml:5-6`) and the `promote` job is gated on `github.ref == 'refs/heads/master' && github.event_name == 'push'` (`:142-143`), so a PR from `ports` gets both suites and cannot move `stable`.
- StorageBaby work happens on branch `cradle-ports` in a fresh worktree under `.claude/worktrees/cradle-ports`, cut from `origin/master` (`004baef`). **The main StorageBaby checkout is not modified by any task** — no spec-status commit, no memory edit; those are the operator's, after the rollout.
- **No command touches the live NAS in this plan.** The rollout is a separate, operator-present step. The plan ends with: Cradle `ports` pushed and a Cradle PR open with CI green, `stable` **unmoved**, the StorageBaby PR open and unmerged, the renaissance diff written out verbatim, and a rollout runbook `docs/runbooks/2026-10-20-cradle-ports-rollout.md` (draft, in the StorageBaby PR).
- **Stage 2's lesson is a hard rule: Cradle's `stable` must not move until the host PRs are ready.** Three repositories break together here — Cradle, StorageBaby and renaissance — and a `stable` carrying stage 3 while a host folder still says `port:` is a host that refuses every converge until the next PR lands.
- **renaissance is not touched.** It is the operator's repository. Task 5 computes its exact diff and writes it into the runbook, verbatim, for him to apply and promote.
- **VM safety, before every Molecule step:** `free -g` shows ≥ 16 GiB available **and** `virsh -c qemu:///system list --all` shows no running VM. One Molecule step at a time. `prepare` runs once per VM lifecycle, so a `prepare.yml` change means `mise run molecule destroy` first.
- **Cradle commits:** one per task, conventional title, a body saying why, trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. `mise run format` before each; the task's own check must have passed; never `--no-verify`; ansible-lint clean (`tests/static/test_lint.py`). Pushes and `gh run watch` only in Task 4 (Cradle) and Task 5 (StorageBaby) — never a force-push.
- **No secret value is read, written or re-encrypted** in either repository. This stage touches no `*.sops.yaml` at all.
- **The derivation is Cradle's interface.** `10000 + (crc32(name + "/" + domain) % 10000)`, crc32 over UTF-8 via `zlib.crc32` masked `& 0xffffffff`, the domain string exactly as declared (label or full hostname). It lives in one file, and the static suite and the verifiers import that file rather than restating it — identical by construction, and pinned by three known vectors so a change to the file fails a test:

  | name        | domain               | port    |
  | ----------- | -------------------- | ------- |
  | `jellyfin`  | `jellyfin`           | `13465` |
  | `nextcloud` | `nextcloud.klees.io` | `18643` |
  | `traefik`   | `traefik`            | `12195` |

- `docs/ownership.md` § 1.1 holds: a service folder ships every fact about itself, and the loopback port stops being one of them.

---

### Task 1: The derivation, the role, the playbook, the fixtures and the static suite

**Files (Cradle, on branch `ports`):**

- Create: `ansible/filter_plugins/cradle_ports.py`
- Modify: `ansible/roles/service/tasks/main.yml:30-59` (the refusal, `_route_specs`, the fqdn loop, the two new facts)
- Modify: `ansible/roles/service/templates/traefik-route.yml.j2:59`
- Modify: `ansible/playbook.yml:374-397` (the `_claimed_ports` loop, the assert's message, and the new in-range TCP refusal after it)
- Modify: `ansible/builtins/traefik/service.yml`, `ansible/builtins/traefik/quadlet/traefik.container.j2:17,49`, `ansible/builtins/kopia/service.yml`, `ansible/builtins/kopia/quadlet/kopia.container.j2:15`
- Modify: 14 fixture `service.yml` and 13 fixture quadlet templates under `tests/fixtures/host-repo/hosts/test-{a,ci}/services/` and `tests/fixtures/sources/ext-echo/deploy/cradle/`
- Modify: `tests/static/conftest.py` (`routes_of`, `route_ports` → `publish_ports`, the derivation import), `tests/static/test_ports.py` (rewritten), `tests/static/test_contract.py:16,53,139-145,168-173`, `tests/static/test_quadlet_conventions.py:122-156`, `tests/static/test_render.py` (two assertions added)

**Interfaces:**

- Produces: the filters `cradle_publish_port(name, domain)` and `cradle_publish_ports(spec)`; the template variables `publish_port`, `container_port` and `routes[n].{publish_port, container_port}`; three refusals (a declared `port`; a derived port colliding with another route or a declared TCP port on the host; a declared TCP port, target or range entry inside 10000-19999); `conftest.derive_publish_port`, `conftest.publish_ports`, `conftest.PORT_BASE`, `conftest.PORT_SPAN`.
- Consumes: nothing. `service.port` and `route.port` cease to exist.

- [ ] **Step 1: the branch, and the render baseline before anything moves**

`mise run render` renders with the Cradle checkout as it stands, and that checkout is where the work happens — so the "before" side has to be taken first. Nothing in either repository is modified by this step.

```bash
cd /home/jlk/Projects/cradle && git pull --ff-only && git checkout -b ports
B=/tmp/claude-1000/-home-jlk-Projects-StorageBaby/205176ee-280c-48c7-baa9-d3b66f2e4832/scratchpad/stage3
mkdir -p "$B"
git -C /home/jlk/Projects/cradle rev-parse master > "$B/CRADLE_BEFORE"
git -C /home/jlk/Projects/StorageBaby rev-parse HEAD > "$B/HOST_BEFORE"
mise run render /home/jlk/Projects/StorageBaby hosts/StorageBaby "$B/before"
ls "$B/before"
```

Expected: one directory per placed StorageBaby service plus `traefik`, `kopia` and `traefik-dynamic.d`; `renaissance` absent (its repository is not fetched, the playbook says so and skips it). `$B/CRADLE_BEFORE` is also the SHA the rollback branch is pushed at in Task 3 Step 7, and Task 5 Step 5 diffs against `$B/before`.

- [ ] **Step 2: the derivation, in one file**

Create `ansible/filter_plugins/cradle_ports.py`:

```python
"""Cradle's loopback port derivation: the one place the function lives.

A service does not choose the port Traefik reaches it on. Cradle derives it from the
service name and the route's declared domain, which makes it stateless -- no file, no
registry, no "next free port" -- and identical on every host and every Cradle version.
What a service declares is `container_port`, the port the process inside the container
listens on, which is the one port only the service knows.

`filter_plugins/` and not `plugins/filter/`: Ansible adds `<playbook_dir>/filter_plugins`
to the plugin loader by itself, in every mode this repository runs in -- the static
suite's `ansible-playbook ansible/playbook.yml` from the repository root, the deploy
script's run after `cd "$CRADLE_DIR"`, and Molecule's `import_playbook` of
`/repo/ansible/playbook.yml` from the scenario directory. `plugins/filter/` is the
*collection* layout and is not found in any of them, and `ansible/ansible.cfg` is never
read at all, because no entry point has the working directory inside `ansible/`. So this
path is the one that needs no configuration to work.

The function and the range are part of Cradle's *interface*: changing either restarts
every service on every host. `tests/static/test_ports.py` pins them with known vectors,
and `ansible/roles/service/README.md`, "Ports", names them.
"""

import zlib

# 10000-19999: above every well-known and registered port a service image listens on,
# below the ephemeral range, and clear of every plain-TCP port this platform's services
# declare -- 21, 2121, 7777 and the FTP passive range 21100-21109 are all above 19999,
# which is what lets a declared TCP port *inside* this range be refused outright instead
# of only when it happens to equal a derived one. 10,000 slots wide: with ~20 routes on a
# host the chance of any collision is about 2 %, and a collision is refused at converge
# naming both sides rather than resolved. A resolved collision would mean a port that
# depends on what else is placed, which is not deterministic and not what a route file
# could carry.
PORT_BASE = 10000
PORT_SPAN = 10000


def cradle_publish_port(name, domain):
    """The loopback port one route is published on.

    crc32 over the UTF-8 bytes of `<name>/<domain>`, where `<domain>` is the route's
    declared string exactly as written -- the bare label or the full hostname. Not the
    resolved fqdn: a service placed on two hosts with different default domains would
    otherwise get two different ports for one route, and the number would stop being a
    property of the service folder.

    `& 0xffffffff` because `zlib.crc32` is documented to return a signed value on some
    platforms; masking makes the result the unsigned one everywhere, which is what the
    known vectors were computed with.
    """
    key = f"{name}/{domain}".encode("utf-8")
    return PORT_BASE + (zlib.crc32(key) & 0xFFFFFFFF) % PORT_SPAN


def cradle_publish_ports(spec):
    """Every loopback port a spec's routes derive into, in declaration order.

    Here and not as a Jinja expression at the two call sites: Ansible's Jinja has no
    comprehension, so `map` over a list of routes cannot reach both the spec's name and
    each route's domain in one pass without an intermediate fact. The playbook's
    host-wide collision assert and any future consumer get the list instead.
    """
    routes = spec.get("routes")
    if routes is None:
        routes = [{"domain": spec["domain"]}] if "domain" in spec else []
    return [cradle_publish_port(spec["name"], route["domain"]) for route in routes]


class FilterModule:
    def filters(self):
        return {
            "cradle_publish_port": cradle_publish_port,
            "cradle_publish_ports": cradle_publish_ports,
        }
```

- [ ] **Step 3: the role refuses `port` and derives the two ports per route**

In `ansible/roles/service/tasks/main.yml`, insert before "Derive hostname, backup flag and raw routes":

```yaml
# A folder written for the pre-2026-10-19 contract, named rather than mis-published. The
# refusal is here and not only in the static suite because the suite cannot see a service
# that lives in its own repository: a pointer's `service.yml` is fetched onto the host,
# and this is the only place that reads it before a unit is written.
- name: The service does not choose its loopback port
  ansible.builtin.assert:
    that:
      - service.port is not defined
      - service.routes | default([]) | selectattr('port', 'defined') | list | length == 0
    fail_msg: >-
      {{ service.name }} declares `port`. Cradle derives the loopback port from the
      service name and the route's domain since 2026-10-19 and there is no setting for
      it -- declare `container_port`, the port the process inside the container listens
      on, in service.yml and in each `routes[]` entry. See
      ansible/roles/service/README.md, "Ports".
    quiet: true
```

Then `_route_specs` in the next task becomes:

```yaml
_route_specs:
  "{{ service.routes | default(([{'domain': service.domain, 'container_port': service.container_port}]
  if service.domain is defined else [])) }}"
```

the fqdn loop gains the derived port in the same `combine` — one pass, because both are
properties of the same route and a second loop would be a second list to keep in step:

```yaml
- name: Resolve route fqdns and derive each route's loopback port
  ansible.builtin.set_fact:
    routes_out: >-
      {{ routes_out + [item | combine({
         'fqdn': item.domain if '.' in item.domain else item.domain ~ '.' ~ domain,
         'publish_port': service.name | cradle_publish_port(item.domain)})] }}
  loop: '{{ _route_specs }}'
  loop_control:
    label: '{{ item.domain }}'
```

and the task that keeps the single-route shorthand gains its two ports:

```yaml
# The first route's three facts, for the single-route templates that are the majority:
# `fqdn`, `publish_port` (what Traefik dials) and `container_port` (what the process
# inside listens on). A service with no route has all three empty -- `build-echo` is the
# one, and no template of its reads any of them.
- name: Routes, and the first route's fqdn and two ports for the single-route templates
  ansible.builtin.set_fact:
    routes: '{{ routes_out }}'
    fqdn: "{{ (routes_out | first).fqdn if routes_out | length > 0 else '' }}"
    publish_port: "{{ (routes_out | first).publish_port if routes_out | length > 0 else '' }}"
    container_port: "{{ (routes_out | first).container_port if routes_out | length > 0 else '' }}"
```

- [ ] **Step 4: the route file targets the derived port**

`ansible/roles/service/templates/traefik-route.yml.j2:59`:

```jinja
          - url: "{{ scheme }}://127.0.0.1:{{ route.publish_port }}"
```

- [ ] **Step 5: the playbook's collision assert reads the derived ports**

In `ansible/playbook.yml`, "Collect every port the placed services claim" becomes:

```yaml
# Every loopback number the host's placement claims: the derived route ports, the TCP
# entrypoints and the TCP targets, in one namespace. The derivation is stateless, so a
# collision is a property of the placement alone and is refused here rather than
# resolved -- a port that depended on what else was placed would not be deterministic.
# This is also the only check a *pointer* service passes through: tests/static cannot
# read a spec it has no local tree for, so two services colliding across repositories
# surface here, at converge, naming both.
#
# `cradle_publish_ports` takes the whole spec because Ansible's Jinja has no
# comprehension to reach the name and each route's domain in one map.
- name: Collect every port the placed services claim
  ansible.builtin.set_fact:
    _claimed_ports: >-
      {{ (_claimed_ports | default([]))
         + (_spec | cradle_publish_ports)
         + (_spec.tcp_ports | default([]) | selectattr('target', 'defined') | map(attribute='target') | list) }}
```

and the assert's `fail_msg` loses "Route ports" for the derived form:

```yaml
          ports claimed twice on {{ host_name }}: {{ _duplicates | join(', ') }}.
          Derived route ports, TCP entrypoints and TCP targets are one namespace per
          host. A route's port is crc32-derived from `<service>/<domain>`, so the remedy
          is to rename the route's label -- which is the service's own fact.
```

The loop's other half is unchanged, and so is the `_spec.port` term's **removal**: the
line `+ ([_spec.port] if (_spec.port is defined and _spec.routes is not defined) else [])`
goes, because `cradle_publish_ports` already covers the shorthand.

- [ ] **Step 6: the built-ins declare `container_port`**

`ansible/builtins/kopia/service.yml`, the first two keys:

```yaml
name: kopia
# The port kopia's server listens on inside the container. The loopback port Traefik
# reaches it on is derived from `kopia/<route label>` and is in no file -- see
# ansible/roles/service/README.md, "Ports".
container_port: 51515
```

`ansible/builtins/kopia/quadlet/kopia.container.j2:15`:

```jinja
PublishPort=127.0.0.1:{{ publish_port }}:{{ container_port }}
```

`ansible/builtins/traefik/service.yml`:

```yaml
name: traefik
# Declared because the contract requires a `container_port` beside a `domain`, and unread
# by this service's unit: Traefik runs `Network=host`, so it publishes nothing and its
# dashboard entrypoint binds the host's loopback directly -- the two ports are one number,
# and that number is the derived `publish_port` the route file points at. The unit reads
# `publish_port` on both the entrypoint flag and the health check, and
# `test_quadlet_conventions.py` holds it there.
container_port: 8080
```

`ansible/builtins/traefik/quadlet/traefik.container.j2:17` and `:49`:

```jinja
  --entrypoints.traefik.address=127.0.0.1:{{ publish_port }} \
```

```jinja
HealthCmd=wget --spider -q http://127.0.0.1:{{ publish_port }}/ping || exit 1
```

- [ ] **Step 7: the fixture specs**

`port:` → `container_port:` in each, and the value changes wherever the old route port was
a _mapping_ rather than the container's own port — the pod's second number is the truth:

| spec                                           | was                         | becomes                                         |
| ---------------------------------------------- | --------------------------- | ----------------------------------------------- |
| `test-a/services/jellyfin/service.yml:2`       | `port: 8096`                | `container_port: 8096`                          |
| `test-a/services/immich/service.yml:2`         | `port: 2283`                | `container_port: 2283`                          |
| `test-a/services/yuzukam/service.yml:2`        | `port: 3000`                | `container_port: 3000`                          |
| `test-a/services/stirling-pdf/service.yml:2`   | `port: 8180`                | `container_port: 8080`                          |
| `test-a/services/mealie/service.yml:2`         | `port: 9000`                | `container_port: 9000`                          |
| `test-a/services/mcp/service.yml:2`            | `port: 3002`                | `container_port: 3002`                          |
| `test-a/services/hermes/service.yml:5`         | `port: 9119`                | `container_port: 9119`                          |
| `test-a/services/openarchiver/service.yml:5`   | `port: 3001`                | `container_port: 3000`                          |
| `test-a/services/openproject/service.yml:2`    | `port: 8081`                | `container_port: 8080`                          |
| `test-a/services/mailflow/service.yml:2`       | `port: 3003`                | `container_port: 80`                            |
| `test-a/services/paperless/service.yml:2`      | `port: 8000`                | `container_port: 8000`                          |
| `test-a/services/calino/service.yml:13,22`     | `port: 8085` / `port: 8086` | `container_port: 8085` / `container_port: 8086` |
| `test-a/services/nextcloud/service.yml:10,13`  | `port: 8280` / `port: 9980` | `container_port: 80` / `container_port: 9980`   |
| `sources/ext-echo/deploy/cradle/service.yml:7` | `port: 8118`                | `container_port: 8118`                          |

`stirling-pdf`, `openarchiver`, `openproject`, `mailflow` and `nextcloud`'s first route are
exactly the five where the two numbers differed, and the second number of the old publish
line is the one to carry over — `test_render.py` Step 13 is the gate that catches a wrong
one, because the rendered publish line would map a port nothing listens on.

`test-a/services/{build-echo,tcp-echo}/service.yml` are unchanged: neither declares a
domain, and `tcp-echo`'s 7777 is a TCP target, not a route. `test-ci/services/{paperless,yuzukam}`
are symlinks into `test-a` and change with it; `test-ci/services/{build-echo,ext-echo,tcp-echo}`
are the byte-identical copies `test_a_service_placed_on_several_hosts_is_one_folder_or_identical_copies`
holds — `ext-echo` is a pointer in both and has no copy to edit, so only its repository's
folder under `tests/fixtures/sources/` changes.

Each converted spec keeps its own comment where it had one, and gains nothing: the
explanation of what `container_port` means belongs in the role README, not in fourteen files.

- [ ] **Step 8: the fixture templates**

Four single-container templates read `{{ service.port }}` and become:

```jinja
PublishPort=127.0.0.1:{{ publish_port }}:{{ container_port }}
```

— `test-a/services/jellyfin/quadlet/jellyfin.container.j2:8`,
`test-a/services/yuzukam/quadlet/yuzukam.container.j2:8`,
`test-a/services/stirling-pdf/quadlet/stirling-pdf.container.j2:8`,
`sources/ext-echo/deploy/cradle/quadlet/ext-echo.container.j2:14`.

Nine pods carry literal numbers. Each route's publish line becomes a templated one, and
the comment above it — which today says "Literal, not `{{ service.port }}`: the static
check reads this line out of the raw template" — becomes the reason it can no longer be:

```jinja
{# The loopback port is Cradle's, derived and in no file; `container_port` is this pod's
   own. The static check reads this line out of the raw template and accepts a `publish_port`
   expression for a route and a literal only for a `tcp_ports` target. #}
PublishPort=127.0.0.1:{{ publish_port }}:{{ container_port }}
```

| pod template                                               | line(s) | becomes                                                                                                   |
| ---------------------------------------------------------- | ------- | --------------------------------------------------------------------------------------------------------- |
| `test-a/services/immich/quadlet/immich.pod.j2`             | 3, 5    | `PublishPort=127.0.0.1:{{ publish_port }}:{{ container_port }}`                                           |
| `test-a/services/mealie/quadlet/mealie.pod.j2`             | 3, 6    | the same                                                                                                  |
| `test-a/services/mcp/quadlet/mcp.pod.j2`                   | 3, 10   | the same                                                                                                  |
| `test-a/services/hermes/quadlet/hermes.pod.j2`             | 3, 11   | the same                                                                                                  |
| `test-a/services/openarchiver/quadlet/openarchiver.pod.j2` | 3, 7    | the same                                                                                                  |
| `test-a/services/mailflow/quadlet/mailflow.pod.j2`         | 3, 10   | the same                                                                                                  |
| `test-a/services/openproject/quadlet/openproject.pod.j2`   | 3, 9    | the same                                                                                                  |
| `test-a/services/calino/quadlet/calino.pod.j2`             | 10, 11  | one line per route: `{{ routes[0].publish_port }}:{{ routes[0].container_port }}` and `{{ routes[1].* }}` |
| `test-a/services/nextcloud/quadlet/nextcloud.pod.j2`       | 7, 8    | the same two-route form                                                                                   |
| `test-a/services/paperless/quadlet/paperless.pod.j2`       | 3, 7    | line 7 templated; **lines 9 and 14 stay literal** — `2121` and `21100-21109` are TCP targets, not routes  |

`test-a/services/tcp-echo/quadlet/tcp-echo.container.j2:16` stays literal for the same
reason, and its `test-ci` copy with it. After the edits, copy each changed `test-ci` real
copy from `test-a` so the byte-identity test cannot fail on a missed hunk:

```bash
cd /home/jlk/Projects/cradle/tests/fixtures/host-repo/hosts
diff -r test-a/services/tcp-echo test-ci/services/tcp-echo
diff -r test-a/services/build-echo test-ci/services/build-echo
```

Expected: no output from either.

- [ ] **Step 9: `conftest` imports the derivation instead of restating it**

In `tests/static/conftest.py`, after `RENDER_TCP_BIND_ADDRESS`:

```python
# The derivation, imported rather than restated. Cradle's loopback ports are a function of
# the service and route name, and a *copy* of that function in the test suite is a copy
# that can drift from the one a host runs -- the failure mode would be a green suite and a
# host whose route files point at ports nothing listens on. So the suite loads the filter
# plugin itself; `test_ports.py` pins the function with known vectors, which is what makes
# the single definition safe to change only deliberately.
_ports = importlib.util.spec_from_file_location(
    "cradle_ports", REPO / "ansible" / "filter_plugins" / "cradle_ports.py"
)
cradle_ports = importlib.util.module_from_spec(_ports)
_ports.loader.exec_module(cradle_ports)
derive_publish_port = cradle_ports.cradle_publish_port
PORT_BASE = cradle_ports.PORT_BASE
PORT_SPAN = cradle_ports.PORT_SPAN
```

with `import importlib.util` at the top. Then `routes_of` and the port helper:

```python
def routes_of(spec: dict) -> list[dict]:
    """Every route a service declares: the `routes` list, or the domain+container_port shorthand."""
    if "routes" in spec:
        return spec["routes"]
    return [{"domain": spec["domain"], "container_port": spec["container_port"]}] if "domain" in spec else []
```

and `route_ports` is replaced — the name goes with the concept, because a _declared_ route
port no longer exists:

```python
def publish_ports(spec: dict) -> list[int]:
    """Every loopback port a service's routes are published on, derived.

    Takes the whole spec, because the derivation needs the service's name as well as each
    route's domain -- which is also why this is not a property of a route on its own.
    """
    return [derive_publish_port(spec["name"], r["domain"]) for r in routes_of(spec)]
```

`placed_fqdns` and `route_fqdn` are unchanged. Then fix the two importers:
`tests/static/test_ports.py` (rewritten below) and `tests/static/test_quadlet_conventions.py:6`.

- [ ] **Step 10: `test_ports.py`, rewritten**

```python
from collections import defaultdict

from conftest import (
    PORT_BASE,
    PORT_SPAN,
    derive_publish_port,
    host_names,
    placements,
    publish_ports,
    routes_of,
    tcp_ports,
)

# Traefik's own two listeners. A service claiming either would either fail to bind or
# take HTTPS off the host.
RESERVED = {80: "traefik's web entrypoint", 443: "traefik's websecure entrypoint"}

# Three (name, domain) pairs and the port each derives into, computed once by hand and
# written down here. This is the pin on Cradle's *interface*: the function and its range
# are not an implementation detail, because changing either rewrites every route file and
# restarts every service on every host. A refactor that changes the arithmetic fails this
# test with three numbers to compare, instead of failing a rollout.
#
# `traefik/traefik` is in the list on purpose: the built-ins are no exception to the
# derivation (spec 2026-10-19, decision 3), and the dashboard's port is as derived as any.
KNOWN_VECTORS = {
    ("jellyfin", "jellyfin"): 13465,
    ("nextcloud", "nextcloud.klees.io"): 18643,
    ("traefik", "traefik"): 12195,
}


def test_the_derivation_matches_its_known_vectors():
    for (name, domain), expected in KNOWN_VECTORS.items():
        assert derive_publish_port(name, domain) == expected, f"{name}/{domain}"


def test_the_derivation_is_the_declared_range():
    assert (PORT_BASE, PORT_SPAN) == (10000, 10000)


def test_the_derivation_reads_the_declared_domain_and_not_the_fqdn():
    """A label and the hostname it completes to are different inputs.

    The port is a property of the service *folder*, so it must not depend on the host's
    default domain -- a folder symlinked onto two hosts would otherwise publish on two
    different ports for one route, and the number would stop being derivable from the
    folder alone.
    """
    assert derive_publish_port("jellyfin", "jellyfin") != derive_publish_port(
        "jellyfin", "jellyfin.home.klees.io"
    )


def test_every_placed_route_derives_into_the_range():
    for p in placements():
        for port in publish_ports(p.spec):
            assert PORT_BASE <= port < PORT_BASE + PORT_SPAN, f"{p.host}/{p.name}: {port}"


def test_no_service_declares_a_loopback_port():
    """`port` is refused, not ignored.

    A folder written for the pre-2026-10-19 contract would otherwise converge green and
    publish on a port nothing points at -- the route file carries the derived number. The
    role refuses it too (`roles/service/tasks/main.yml`), which is what covers a service
    whose folder lives in its own repository and that this suite never sees.
    """
    for p in placements():
        spec_file = p.dir / "service.yml"
        assert "port" not in p.spec, f"{spec_file}: `port` is refused; declare `container_port`"
        for r in routes_of(p.spec):
            assert "port" not in r, f"{spec_file}: route {r['domain']} declares `port`; use `container_port`"


def test_ports_are_unique_per_host():
    """Derived route ports, TCP entrypoints and TCP targets are one namespace.

    A TCP entrypoint binds one concrete address (`tcp_bind_address`) and everything else on
    the host binds loopback, so a public port and a loopback port of the same number do
    coexist -- that is exactly what lets a `tcp_ports` entry forward a port to itself. They
    are still held in one namespace per host, deliberately: `tcp_bind_address` is a host
    variable, and a host that set it to a wildcard would bring the collision straight back,
    with every socket in the pair belonging to a different service. A number on a host
    means one thing.

    Within the pair that is a real collision either way: two routes deriving into one port
    would share a loopback socket and one of them would be dead, and a target that equals
    a derived route port is two processes on 127.0.0.1.

    The remedy for a derived collision is to rename the route's label, which is the
    service's own fact -- there is no "next free port", because a port that depended on
    what else was placed would not be deterministic.

    Overlapping ranges need no check of their own: `tcp_ports` expands a range port by
    port, so two ranges that overlap arrive here as the same number twice.
    """
    all_placements = placements()
    for host in host_names():
        seen = defaultdict(list)
        for p in all_placements:
            if p.host != host:
                continue
            # Every route's port, not just the first: a multi-route service publishes
            # one loopback port per route, and each of them has to be free on the host.
            for route, port in zip(routes_of(p.spec), publish_ports(p.spec)):
                seen[port].append(f"{p.name} route {route['domain']}")
            for entry in tcp_ports(p.spec):
                seen[entry["port"]].append(f"{p.name} tcp entrypoint")
                # Counted as a second number only when it is one: `target == port` is the
                # default and the shape a passive range must have, and it is one claim --
                # the entrypoint is on the host's address, the target on loopback.
                if entry["target"] != entry["port"]:
                    seen[entry["target"]].append(f"{p.name} tcp target")
        dupes = {port: names for port, names in seen.items() if len(names) > 1}
        assert not dupes, f"{host}: port collisions {dupes}"
        clash = {port: RESERVED[port] for port in set(seen) & set(RESERVED)}
        assert not clash, f"{host}: {clash} cannot be claimed by a service"
```

Then the other half of spec § 3 — the range held clear of what a service does still
declare for itself:

```python
def test_no_declared_tcp_port_is_inside_the_derivation_range():
    """A `tcp_ports` entry inside 10000-19999 is refused, not merely checked for equality.

    `test_ports_are_unique_per_host` above catches a TCP port that *happens* to equal a
    derived one today. This catches the one that does not yet: a port inside the range is
    a collision waiting for someone to add a route, and the route's number is not a thing
    anybody chooses, so the collision would arrive with a service that had nothing to do
    with it. The range was chosen to make this refusable -- 21, 2121, 7777 and the FTP
    passive range 21100-21109 are all above 19999 -- and refusing it is what keeps that
    true as services are added.

    A range entry is checked end to end and not only at its bounds, because a range that
    straddles 19999 has part of itself inside.
    """
    for p in placements():
        for entry in tcp_ports(p.spec):
            for port in (entry["port"], entry["target"]):
                assert not (PORT_BASE <= port < PORT_BASE + PORT_SPAN), (
                    f"{p.host}/{p.name}: tcp port {port} is inside the derivation range "
                    f"{PORT_BASE}-{PORT_BASE + PORT_SPAN - 1}, which Cradle reserves for "
                    f"derived loopback ports"
                )
```

`tcp_ports(spec)` already expands a `range:` entry port by port and normalises each to
`{port, target}` (`tests/static/conftest.py:192-200`), so one loop covers both forms.

The playbook's half of the same claim goes beside the collision assert of Step 5:

```yaml
# The range is Cradle's, and a declared TCP port inside it is refused outright rather
# than only when it collides today: the derived ports are nobody's choice, so a port
# in the range is a collision that would arrive with the next service to be placed and
# name the wrong one. `_tcp_expanded` is already one entry per port, ranges included.
- name: No declared TCP port is inside the derived-port range
  ansible.builtin.assert:
    that: _inside | length == 0
    fail_msg: >-
      TCP ports inside Cradle's derived-port range on {{ host_name }}:
      {{ _inside | join(', ') }}. 10000-19999 is reserved for the loopback ports
      Cradle derives for routes; declare a TCP port outside it.
    quiet: true
  vars:
    # Both sides of every declared TCP port: `_tcp_expanded` is the entrypoint list
    # with ranges already expanded port by port (`ansible/playbook.yml:348-357`), and
    # `_placed_tcp` is the raw declarations, from which the targets come -- the
    # entrypoint list deliberately drops them.
    _tcp_all: >-
      {{ (_tcp_expanded | default([]))
         + (_placed_tcp | default([]) | selectattr('target', 'defined') | map(attribute='target') | list) }}
    _inside: "{{ _tcp_all | select('ge', 10000) | select('le', 19999) | unique | sort | list }}"
```

No new fact is needed: both lists already exist by the time the collision assert runs, and
this task goes directly after it.

- [ ] **Step 11: `test_contract.py`**

`:16` `"port"` → `"container_port"` in `KEYS`; `:53` `ROUTE_KEYS` likewise. Then the three
port assertions:

```python
        assert isinstance(spec.get("container_port"), int), (
            f"{p.name}: a service with a domain needs a container_port -- the port the "
            f"process inside the container listens on. The loopback port Traefik reaches "
            f"it on is derived and is not declared anywhere."
        )
```

```python
    if "container_port" in spec:
        assert isinstance(spec["container_port"], int)
```

```python
        assert "domain" not in spec and "container_port" not in spec, (
            f"{p.name}: use either routes or domain+container_port"
        )
```

```python
            assert {"domain", "container_port"} <= set(r), (
                f"{p.name}: route entries need domain and container_port"
            )
```

```python
            assert isinstance(r["container_port"], int)
```

The `tcp_ports` block is untouched: `port`, `target` and `range` there are a different
key in a different place, and they stay declared (spec § 1, non-goals).

- [ ] **Step 12: `test_quadlet_conventions.py` keeps the shape check and gives up the numbers**

The numeric claim moves to the render (Step 13): a template cannot carry a derived number,
so a test reading raw templates can only say what _kind_ of value is in the host position.
That is still worth saying, and precisely:

```python
# A publish line's host-side port is either Cradle's -- a Jinja expression naming
# `publish_port`, which is how a route is published since the ports are derived -- or a
# literal, which may only be a `tcp_ports` target. Keeping the two apart is the whole of
# what this file can still check: the numbers themselves exist only after a render, and
# `test_render.py::test_published_ports_are_exactly_the_derived_route_ports` checks them
# there.
PUBLISH_LINE = re.compile(r"^PublishPort=127\.0\.0\.1:([^:]+):", flags=re.M)
LITERAL = re.compile(r"^(\d+)(?:-(\d+))?$")


def literal_published_ports(text: str) -> list[int]:
    """Every host port a template publishes as a literal, a `lo-hi` range port by port."""
    out = []
    for host_side in PUBLISH_LINE.findall(text):
        m = LITERAL.match(host_side.strip())
        if m:
            lo, hi = m.group(1), m.group(2)
            out += list(range(int(lo), int(hi or lo) + 1))
    return out


@pytest.mark.parametrize(
    "p",
    [p for p in placements() if list((p.dir / "quadlet").glob("*.pod.j2"))],
    ids=lambda p: f"{p.host}/{p.name}",
)
def test_pod_publishes_its_routes_through_the_derived_port(p):
    # In a pod the ports are the pod's, not the containers': a container that publishes
    # its own would fail to start.
    pod = next((p.dir / "quadlet").glob("*.pod.j2")).read_text()
    lines = PUBLISH_LINE.findall(pod)
    templated = [h for h in lines if not LITERAL.match(h.strip())]
    # One templated line per route, and each of them naming `publish_port`: a pod that
    # published a route on a literal would be pointing at a number the route file does
    # not carry, which is a service unreachable through Traefik and green everywhere else.
    assert len(templated) == len(routes_of(p.spec)), f"{p.name}: {lines} vs {len(routes_of(p.spec))} routes"
    for host_side in templated:
        assert "publish_port" in host_side, f"{p.name}: {host_side} is not the derived port"
    # A literal is allowed only where the protocol itself names the port: a `tcp_ports`
    # target, which the Traefik TCP service forwards to and which therefore cannot move.
    targets = [e["target"] for e in tcp_ports(p.spec)]
    assert sorted(literal_published_ports(pod)) == sorted(targets), f"{p.name}: literal ports vs tcp targets"
    assert not re.search(r"^PublishPort=(?!127\.0\.0\.1:)", pod, flags=re.M)
    for tpl in (p.dir / "quadlet").glob("*.container.j2"):
        text = tpl.read_text()
        assert re.search(rf"^Pod={p.name}\.pod$", text, flags=re.M), f"{tpl.name} must join {p.name}.pod"
        assert not re.search(r"^PublishPort=", text, flags=re.M), f"{tpl.name}: publish ports on the pod, not the container"


@pytest.mark.parametrize("path", _unit_templates(), ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_no_template_names_the_retired_port_variables(path):
    """`service.port` and `route.port` do not exist any more.

    A template that names one fails the render with an undefined-variable trace pointing
    at a Jinja line; this says what is actually wrong, in the one place a reader of the
    old contract would look.
    """
    text = path.read_text()
    # `.port }}` is the general form and covers `service.port`, `route.port` and
    # `routes[n].port` in one; `container_port` and `publish_port` do not match it,
    # because neither has a dot immediately before `port`.
    for bad in ("service.port", ".port }}"):
        assert bad not in text, f"{path.name}: `{bad}` is gone -- use publish_port / container_port"
```

The ten templates whose _comment_ says "Literal, not `{{ service.port }}`" are rewritten
in Step 8, so the check also catches a comment left behind — which is the right outcome: a
comment naming a variable that no longer exists is the next reader's wrong turn.

with `routes_of` added to the `from conftest import ...` line and `route_ports` removed.
`test_published_ports_are_loopback_only` at `:82` is unchanged — it reads the prefix only.

The one built-in that publishes nothing needs its own line, because `Network=host` means
the dashboard's port reaches the route file through a flag and not through a publish:

```python
def test_traefik_binds_its_dashboard_on_the_derived_port():
    """Traefik publishes nothing -- `Network=host` -- so its entrypoint flag and its health
    check are what the derived port has to reach, and its declared `container_port` is
    unread. A flag left on `service.port` would be a dashboard on a port its own route
    file does not point at."""
    unit = (BUILTINS / "traefik" / "quadlet" / "traefik.container.j2").read_text()
    assert "--entrypoints.traefik.address=127.0.0.1:{{ publish_port }}" in unit
    assert "http://127.0.0.1:{{ publish_port }}/ping" in unit
```

- [ ] **Step 13: `test_render.py` gets the numbers**

Two additions. First, the rendered publish lines, which is where the arithmetic and the
spec meet:

```python
@pytest.mark.parametrize(
    "p",
    [p for p in placements() if quadlet_templates(p)],
    ids=lambda p: f"{p.host}/{p.name}",
)
def test_published_ports_are_exactly_the_derived_route_ports(rendered, p):
    """Every rendered `PublishPort=` is a derived route port or a declared TCP target.

    On the rendered unit and not on the template, because a derived port exists only
    after a render -- and this is the assertion the whole stage rests on: a publish line
    that disagrees with the route file by one digit is a service that is up, healthy and
    unreachable.
    """
    unit_dir = rendered / p.host / p.name
    found = []
    for unit in sorted(list(unit_dir.glob("*.pod")) + list(unit_dir.glob("*.container"))):
        for ln in unit.read_text().splitlines():
            if ln.startswith("PublishPort="):
                assert ln.startswith("PublishPort=127.0.0.1:"), f"{unit.name}: {ln}"
                host_side = ln.split(":", 2)[1]
                lo, _, hi = host_side.partition("-")
                found += list(range(int(lo), int(hi or lo) + 1))
    expected = publish_ports(p.spec) + [e["target"] for e in tcp_ports(p.spec)]
    # Traefik publishes nothing: `Network=host`, and its dashboard entrypoint binds the
    # host's loopback through a flag instead. `test_traefik_dashboard_entrypoint_is_derived`
    # below is its half of this claim.
    if p.name == "traefik":
        assert found == []
        return
    assert sorted(found) == sorted(expected), f"{p.host}/{p.name}: {found} vs {expected}"
```

Second, the route file's target, which `test_route_rendered` does not check today — it
asserts the `Host()` rule and nothing about where the traffic goes:

```python
        doc = yaml.safe_load(route.read_text())
        server = doc["http"]["services"][f"{p.name}-{r['domain']}"]["loadBalancer"]["servers"][0]
        # The two halves of a working route, in one place: the name Traefik matches on and
        # the port it forwards to. The port is derived, so this is also the only static
        # check that the role's derivation and the suite's agree -- they are one function
        # (conftest imports the filter plugin), and this is where that is proven end to end.
        assert server["url"].endswith(f"127.0.0.1:{derive_publish_port(p.name, r['domain'])}"), server
```

and traefik's flag, on the rendered unit:

```python
@pytest.mark.parametrize("host", host_names())
def test_traefik_dashboard_entrypoint_is_derived(rendered, host):
    unit = (rendered / host / "traefik" / "traefik.container").read_text()
    port = derive_publish_port("traefik", load_yaml(HOSTS / host / "host.yml").get("ingress", {}).get("domain", "traefik"))
    assert f"--entrypoints.traefik.address=127.0.0.1:{port}" in unit, unit
    assert f"http://127.0.0.1:{port}/ping" in unit, unit
```

with `derive_publish_port`, `publish_ports` and `host_names` added to the `from conftest import` block.

- [ ] **Step 14: the refusal, run for real**

Append to `tests/static/test_ports.py`:

```python
def test_a_declared_port_is_refused_by_the_role(tmp_path):
    """The refusal on the host, not just in this suite: a copy of a fixture host whose
    jellyfin still says `port:` must fail the converge naming the key and the service.

    A copy and not an edit in place, so a failing test cannot leave the fixture tree
    changed. `symlinks=False` resolves test-ci's links into real folders, which is what
    makes the copy standalone outside the tree they point into. This is the case that
    covers a service living in its own repository -- the static suite cannot read a spec
    it has no local tree for, and the role is the only thing that sees it.
    """
    host = "test-ci"
    dest = tmp_path / "hosts" / host
    shutil.copytree(HOSTS / host, dest, symlinks=False)
    spec = dest / "services" / "yuzukam" / "service.yml"
    doc = yaml.safe_load(spec.read_text())
    doc["port"] = doc.pop("container_port")
    spec.write_text(yaml.safe_dump(doc))
    proc = run_playbook(
        "-e", f"host_dir={dest}",
        "-e", f"render_output={tmp_path / 'out'}",
        "-e", f"tcp_bind_address={RENDER_TCP_BIND_ADDRESS}",
        "-e", f"sources_root={FIXTURE_SOURCES}",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "declares `port`" in out and "yuzukam" in out, out
```

with `import shutil`, `import yaml` and `FIXTURE_SOURCES, HOSTS, RENDER_TCP_BIND_ADDRESS, run_playbook`
added to the imports. (`run_playbook` already carries `render_only` and `ansible_become`;
`tests/static/conftest.py:30-56`.)

- [ ] **Step 15: run the static suite**

```bash
cd /home/jlk/Projects/cradle && mise run test-static
```

Expected: all PASS. The five likely first failures and what each means:

- `No filter named 'cradle_publish_port'` — the plugin is not at `ansible/filter_plugins/`, or its `FilterModule.filters()` does not return both names.
- `test_published_ports_are_exactly_the_derived_route_ports` off by one number on exactly one service — a `container_port` carried over from the _route_ port instead of the pod's second number; Step 7's five-service list is where to look.
- `test_route_rendered`'s `url` assertion failing for every service — the route template still reads `route.port`.
- `test_ports_are_unique_per_host` failing — a label was renamed or a spec mistyped: the derivation was computed collision-free for exactly the labels in Step 7's table.
- `test_no_declared_tcp_port_is_inside_the_derivation_range` failing — `PORT_BASE` is 20000 again, or a fixture's `tcp_ports` was edited. The four declared TCP numbers in this repository are 21, 2121, 7777 and 21100-21109, all above 19999, and none of them is touched by this stage.

- [ ] **Step 16: Commit**

```bash
cd /home/jlk/Projects/cradle && mise run format && git add -A && git commit -q -m "feat!: Cradle derives the loopback port a route is published on

A service declares \`container_port\`, the port its own process listens on,
and nothing about the host's loopback. Cradle derives that from
crc32(\"<name>/<domain>\") into 10000-19999 -- stateless, identical on every
host, and refused rather than resolved when two routes or a route and a
TCP port land on one number. \`port\` is refused in service.yml, in
routes[] and by the role, so a folder written for the old contract fails
loudly instead of publishing a port its route file does not carry.
Templates read publish_port / container_port; service.port and route.port
are gone. One function, in ansible/filter_plugins/cradle_ports.py, which
the static suite imports rather than restating.
Design: docs/superpowers/specs/2026-10-19-cradle-ports-design.md.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The verifiers, and Molecule on test-ci

**Files (Cradle):**

- Modify: `tests/integration/molecule/test-ci/tests/test_service.py:243-248` (`routes_of`), plus the derivation import
- Modify: `tests/integration/molecule/test-ci/tests/test_traefik.py` (one new case, reusing `listener_addresses`)
- Modify: `tests/integration/molecule/test-ci/tests/test_deploy.py:698-701` (the mutation that must still change traefik's unit)
- No change in intent: `test_ftp.py` (TCP, declared, unchanged), `test_kopia.py`, `prepare.yml` (no secret and no placement changes, so **no `destroy` is forced by this task**)

**Interfaces:**

- Consumes: Task 1.
- Produces: the verifier helpers `derive_publish_port` and `publish_ports`; a case that proves each derived port is bound on loopback by the service's own user.

- [ ] **Step 1: the verifiers import the same function**

In `tests/integration/molecule/test-ci/tests/test_service.py`, below `BUILTINS`:

```python
# The derivation, imported from the engine under test rather than restated. A verifier
# that recomputed the arithmetic would pass against its own copy of it, which is the one
# thing this check must not be able to do.
_ports = importlib.util.spec_from_file_location(
    "cradle_ports", "/repo/ansible/filter_plugins/cradle_ports.py"
)
cradle_ports = importlib.util.module_from_spec(_ports)
_ports.loader.exec_module(cradle_ports)
derive_publish_port = cradle_ports.cradle_publish_port
```

with `import importlib.util` at the top, and `routes_of` becomes:

```python
def routes_of(spec: dict) -> list[dict]:
    """Every route a service declares: the `routes` list, or the domain+container_port shorthand."""
    if "routes" in spec:
        return spec["routes"]
    return [{"domain": spec["domain"], "container_port": spec["container_port"]}] if "domain" in spec else []


def publish_ports(spec: dict) -> list[int]:
    """Every loopback port this service's routes are published on, derived."""
    return [derive_publish_port(spec["name"], r["domain"]) for r in routes_of(spec)]
```

`test_domain_answers_over_https` needs no edit: it dials Traefik on 443 with a `Host:`
header and never names a loopback port — which is the point, and why the route probes
survive this stage untouched.

- [ ] **Step 2: each derived port is really bound, by the right user**

First the placement helper, in `test_service.py` beside `placed_tcp_entries` and built the
same way — `owner in (BUILTIN, hostname)` is this file's one idiom for "placed here", and
the kopia server's extra condition is the one `placed_builtin` applies:

```python
def placed_publish_ports(host) -> list[tuple[str, str, int]]:
    """(service, route domain, derived loopback port) for every route placed on this VM."""
    hostname = host_name(host)
    hostvars = load_spec(HOSTS / hostname / "host.yml")
    found = []
    for owner, spec_path in SPECS:
        if owner not in (BUILTIN, hostname):
            continue
        spec = load_spec(spec_path)
        # The one built-in that is not placed everywhere: no server on a host that backs
        # nothing up, the same condition `placed_builtin` applies.
        if spec["name"] == "kopia" and hostvars["backup"] == "none":
            continue
        for route, port in zip(routes_of(spec), publish_ports(spec)):
            found.append((spec["name"], route["domain"], port))
    return sorted(found)
```

Then, in `tests/integration/molecule/test-ci/tests/test_traefik.py`, after
`test_traefik_is_the_only_routable_listener_on_every_placed_tcp_port`:

```python
def test_every_derived_route_port_is_bound_on_loopback_by_its_service(host):
    """The derivation, observed on the machine rather than computed twice.

    A route file pointing at `127.0.0.1:<derived>` and a unit publishing a different
    number are both green in a render and in a static test; the only place the two meet is
    a listening socket. So: for every route placed on this VM, something listens on its
    derived port, on loopback and not on a wildcard, and the socket belongs to that
    service's own user.

    `ss -H -lntpe` and not `-lntp`: the process name is `rootlessport` or `pasta` for
    every rootless publish, so the *name* says nothing about which service it is -- `-e`
    adds `uid:<n>`, which does. Traefik is the exception and skipped: `Network=host`, so
    its dashboard socket is Traefik's own and its port is checked by the route probe and
    by the rendered-unit test instead.
    """
    listeners = {local: line for local, line in listener_addresses(host)}
    entries = [(n, d, p) for n, d, p in placed_publish_ports(host) if n != "traefik"]
    assert entries, "no route placed on this host -- has the placement changed?"
    for name, domain, port in entries:
        line = listeners.get(f"127.0.0.1:{port}")
        assert line, (
            f"{name} route {domain} derives {port} and nothing listens on "
            f"127.0.0.1:{port}:\n" + "\n".join(sorted(listeners))
        )
        for wildcard in WILDCARDS:
            assert f"{wildcard}:{port}" not in listeners, f"{port} is also bound on {wildcard}"
        uid = host.user(f"svc-{name}").uid
        assert f"uid:{uid}" in line, f"{name}: 127.0.0.1:{port} is not held by svc-{name}: {line}"
```

`listener_addresses` is reused as it stands but needs the uid column, so its
`ss -H -lntp` becomes `ss -H -lntpe` and its docstring gains one sentence: the `-e`
columns carry `uid:<n>`, which is the only thing in the line that identifies the owner of
a rootless published port. The existing TCP case is unaffected — it matches on the address
column and on `"traefik" in line`, both still present.

`test_traefik.py:12` imports `HOSTS, load_spec, placed_tcp_entries` from `test_service`
today; `placed_publish_ports` joins that line.

- [ ] **Step 3: the deploy verifier gets a lever that still exists**

`test_deploy.py:698-701` mutates Cradle's seeded tree with
`sed -i 's|^port: .*|port: 8082|' ansible/builtins/traefik/service.yml`. There is no
`^port:` line after Task 1, so the `sed` is a no-op and `git commit -qam` fails on an
empty tree — the case would go red for the wrong reason. `container_port` is no lever
either: traefik's unit does not read it (header decision 5). So the mutation moves to the
template, which is what has to change for the unit to change:

```python
    r = host.run(
        f"cd {SEEDED_CRADLE} && sed -i '/^HealthOnFailure=kill/i Environment=CRADLE_RESTART_PROBE=1' "
        "ansible/builtins/traefik/quadlet/traefik.container.j2 "
        "&& git -c user.name=t -c user.email=t@t commit -qam 'change traefik unit' && git push -q origin stable"
    )
```

and the docstring's "change traefik port" wording becomes "change traefik's unit". An
`Environment=` Traefik does not know is ignored by Traefik and changes exactly one
rendered unit, which is the whole of what the case needs: traefik restarts, nothing else
does, and the dashboard still answers 200. Verify the `sed` lands before relying on it:

```bash
cd /home/jlk/Projects/cradle && rg -n "^HealthOnFailure=kill" ansible/builtins/traefik/quadlet/traefik.container.j2
```

Expected: exactly one match.

- [ ] **Step 4: the static suite, then Molecule on test-ci, step by step**

```bash
cd /home/jlk/Projects/cradle && mise run test-static
```

Expected: PASS (`test_lint.py` and the Molecule-playbook parse test cover the verifiers'
syntax only; the verifiers themselves run on the VM).

Then, checking the VM preconditions before each step. `prepare.yml` is unchanged by this
task, so one `destroy` at the start is enough:

```bash
free -g | sed -n 2p
virsh -c qemu:///system list --all
cd /home/jlk/Projects/cradle
MOLECULE_HOST=test-ci mise run molecule destroy
MOLECULE_HOST=test-ci mise run molecule create
MOLECULE_HOST=test-ci mise run molecule prepare
MOLECULE_HOST=test-ci mise run molecule converge
MOLECULE_HOST=test-ci mise run molecule idempotence
MOLECULE_HOST=test-ci mise run molecule verify
```

Expected: `converge` writes route files pointing at the derived ports and pods publishing
them; `idempotence` reports `changed=0`; `verify` green, including the new loopback case
and the rewritten deploy case. The two reads worth having on a failure:

```bash
cd /home/jlk/Projects/cradle
MOLECULE_HOST=test-ci mise run molecule-exec 'ss -H -lntpe | grep 127.0.0.1:2'
MOLECULE_HOST=test-ci mise run molecule-exec 'cat /etc/cradle/traefik/dynamic.d/paperless-paperless.klees.io.yml'
```

On test-ci the expected derived ports are `traefik 12195`, `kopia 16915`,
`paperless 13177`, `yuzukam 13721`, `ext-echo 13437` — and the FTP ports `21`, `2121` and
`21100–21109` unchanged beside them. On a red task read the Molecule output top-down and
fix the first one.

Finally the whole scenario in one run, which is what CI does:

```bash
MOLECULE_HOST=test-ci mise run test-integration
```

Expected: `destroy → create → prepare → converge → idempotence → verify → destroy` green, ~30 minutes.

- [ ] **Step 5: Commit**

```bash
cd /home/jlk/Projects/cradle && mise run format && git add -A && git commit -q -m "test: the verifiers check the derived ports on the machine

The route probes needed nothing: they dial Traefik on 443 with a Host
header and never name a loopback port, which is the point. What is new is
the socket check -- for every placed route, something listens on its
derived port, on loopback, and the socket's uid is that service's own, read
from \`ss -lntpe\` because a rootless publish is held by rootlessport and
its name says nothing. The deploy case's lever moves from traefik's
retired \`port:\` key to its unit template.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The docs, the rollback branch and the spec copy

**Files (Cradle):**

- Modify: `ansible/roles/service/README.md` (the template-variable table at `:13-27`, the routes section at `:122-123`, `:159`, the ports rules at `:274-285`)
- Modify: `README.md:57,60,83,202-203`, `CLAUDE.md:31,34,47,48,60,180,182`
- Modify: `docs/ownership.md` (§ 3 platform table, § 4 service table, § 3's pointer paragraph at `:48`), `docs/architecture.md:157,161`, `docs/quickstart.md` (wherever a `port:` appears), `docs/external-service.md:17`
- Modify: `ansible/builtins/traefik/README.md`, `ansible/builtins/kopia/README.md`, `.claude/skills/adding-a-service/SKILL.md`
- Copy: `docs/superpowers/specs/2026-10-19-cradle-ports-design.md` and this plan from StorageBaby
- Push: a branch `pre-ports` at the pre-stage-3 master SHA (no commit, no merge)

**Interfaces:**

- Produces: `origin/pre-ports` in Cradle, the rollback pin the runbook names; every doc describing `container_port` and the derivation.

- [ ] **Step 1: the role README is the reference for the derivation**

The template-variable table at `:13-27`: the `routes` row becomes

```
| `routes`                             | list of `{domain, container_port, fqdn, publish_port}`, one per declared route; empty when the service has none                       |
```

and three rows are added after `fqdn`:

```
| `publish_port`                       | the first route's derived loopback port — what Traefik dials; empty when the service has no route                                      |
| `container_port`                     | the first route's `container_port` as declared — what the process inside listens on                                                   |
```

plus one sentence under the table, because it is the kind of thing a first consumer would
otherwise learn from a red converge: **`publish_port` and `container_port` are strings**
(a `set_fact` through `{{ }}` yields one), while `routes[n].publish_port` and
`routes[n].container_port` are integers (they are built by `combine` and never pass through
a Jinja string). Both render identically; only a comparison tells them apart.

Then a new section **Ports**, placed above "TCP ports", five short paragraphs and no more:

- **What a service declares.** `container_port`, required beside a `domain`, per route in `routes[]`. Nothing about the host's loopback.
- **What Cradle derives.** `publish_port = 10000 + (crc32("<name>/<domain>") mod 10000)`, `<domain>` being the route's declared string, label or full hostname — not the resolved fqdn, so one folder on two hosts publishes one route on one port. `ansible/filter_plugins/cradle_ports.py` is the function and the only copy of it; `tests/static/test_ports.py` pins it with three known vectors (`jellyfin/jellyfin` → 13465, `nextcloud/nextcloud.klees.io` → 18643, `traefik/traefik` → 12195).
- **What a template writes.** `PublishPort=127.0.0.1:{{ publish_port }}:{{ container_port }}`, or one such line per route from `routes[n]`. A literal host-side port is allowed only for a `tcp_ports` target.
- **The function and the range are Cradle's interface.** Changing either rewrites every route file and restarts every service on every host. That is why they are pinned by a test and named here.
- **A collision is refused, naming both sides.** Two routes on a host, or a route and a declared TCP port or target. The remedy is to rename the route's label, which is the service's own fact — there is no next-free-port, because a port depending on what else is placed would not be deterministic. **A declared TCP port inside 10000–19999 is refused too**, whether or not it collides with anything today: the derived ports are nobody's choice, so a TCP port in the range is a collision that would arrive with the next route to be placed and name the wrong service. The range was picked to make that refusable — 21, 2121, 7777 and an FTP passive range at 21100–21109 are all above it.

`:122-123` ("A service declares either `routes: [{domain, port}, ...]` or the one-route
shorthand `domain:` + `port:`") takes `container_port` in both spellings and a sentence
that the loopback port is derived, with a pointer to Ports. `:159`'s comment
(`how Traefik reaches 127.0.0.1:<port>`) becomes `127.0.0.1:<publish_port>`. The ports
rules at `:274-285`: the first bullet's "route ports, TCP entrypoints and TCP targets as
one namespace" keeps its substance and says _derived_ route ports; the bullet at `:280`
about a pod publishing `PublishPort=127.0.0.1:<target>:<container port>` gains the route
form beside the TCP one.

- [ ] **Step 2: ownership.md**

§ 3, one new row at the end of the platform table:

```
| Which loopback port a route is published on        | Derived from the service name and the route's domain — stable, stateless, in no file; a collision is refused at converge naming both sides                                                                                                                            |
```

§ 4, the "Its domains, in full" row's note gains a clause:

```
| Its domains, in full, and the port its container listens on | The complete hostname, not a label the host completes; and `container_port`, which is the one port only the service knows. The loopback port Traefik reaches it on is the platform's, derived — § 3 |
```

§ 3's pointer paragraph at `:48` ("a port such a service claims is invisible to the static
tests, which have no local tree to read it from") keeps its point and gains the change: the
port is no longer _claimed_ at all, it is derived from the service and route name — so the
collision a pointer could cause is still invisible to CI and still surfaces at converge,
but it is now a function of two names rather than of a number somebody picked.

§ 7's drift list gains nothing and item 9 stays, as the spec says.

- [ ] **Step 3: the root docs and the built-in READMEs**

```bash
cd /home/jlk/Projects/cradle
rg -n "service\.port|route\.port|\bport: [0-9]|loopback port|Route → loopback" README.md CLAUDE.md docs/ ansible/builtins/*/README.md .claude -g '!docs/superpowers'
```

and fix each: `README.md:57` (the pod publishes on loopback — on the derived port),
`README.md:60`'s table column "Route → loopback port" loses its literal numbers and
becomes "Route" (the number is derived and belongs in no table that can go stale),
`README.md:83` (the sentence explaining a route port differing from the app's port is now
simply what `container_port` is for), `README.md:202-203` ("every other service publishes
on `127.0.0.1:<port>`" → the derived port). `CLAUDE.md:31,34` (the service-folder sketch
and the template-variable list: `container_port` and `publish_port`), `:47` (`port` is
optional → `container_port` is required beside a `domain` and there is no loopback-port
setting), `:48` (`routes` is `[{domain, container_port}, ...]`), `:60` (every
`PublishPort=` publishes a _derived_ route port), `:180,182` (the port a service binds is
Cradle's, derived; the route file points at it).

`ansible/builtins/kopia/README.md`: wherever `51515` or the server's loopback port appears,
say `container_port: 51515` and that the port Traefik dials is derived. `ansible/builtins/traefik/README.md`:
the dashboard's own port is derived from `traefik/<ingress.domain>` and the unit's
entrypoint flag reads `publish_port`; the declared `container_port` is unread because
`Network=host` collapses the two.

`docs/external-service.md:17` (the template variables a pointer's folder may read:
`publish_port`, `container_port`, `fqdn`, `tz`, …) and one added sentence: an external
service declares `container_port` and never a loopback port, so two repositories can no
longer collide by both picking a number — only by deriving into one, which the host
refuses at converge naming both.

`docs/architecture.md:157,161`: the "two spellings for one route" row's `domain`+`port`
becomes `domain`+`container_port`, and the key-frequency line's `port` becomes
`container_port`.

`.claude/skills/adding-a-service/SKILL.md`: step 2 at `:80-91` is **"Pick a free port"**,
with two `rg '^port:'` commands and a paragraph about a pointer whose port the suite cannot
see. The whole step is deleted and replaced by two sentences — the author declares
`container_port`, the port its own process listens on, and Cradle derives the loopback one,
so there is nothing to look up and nothing to reserve; a collision is the host's to refuse
at converge, and the remedy would be the route's label. (The pointer paragraph's point
survives in `docs/ownership.md` § 3 and does not need repeating here.) `:167-168`'s test
table: the second row's claim becomes "a pod publishes one templated line per route and a
literal only for a `tcp_ports` target", naming
`test_pod_publishes_its_routes_through_the_derived_port`, and a row is added for
`test_published_ports_are_exactly_the_derived_route_ports` in `test_render.py`. `:64`'s
table and `:29` are unchanged.

- [ ] **Step 4: the spec and this plan**

```bash
cp /home/jlk/Projects/StorageBaby/docs/superpowers/specs/2026-10-19-cradle-ports-design.md /home/jlk/Projects/cradle/docs/superpowers/specs/
cp /home/jlk/Projects/StorageBaby/docs/superpowers/plans/2026-10-19-cradle-ports.md /home/jlk/Projects/cradle/docs/superpowers/plans/
```

The StorageBaby original already carries the corrected range (10000–19999) and the
corrected plugin path (`ansible/filter_plugins/`), so the copy needs no edit — confirm
both before copying:

```bash
rg -n "10000|filter_plugins|plugins/filter|20000" /home/jlk/Projects/cradle/docs/superpowers/specs/2026-10-19-cradle-ports-design.md
```

Expected: the derivation reads `10000 + (crc32(...) mod 10000)`, the plugin path is
`ansible/filter_plugins/`, and `20000` appears nowhere. If it does, the wrong file was
copied.

- [ ] **Step 5: check and commit**

```bash
cd /home/jlk/Projects/cradle && mise run format && mise run fmt-check && mise run test-static
```

Expected: PASS.

```bash
cd /home/jlk/Projects/cradle && git add -A && git commit -q -m "docs: a service declares container_port, Cradle derives the rest

The role README gains a Ports section naming the function, the range, the
three known vectors and the one thing the range does not refuse -- a TCP
port inside it, because an FTP passive range lives there. ownership.md
§ 3 gains the derived loopback port as a platform decision and § 4's
service row gains container_port. Every doc, built-in README and the
adding-a-service skill that told an author to pick a free port stops.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 6: the rollback branch**

A branch, at the SHA Step 1 of Task 1 recorded, pushed so the runbook can name it. No
commit is made and nothing is merged; this is the ref `CRADLE_REF` is pinned to if the
rollout has to be undone, and it has to exist _before_ the window rather than be cut
during it.

```bash
cd /home/jlk/Projects/cradle
B=/tmp/claude-1000/-home-jlk-Projects-StorageBaby/205176ee-280c-48c7-baa9-d3b66f2e4832/scratchpad/stage3
git push origin "$(cat "$B/CRADLE_BEFORE")":refs/heads/pre-ports
gh api repos/janlucaklees/cradle/branches/pre-ports --jq .commit.sha
cat "$B/CRADLE_BEFORE"
```

Expected: the two SHAs match, and it is `f8eb03b…` unless `master` moved since Task 1.

---

### Task 4: The full test-a cycle, then the Cradle PR

**Files:** none.

- [ ] **Step 1: both suites on the test-a placement**

```bash
free -g | sed -n 2p
virsh -c qemu:///system list --all
cd /home/jlk/Projects/cradle && mise run test-static && mise run test-integration
```

Expected: green (default `MOLECULE_HOST=test-a`, ~50 minutes). test-a places every fixture
service, so this is the run that proves 18 derived ports, two of them on one pod
(nextcloud, calino), and an FTP range sitting inside the derivation range without
colliding with anything — the shape the NAS will see.

- [ ] **Step 2: push the branch and open the PR for CI**

Nothing is merged, and `stable` must not move: CI runs on `pull_request`
(`.github/workflows/ci.yml:5-6`) and `promote` is gated on a push to `master`
(`:142-143`), so a PR from `ports` gets both suites and cannot promote.

```bash
cd /home/jlk/Projects/cradle
git push -u origin ports
gh pr create --base master --head ports \
	--title "Derived loopback ports" --body "$(
		cat << 'EOF'
Stage 3 of the Cradle split: `docs/superpowers/specs/2026-10-19-cradle-ports-design.md`.
A service stops choosing the loopback port Traefik reaches it on.

- `container_port` replaces `port`, in `service.yml` and in `routes[]`. `port` is refused
  by the static suite **and** by the role, so a folder written for the old contract fails
  loudly instead of publishing a port its route file does not carry.
- `publish_port = 10000 + (crc32("<name>/<domain>") mod 10000)`, in
  `ansible/filter_plugins/cradle_ports.py` — one function, imported by the static suite
  and by the verifiers rather than restated, pinned by three known vectors.
- Templates read `publish_port` / `container_port` (and `routes[n].*`). `service.port`
  and `route.port` are gone and a template naming one fails a named test.
- A collision — two routes, or a route and a declared TCP port or target — is refused at
  converge naming both sides. Not resolved: a port that depended on what else was placed
  would not be deterministic.
- A declared `tcp_ports` port, target or range entry **inside** 10000–19999 is refused as
  well, by the role and by a static test — not only when it equals a derived port today.
  The range was chosen to make that possible: 21, 2121, 7777 and the FTP passive range
  21100–21109 are all above 19999.
- Spec § 4's `ansible/plugins/filter/` is **not** where the plugin went:
  `ansible/ansible.cfg` is never read (no entry point has its working directory inside
  `ansible/`), and `<playbook_dir>/filter_plugins/` is the one path Ansible auto-loads in
  all three modes — the static suite, the deploy script and Molecule's `import_playbook`.

**Do not merge before the host PRs are ready.** Merging this moves `stable` and a host
whose folders still say `port:` refuses every converge until its own PR lands. The window
is `docs/runbooks/2026-10-20-cradle-ports-rollout.md` in the StorageBaby PR.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
	)"
gh run watch --exit-status
gh api repos/janlucaklees/cradle/branches/stable --jq .commit.sha
cat /tmp/claude-1000/-home-jlk-Projects-StorageBaby/205176ee-280c-48c7-baa9-d3b66f2e4832/scratchpad/stage3/CRADLE_BEFORE
```

Expected: `static` and `integration` green on the runner, no `promote` job, and the two
SHAs **equal** — `stable` has not moved, which is the precondition the rollout rests on.
A runner-only failure (KVM, timeout) is read in the job log, fixed, committed and pushed
again. Report the PR URL.

---

### Task 5: StorageBaby's folders declare `container_port`

**Files (in the `cradle-ports` worktree):**

- Modify: 13 `hosts/StorageBaby/services/*/service.yml`
- Modify: 13 `hosts/StorageBaby/services/*/quadlet/*.j2` publish lines
- Modify: `README.md:57,60,83,202-203,208-210`, `CLAUDE.md:31,34,47,48,60,180,182,186`, and four service READMEs — `nextcloud/README.md:54`, `openarchiver/README.md:39`, `hermes/README.md:23`, plus whatever the Step 4 grep finds
- Create: `docs/runbooks/2026-10-20-cradle-ports-rollout.md` (draft)
- Untouched: every `*.sops.yaml`, `host.yml`, `docs/superpowers/`, the other runbooks, and the main checkout

**Interfaces:**

- Consumes: Cradle branch `ports` (for `mise run render`), not `stable`.
- Produces: a PR, open and unmerged, whose body names the Cradle PR and the renaissance diff as merge preconditions.

- [ ] **Step 1: the worktree**

Use `superpowers:using-git-worktrees` to create `.claude/worktrees/cradle-ports` on branch
`cradle-ports` from `origin/master`.

- [ ] **Step 2: the 13 specs**

`port:` → `container_port:`, with the value changing wherever the old number was a mapping
rather than the container's own port — the pod's second number is the truth, and it is
also the port the derived table below was computed against:

| spec                          | was                         | becomes                                         | route → derived                                            |
| ----------------------------- | --------------------------- | ----------------------------------------------- | ---------------------------------------------------------- |
| `jellyfin/service.yml:2`      | `port: 8096`                | `container_port: 8096`                          | `jellyfin` → 13465                                         |
| `immich/service.yml:2`        | `port: 2283`                | `container_port: 2283`                          | `immich.klees.io` → 14550                                  |
| `yuzukam/service.yml:2`       | `port: 3000`                | `container_port: 3000`                          | `yuzukam` → 13721                                          |
| `stirling-pdf/service.yml:2`  | `port: 8180`                | `container_port: 8080`                          | `stirling.klees.io` → 17609                                |
| `mealie/service.yml:2`        | `port: 9000`                | `container_port: 9000`                          | `mealie.klees.io` → 11478                                  |
| `mcp/service.yml:2`           | `port: 3002`                | `container_port: 3002`                          | `mcp` → 15171                                              |
| `hermes/service.yml:5`        | `port: 9119`                | `container_port: 9119`                          | `hermes` → 13500                                           |
| `openarchiver/service.yml:5`  | `port: 3001`                | `container_port: 3000`                          | `openarchiver` → 13910                                     |
| `openproject/service.yml:2`   | `port: 8081`                | `container_port: 8080`                          | `openproject.klees.io` → 12726                             |
| `mailflow/service.yml:2`      | `port: 3003`                | `container_port: 80`                            | `mailflow.klees.io` → 16109                                |
| `paperless/service.yml:2`     | `port: 8000`                | `container_port: 8000`                          | `paperless.klees.io` → 13177                               |
| `calino/service.yml:13,22`    | `port: 8085` / `port: 8086` | `container_port: 8085` / `container_port: 8086` | `calino.klees.io` → 11773, `dav.klees.io` → 19778          |
| `nextcloud/service.yml:10,13` | `port: 8280` / `port: 9980` | `container_port: 80` / `container_port: 9980`   | `nextcloud.klees.io` → 18643, `collabora.klees.io` → 15130 |

Plus the two built-ins, which live in Cradle and change nothing here: `traefik/traefik` →
12195, `kopia/kopia` → 16915. And renaissance, whose folder is its own repository's:
`renaissance/renaissance.klees.io` → 16100.

**18 routes, 18 distinct ports, none equal to 21, 2121 or any of 21100–21109, all inside
10000–19999.** No route label is renamed.

- [ ] **Step 3: the 13 templates**

Three single-container units read `{{ service.port }}` and become
`PublishPort=127.0.0.1:{{ publish_port }}:{{ container_port }}` —
`jellyfin/quadlet/jellyfin.container.j2:8`, `yuzukam/quadlet/yuzukam.container.j2:8`,
`stirling-pdf/quadlet/stirling-pdf.container.j2:8`.

Nine pods carry literals, and their "Literal, not `{{ service.port }}`" comment becomes
the reason it can no longer be one (the wording from Task 1 Step 8):

| pod template                               | line(s) | becomes                                                   |
| ------------------------------------------ | ------- | --------------------------------------------------------- |
| `immich/quadlet/immich.pod.j2`             | 3, 5    | `{{ publish_port }}:{{ container_port }}`                 |
| `mealie/quadlet/mealie.pod.j2`             | 3, 6    | the same                                                  |
| `mcp/quadlet/mcp.pod.j2`                   | 3, 10   | the same                                                  |
| `hermes/quadlet/hermes.pod.j2`             | 3, 11   | the same                                                  |
| `openarchiver/quadlet/openarchiver.pod.j2` | 3, 7    | the same                                                  |
| `mailflow/quadlet/mailflow.pod.j2`         | 3, 10   | the same                                                  |
| `openproject/quadlet/openproject.pod.j2`   | 3, 9    | the same                                                  |
| `calino/quadlet/calino.pod.j2`             | 10, 11  | `{{ routes[0].* }}` and `{{ routes[1].* }}`               |
| `nextcloud/quadlet/nextcloud.pod.j2`       | 7, 8    | the same two-route form                                   |
| `paperless/quadlet/paperless.pod.j2`       | 3, 7    | line 7 templated; **9 and 14 stay literal** (TCP targets) |

- [ ] **Step 4: the docs in this repository**

```bash
cd /home/jlk/Projects/StorageBaby/.claude/worktrees/cradle-ports
rg -n "service\.port|\bport: [0-9]|loopback port|Route → loopback|PublishPort=127" README.md CLAUDE.md hosts/StorageBaby/services/*/README.md
```

and fix each. The ones already located: `CLAUDE.md:31,34` (the folder sketch and the
template-variable list), `:47` (`port` is optional → `container_port` is required beside a
`domain`, and there is no loopback-port setting at all), `:48` (`routes` is
`[{domain, container_port}, ...]`), `:60` (every `PublishPort=` publishes a _derived_ route
port), `:180` ("the port declared in its `service.yml`" → derived from the service and
route name), `:182` (the route file points at the derived port), `:186` (plain TCP stays
declared — the one sentence worth keeping as it is, plus a clause saying so).
`README.md:57,60,83,202-203` as in Task 3 Step 3, and `README.md:208-210` keeps its FTP
paragraph and gains that the passive range sits inside the derivation range and is not
refused — only an exact collision is. `nextcloud/README.md:54` (the `8280`/`80` paragraph
becomes `container_port: 80` and a derived loopback port), `openarchiver/README.md:39`
(the 3001/3000 explanation is now what `container_port` means), `hermes/README.md:23`
(`PublishPort` on the pod — the templated form).

One line is added to CLAUDE.md's "Managing services", where the keys are listed: the
loopback port is Cradle's, derived from `<name>/<route domain>` into 10000–19999, in no
file, and a collision is refused at converge naming both services.

- [ ] **Step 5: the render diff against the baseline**

Rendered with the Cradle **branch** checkout, which is where the engine is: `stable` must
not have moved.

```bash
B=/tmp/claude-1000/-home-jlk-Projects-StorageBaby/205176ee-280c-48c7-baa9-d3b66f2e4832/scratchpad/stage3
cd /home/jlk/Projects/cradle && git branch --show-current
mise run render /home/jlk/Projects/StorageBaby/.claude/worktrees/cradle-ports hosts/StorageBaby "$B/after"
diff -r "$B/before" "$B/after"
```

Expected — `ports`, then **only** differences in port numbers, of exactly four kinds:

1. **Every unit's publish lines.** One per route, `127.0.0.1:<old>:<container>` →
   `127.0.0.1:<derived>:<container>`, the second number unchanged in every case — the five
   services where it differed from the first (stirling-pdf, openarchiver, openproject,
   mailflow, nextcloud's web route) are exactly the five whose `container_port` is not the
   old `port`. Paperless's `2121` and `21100-21109` lines are **unchanged**.
2. **Every route file's `url:`.** `http://127.0.0.1:<old>` → `…:<derived>`, and
   `https://` for kopia's and collabora's, whose `scheme` is unchanged.
3. **Traefik's unit**, two lines: `--entrypoints.traefik.address=127.0.0.1:8080` → `:12195`
   and the health check's `http://127.0.0.1:8080/ping` → `:12195`. No publish line, because
   `Network=host`.
4. **Kopia's unit**, one line: `PublishPort=127.0.0.1:51515:51515` → `127.0.0.1:16915:51515`.

Nothing else. No secret, no volume path, no bind, no `AddHost=`, no timer, no storage file,
no sidecar — `backup_fqdn` is a name and not a port, and `renaissance` is absent from both
sides because its repository is not fetched. Any other difference is a behaviour change
stage 3 did not intend: read it, fix the cause in Cradle on `ports`, push (the PR's CI
re-runs), and re-run this step.

Then the number that must appear, per service, checked rather than eyeballed:

```bash
rg -o "PublishPort=127\.0\.0\.1:[0-9]+" "$B/after" | sed 's/.*://' | sort -un | paste -sd' '
rg -o "127\.0\.0\.1:[0-9]+" "$B/after/traefik-dynamic.d" | sed 's/.*://' | sort -un | paste -sd' '
```

`sort -un`, numerically: lexicographic order would put `2121` between `19778` and `21100`
and make the two lists below unreadable.

The first must be exactly these **18** numbers (`-o` with `[0-9]+` stops at the range's
dash, so paperless's `21100-21109` prints as `21100`; traefik contributes nothing because
it publishes nothing, and renaissance is absent from the render):

```
2121 11478 11773 12726 13177 13465 13500 13721 13910 14550 15130 15171 16109 16915 17609 18643 19778 21100
```

The second must be exactly these **27** — the 16 derived route ports that get a route file
with a `url:` on this host, plus paperless's `2121` and its ten passive ports, each of which
is a TCP service's own `127.0.0.1:<target>` line. Traefik contributes nothing here either:
its dashboard router points at `api@internal` and so its file carries no `url:`, which is
why 12195 is absent:

```
2121 11478 11773 12726 13177 13465 13500 13721 13910 14550 15130 15171 16109 16915 17609 18643 19778 21100 21101 21102 21103 21104 21105 21106 21107 21108 21109
```

Any number not in the list, or any of these missing, is the diff to read before the PR is
opened. Mechanical, so it cannot be eyeballed wrong at 02:00.

- [ ] **Step 6: the renaissance diff, computed and written down, not applied**

renaissance is the operator's repository and this plan does not touch it. The diff is
computed here so the runbook can carry it verbatim:

```bash
cd /tmp
gh api "repos/janlucaklees/renaissance/contents/deploy/storagebaby/service.yml?ref=stable" -H "Accept: application/vnd.github.raw" | sed -n '1,12p'
gh api "repos/janlucaklees/renaissance/contents/deploy/storagebaby/quadlet/renaissance.pod.j2?ref=stable" -H "Accept: application/vnd.github.raw" | sed -n '1,10p'
```

Expected, at the SHA this plan was written against: `service.yml:7-11` is

```yaml
# Free on the host's loopback when this was written; the host refuses a converge in
# which two services claim one port.
port: 8300
```

and `quadlet/renaissance.pod.j2:7` is `PublishPort=127.0.0.1:8300:80`. If either differs,
re-read the whole folder and recompute the diff before writing it down.

The change, for the runbook — two files, three lines, nothing else in that folder:

```diff
--- a/deploy/storagebaby/service.yml
+++ b/deploy/storagebaby/service.yml
@@
-# Free on the host's loopback when this was written; the host refuses a converge in
-# which two services claim one port.
-port: 8300
+# The port nginx listens on inside the pod. The loopback port Traefik reaches it on is
+# Cradle's and is derived from `renaissance/renaissance.klees.io` -- 16100 today, and in
+# no file. A collision with another service is refused at converge, naming both.
+container_port: 80
--- a/deploy/storagebaby/quadlet/renaissance.pod.j2
+++ b/deploy/storagebaby/quadlet/renaissance.pod.j2
@@
-PublishPort=127.0.0.1:8300:80
+PublishPort=127.0.0.1:{{ publish_port }}:{{ container_port }}
```

`container_port: 80` and not `8300`: the pod's second number is nginx's own port, and
`8300` was only ever the loopback side. The comment at `renaissance.pod.j2:4-6` ("One
port, nginx's, which splits the one port and knows no path rules") stays as it is; the
"Literal" half of it, if present at that SHA, becomes the Task 1 Step 8 wording.

- [ ] **Step 7: the rollout runbook, as a draft in the PR**

Create `docs/runbooks/2026-10-20-cradle-ports-rollout.md`, modelled on
`2026-10-18-cradle-built-ins-rollout.md`, marked **draft — written before the rollout,
filled in during it**, with these sections:

- **Preconditions.** Cradle PR open and green, `stable` still at the pre-stage-3 SHA (the
  value of `$B/CRADLE_BEFORE`, quoted in full), `origin/pre-ports` in Cradle at that same
  SHA. This repository's PR open. The renaissance diff below reviewed. The time is outside
  01:30–04:00 (maintenance at 02:00) and `systemctl --user -M svc-kopia@ list-timers`
  shows the hourly replica push not running. The operator is present. No Molecule run on
  the workstation. **Nothing in this stage touches a secret, a volume or the pool**, so
  there is no data precondition at all — which is what makes it a restart window and not
  a migration.
- **Stop the timer, first.** `systemctl stop cradle-deploy.timer` and
  `systemctl is-active cradle-deploy.timer` → `inactive`, **before** any merge. Three
  repositories break together here: a tick between the Cradle merge and the host merge
  converges a host whose folders still say `port:` and fails, every five minutes, until
  the second merge lands.
- **The three merges, in order, each waited out.** Merge the Cradle PR → `gh run watch`
  on the master push → `gh api repos/janlucaklees/cradle/branches/stable --jq .commit.sha`
  equals the merge commit. Merge this repository's PR → the same wait on its own CI.
  Apply the renaissance diff (§ "The renaissance change" below, verbatim), let its CI
  promote its `stable`, and confirm `gh api repos/janlucaklees/renaissance/branches/stable --jq .commit.sha`
  moved. Only then the pre-flight.
- **The pre-flight.** `cradle-deploy --check 2>&1 | tail -200`. Expected, and only this:
  every service's unit reported as changing, with the diff being its publish line's host
  port (traefik's two flag lines instead, kopia's one publish line); every
  `traefik-dynamic.d/<name>-<domain>.yml` reported as changing, with the diff being the
  `url:`'s port; `renaissance` fetched at its new `stable` and its unit changing the same
  way. **No secret sync, no volume creation, no chown, no remount, no `backup-clients`
  write, no storage task changed** — nothing in this stage writes anything but a unit and
  a route file. The derived numbers to expect are the 18 of Step 2's table. Anything
  beyond that list stops the rollout.
- **The converge.** `systemctl start cradle-deploy.service`, blocking. Order within the
  tick: traefik first (it is placed first), then kopia, then the host's own services in
  folder order; each restarts once, because its unit changed. Traefik's route files are
  rewritten in place and it watches the directory, so the only outage per service is its
  own restart. Expect the whole tick to take as long as fifteen container restarts.
- **Verification.** `cradle-svc ps traefik`, `cradle-svc ps kopia` and each of the
  fourteen: active. `ss -H -lntpe | grep 127.0.0.1` — one line per derived port of Step
  2's table, each with the `uid:` of its own `svc-<name>`, and **no line on the old ports**
  (8085, 8086, 8080, 8096, 8000, 8081, 8180, 8280, 9980, 9119, 9000, 3000, 3001, 3002,
  3003, 2283, 8300, 51515 as _host_-side ports; several of them are still container-side
  and inside a netns, which `ss` on the host does not show). Then every route through
  Traefik, which is the claim that matters:

  ```bash
  for n in jellyfin.home.klees.io mcp.home.klees.io hermes.home.klees.io \
  	openarchiver.home.klees.io yuzukam.home.klees.io kopia.home.klees.io \
  	traefik.home.klees.io immich.klees.io mealie.klees.io stirling.klees.io \
  	openproject.klees.io mailflow.klees.io paperless.klees.io nextcloud.klees.io \
  	collabora.klees.io calino.klees.io dav.klees.io renaissance.klees.io; do
  	printf '%-32s %s\n' "$n" "$(curl -sk -o /dev/null -w '%{http_code}' -H "Host: $n" https://127.0.0.1/)"
  done
  ```

  Expected: no `404` and no `000`. A `404` is Traefik matching no router; `000` is nothing
  answering. `dav.klees.io` and `mcp` answer on their declared `probe_path` rather than
  `/`, so check those two with `/health` and `/livez`. The FTP ports are unchanged and
  `test_ftp`'s live equivalent is one `curl --ftp-pasv ftp://<lan address>/` as the
  scanner's user. Finally the hourly `kopia-sync.service` run and one sidecar's
  `kopia repository status`, because the sidecars reach the server through Traefik and
  Traefik's port moved under them.

- **Rollback.** Pin `CRADLE_REF=pre-ports` in `/etc/cradle/deploy.conf` **and** revert both
  host-side changes — this repository's merge, and renaissance's. The three have to go back
  together for the same reason they went forward together: a `container_port` folder on a
  pre-stage-3 engine renders no publish line at all. Every service restarts a second time
  and every route file is rewritten back; nothing else is undone, because nothing else was
  done. Then start the timer only after the revert's CI has moved both `stable` branches.
- **Start the timer.** `systemctl start cradle-deploy.timer`, and
  `systemctl list-timers cradle-deploy.timer` shows a next elapse. **This is the step
  that is forgotten**, and a host with a stopped timer looks healthy for weeks.
- **The renaissance change**, Step 6's diff, verbatim, with the two `gh api` commands that
  produced it so the operator can confirm nothing moved in between.

- [ ] **Step 8: check, commit, open the PR**

```bash
cd /home/jlk/Projects/StorageBaby/.claude/worktrees/cradle-ports && mise run devtools && mise run fmt-check
```

Expected: PASS. Commit through the `commit` skill:
`refactor!: services declare container_port, Cradle derives the loopback port`, body
naming the 13 specs, the 13 templates, the four service READMEs and the runbook. Then:

```bash
cd /home/jlk/Projects/StorageBaby/.claude/worktrees/cradle-ports
git push -u origin cradle-ports
gh pr create --title "Services declare container_port; Cradle derives the loopback port" --body "$(
	cat << 'EOF'
Stage 3 of the Cradle split: `docs/superpowers/specs/2026-10-19-cradle-ports-design.md`.
Cradle derives the loopback port Traefik reaches a service on, so no folder here picks one.

- 13 `service.yml`: `port:` → `container_port:`, the port the process inside the container
  listens on. Five of them change value, because the old number was the loopback side and
  the container's own is the pod's second number: stirling-pdf 8180→8080, openarchiver
  3001→3000, openproject 8081→8080, mailflow 3003→80, nextcloud's web route 8280→80.
- 13 templates: every route's publish line becomes
  `PublishPort=127.0.0.1:{{ publish_port }}:{{ container_port }}`. Paperless's `2121` and
  `21100-21109` stay literal — `tcp_ports` are declared and unchanged.
- `README.md`, `CLAUDE.md` and four service READMEs stop describing a port anybody picks.
- `docs/runbooks/2026-10-20-cradle-ports-rollout.md` is the rollout window, draft.

18 routes on this host derive into 18 distinct ports, all inside 10000–19999, none equal
to the FTP ports 21, 2121 or 21100–21109. No route label is renamed, so no DNS record
changes.

**Three repositories merge in one window, with the deploy timer stopped**, in this order:

1. Cradle PR (`janlucaklees/cradle`, branch `ports`) — merging it moves Cradle's `stable`,
   and from that moment a host whose folders still say `port:` refuses every converge.
2. This PR.
3. renaissance: `deploy/storagebaby/service.yml` and `quadlet/renaissance.pod.j2`, the
   exact diff in the runbook's last section, promoted to its `stable`.

The runbook has the pre-flight expectations, the verification and the rollback (Cradle's
`pre-ports` branch is pushed at the pre-stage-3 SHA for the pin).

Render diff against the pre-stage-3 engine: publish lines, route-file `url:` ports,
traefik's two dashboard lines and kopia's publish line — port numbers only, nothing else.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

**Neither PR is merged by this plan.** Report both URLs and stop: merging them is the
rollout's first two steps, with the operator present.
