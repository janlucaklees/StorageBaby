# Phase 2: Single-Container Services Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring kopia (server), jellyfin, stirling-pdf, yuzukam and paperless-upload onto the Phase 1 platform under `hosts/storagebaby/services/`, all five exercised on the `test-a` VM, with the harness generalised first and the old Docker directories removed.

**Architecture:** Unchanged from Phase 1: the generic `service` role turns a service folder into a rootless Podman user with rendered Quadlet units and a Traefik route. Phase 2 adds four optional contract keys (`binds`, `devices`, `groups`, `config`) plus per-host `service_config` overrides, orders `.build` units before their containers, and generalises the integration checks over placed services. Services are placed on `test-a` by directory symlinks into `hosts/storagebaby/services/`.

**Tech Stack:** As Phase 1: Ansible, Podman 6 Quadlet, sops+age, Molecule on libvirt VMs, pytest + testinfra, Traefik v3.

**Spec:** `docs/superpowers/specs/2026-09-22-phase-2-single-services-design.md` (extends `2026-09-21-gitops-podman-platform-design.md`)

## Global Constraints

- Rootless Podman only, one `svc-<name>` user per service, no container as root on the host, no socket into a container, nothing privileged anywhere. Test hosts are libvirt VMs; never a privileged container.
- Every migrated service binds only `127.0.0.1:<port>` when it has a `domain`; Traefik on host network is the only way in. Ports unique per host.
- Placement is the folder. `hosts/test-a/services/<name>` is a relative symlink to `../../storagebaby/services/<name>`. No hostname in any role, template, test or task.
- Secrets: sops+age at rest, podman secrets at runtime; test hosts get generated secrets under `hosts/test-*/secrets/`. Never print a secret value.
- Volumes resolve from `storage_roots` classes `pool` and `fast`. `binds` are absolute host paths the service does not own; the role never changes permissions on an existing tree, only ensures the directory and its group exist and adds the service user to that group.
- `devices` render only when the host's `host.yml` has `gpu: true`. Any `groups` or `binds` group implies `GroupAdd=keep-groups`.
- Every `*.container.j2` has `HealthCmd`, `HealthOnFailure=kill`, `Restart=always` (static test enforces).
- Update policy: floating tag + `AutoUpdate=registry` for jellyfin, stirling-pdf, yuzukam; pinned tag for kopia; `.build` for paperless-upload.
- Tests are the verification: `make test-static` and `make test-integration` green after every task; each service task ends with its old directory deleted in the same commit.
- All commands via `make` targets in the devtools image. Never run docker, virsh mutations or molecule directly; read-only `virsh -c qemu:///system list --all` is fine.
- Commit per task on `podman-platform` (standing permission). Do not push.

---

### Task 1: Contract extensions in the static layer

**Files:**

- Modify: `tests/static/test_contract.py`, `tests/static/test_ports.py`, `tests/static/test_hosts.py`
- Modify: `hosts/storagebaby/host.yml`, `hosts/test-a/host.yml`
- Modify: `docs/superpowers/specs/2026-09-21-gitops-podman-platform-design.md` section 5 (link to the Phase 2 spec for the new keys)

**Interfaces:**

- Produces: `service.yml` optional keys `binds: {<name>: {host: <abs>, container: <abs>, mode: ro|rw, group: <str>}}`, `devices: [<abs>]`, `groups: [<str>]`, `config: {}`; `port` required iff `domain` set.
- Produces: `host.yml` keys `gpu: bool` (required), `packages: [str]` (required, may be empty), `service_config: {<service>: {...}}` (required, may be empty; every key must name a service placed on that host, shared included).

- [ ] **Step 1: Write the tests**

Replace `tests/static/test_contract.py`:

```python
import re

import pytest

from conftest import placements

REQUIRED = {"name", "volumes", "secrets", "backup"}
CLASSES = {"pool", "fast"}
MODES = {"ro", "rw"}
GROUP_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


@pytest.mark.parametrize("p", placements(), ids=lambda p: f"{p.host}/{p.name}")
def test_service_contract(p):
    spec = p.spec
    missing = REQUIRED - set(spec)
    assert not missing, f"{p.name}: missing keys {missing}"
    assert spec["name"] == p.dir.name, "name must equal the folder name"
    if "domain" in spec:
        assert isinstance(spec.get("port"), int), f"{p.name}: a service with a domain needs a loopback port"
    if "port" in spec:
        assert isinstance(spec["port"], int)
    for vol, cfg in spec["volumes"].items():
        assert cfg.get("class") in CLASSES, f"volume {vol} has no valid class"
    for name, b in spec.get("binds", {}).items():
        assert b["host"].startswith("/"), f"bind {name}: host path must be absolute"
        assert b["container"].startswith("/"), f"bind {name}: container path must be absolute"
        assert b.get("mode", "ro") in MODES, f"bind {name}: mode must be ro or rw"
        assert GROUP_RE.match(b["group"]), f"bind {name}: invalid group name"
    for dev in spec.get("devices", []):
        assert dev.startswith("/dev/"), f"device {dev} must be under /dev"
    for g in spec.get("groups", []):
        assert GROUP_RE.match(g), f"invalid group name {g}"
    assert isinstance(spec.get("config", {}), dict)
    assert isinstance(spec["secrets"], list)
    assert spec["backup"] == "none" or {"paths", "schedule", "retention"} <= set(spec["backup"])


def test_at_least_one_placement():
    assert placements()
```

Replace `tests/static/test_ports.py`:

```python
from collections import defaultdict

from conftest import host_names, placements


def test_loopback_ports_unique_per_host():
    all_placements = placements()
    for host in host_names():
        seen = defaultdict(list)
        for p in all_placements:
            if p.host == host and "port" in p.spec:
                seen[p.spec["port"]].append(p.name)
        dupes = {port: names for port, names in seen.items() if len(names) > 1}
        assert not dupes, f"{host}: port collisions {dupes}"
```

Replace `tests/static/test_hosts.py`:

```python
import pytest

from conftest import HOSTS, host_names, load_yaml, placements

REQUIRED = {"domain", "acme", "acme_email", "tz", "storage_roots", "volume_overrides", "deploy_timer", "gpu", "packages", "service_config"}
CLASSES = {"pool", "fast"}


@pytest.mark.parametrize("host", host_names())
def test_host_contract(host):
    cfg = load_yaml(HOSTS / host / "host.yml")
    missing = REQUIRED - set(cfg)
    assert not missing, f"{host}: missing keys {missing}"
    assert isinstance(cfg["acme"], bool)
    assert isinstance(cfg["deploy_timer"], bool)
    assert isinstance(cfg["gpu"], bool)
    assert set(cfg["storage_roots"]) == CLASSES
    assert all(isinstance(v, str) and v.startswith("/") for v in cfg["storage_roots"].values())
    assert isinstance(cfg["volume_overrides"], dict)
    assert all(isinstance(v, str) and v.startswith("/") for v in cfg["volume_overrides"].values())
    assert isinstance(cfg["packages"], list) and all(isinstance(p, str) for p in cfg["packages"])
    assert isinstance(cfg["service_config"], dict)
    placed = {p.name for p in placements() if p.host == host}
    unknown = set(cfg["service_config"]) - placed
    assert not unknown, f"{host}: service_config for services not placed here: {unknown}"
```

- [ ] **Step 2: Run to verify failure**

Run: `make test-static`
Expected: `test_host_contract` fails for both hosts (missing `gpu`, `packages`, `service_config`).

- [ ] **Step 3: Extend both host.yml files**

Append to `hosts/storagebaby/host.yml`:

```yaml
gpu: true
packages:
  [
    mesa,
    vulkan-mesa-layers,
    vulkan-radeon,
    vulkan-tools,
    vdpauinfo,
    libva-utils
  ]
service_config: {}
```

Append to `hosts/test-a/host.yml`:

```yaml
gpu: false
packages: []
service_config: {}
```

- [ ] **Step 4: Run to verify pass, then spec pointer**

Run: `make test-static`. Expected: green (23 passed). In the Phase 1 spec, section 5, add one sentence after the `service.yml` example: "Phase 2 adds the optional keys `binds`, `devices`, `groups`, `config` and the host keys `gpu`, `packages`, `service_config`; see `2026-09-22-phase-2-single-services-design.md`."

- [ ] **Step 5: Commit**

```bash
git add tests/static hosts docs/superpowers/specs/2026-09-21-gitops-podman-platform-design.md
git commit -m "Extend the service and host contracts for binds, devices, groups and per-host config"
```

---

### Task 2: Role support for the new keys, build ordering, packages, make

**Files:**

- Modify: `ansible/roles/service/tasks/main.yml`, `host.yml`, `units.yml`, `render.yml`
- Modify: `ansible/roles/host_base/tasks/main.yml`
- Modify: `bootstrap.sh` (add `make` to the pacman line; the unit heredocs stay untouched)
- Create: `tests/integration/molecule/test-ci/tests/test_binds.py` (asserts group-based read access works from a container; uses a throwaway probe only if no placed service has binds yet, see Step 1)

**Interfaces:**

- Produces template variables: `service` now includes `config` deep-merged with `service_config[service.name]` from `host.yml`; `binds` dict name → `{host, mode, group}`; `devices_enabled` (bool: `gpu` and the service has devices); `keep_groups` (bool: any `groups` or any bind group).
- Produces: unit restart order `-build.service` first, then `-pod.service`, then `.service`; a changed `.build` marks every `.container` of the service changed.
- Produces host state: groups created (`system: true`), `svc-<name>` member of each; bind directories exist (0775, group = bind group, setgid) only when absent; `make` and `host.yml` `packages` installed.

- [ ] **Step 1: Write the integration test**

`tests/integration/molecule/test-ci/tests/test_binds.py`:

```python
"""Group-based access to bind paths from inside a rootless container.

Runs for every placed service that declares `binds`: the role must have created
the directory with the declared group and added the service user to it. The
test writes a group-readable file as root and reads it back through the
service's first container. Services with binds but no running container yet
skip.
"""

import pytest

from conftest import run_as
from test_service import SPECS, placed


@pytest.mark.parametrize("spec", [s for s in SPECS if s.get("binds")], ids=lambda s: s["name"])
def test_service_user_in_bind_groups(host, spec):
    placed(host, spec)
    user = f"svc-{spec['name']}"
    groups = set(host.check_output(f"id -nG {user}").split())
    for name, b in spec["binds"].items():
        assert b["group"] in groups, f"{user} not in group {b['group']} for bind {name}"
        d = host.file(b["host"])
        assert d.is_directory and d.group == b["group"]


@pytest.mark.parametrize("spec", [s for s in SPECS if s.get("binds")], ids=lambda s: s["name"])
def test_container_can_read_group_file(host, spec):
    placed(host, spec)
    user = f"svc-{spec['name']}"
    name, b = next(iter(spec["binds"].items()))
    probe = f"{b['host']}/.storagebaby-probe"
    host.run(f"install -m 0640 -o root -g {b['group']} /dev/null {probe} && echo ok > {probe}")
    container = spec.get("container_name", spec["name"])
    r = run_as(host, user, f"podman exec {container} cat {b['container']}/.storagebaby-probe")
    host.run(f"rm -f {probe}")
    assert r.rc == 0 and r.stdout.strip() == "ok", r.stderr
```

`SPECS` and `placed` are the module-level spec list and skip helper that `test_service.py` already defines; if their names differ, import the actual names.

- [ ] **Step 2: Role: main.yml**

After the volume resolution in `ansible/roles/service/tasks/main.yml`, add:

```yaml
- name: Merge per-host service config over the service's own config
  ansible.builtin.set_fact:
    service: "{{ service | combine({'config': (service.config | default({})) | combine((service_config | default({}))[service.name] | default({}), recursive=True)}) }}"

- name: Derive bind, device and group facts
  ansible.builtin.set_fact:
    binds: '{{ service.binds | default({}) }}'
    devices_enabled: '{{ (gpu | default(false) | bool) and (service.devices | default([]) | length > 0) }}'
    extra_groups: "{{ (service.groups | default([])) + (service.binds | default({}) | dict2items | map(attribute='value.group') | list) | unique }}"
    keep_groups: "{{ ((service.groups | default([])) + (service.binds | default({}) | dict2items | map(attribute='value.group') | list)) | length > 0 }}"
```

- [ ] **Step 3: Role: host.yml**

After the `Service user` task and its uid lookup, add:

```yaml
- name: Extra host groups exist
  ansible.builtin.group:
    name: '{{ item }}'
    system: true
  loop: '{{ extra_groups }}'

- name: Service user is a member of its extra groups
  ansible.builtin.user:
    name: '{{ svc_user }}'
    groups: '{{ extra_groups }}'
    append: true
  when: extra_groups | length > 0
```

After the `Volume directories` task, add:

```yaml
- name: Bind directories exist (created only when absent; existing trees are never re-permissioned)
  ansible.builtin.stat:
    path: '{{ item.value.host }}'
  loop: '{{ binds | dict2items }}'
  loop_control:
    label: '{{ item.key }}'
  register: _bind_stat

- name: Create missing bind directories with the declared group
  ansible.builtin.file:
    path: '{{ item.item.value.host }}'
    state: directory
    owner: root
    group: '{{ item.item.value.group }}'
    mode: '2775'
  loop: '{{ _bind_stat.results }}'
  loop_control:
    label: '{{ item.item.key }}'
  when: not item.stat.exists
```

Membership changes only take effect for new sessions of the service user: after the group tasks, add a task that restarts the user manager only when membership changed (`register` the user task, `when: _member.changed`): `systemctl --user -M {{ svc_user }}@ daemon-reexec` is not enough for supplementary groups; use the synchronous pair `systemctl stop user@{{ svc_uid }}.service` then `systemctl start user@{{ svc_uid }}.service` (implemented; `loginctl terminate-user` is asynchronous and fails when logind has no user object), followed by the existing `Wait for the user manager` task (move the wait after this block). Document this in a comment.

- [ ] **Step 4: Role: units.yml ordering and build propagation**

Replace the `Map rendered files to systemd unit names` task and the following `Restart everything...` task with:

```yaml
- name: Map rendered files to systemd unit names, in start order (build, pod, container)
  ansible.builtin.set_fact:
    _mapped: "{{ (_mapped | default([])) + [{'unit': unit_name, 'kind': _ext, 'changed': item.changed}] }}"
  vars:
    _base: '{{ item.dest | basename }}'
    _stem: '{{ (_base | splitext)[0] }}'
    _ext: '{{ (_base | splitext)[1] }}'
    unit_name: "{{ _stem ~ {'.container': '.service', '.pod': '-pod.service', '.build': '-build.service'}[_ext] }}"
  loop: '{{ quadlet_render.results }}'
  loop_control:
    label: '{{ item.dest | basename }}'
  when: (item.dest | basename | splitext)[1] in ['.container', '.pod', '.build']

- name: Order units and propagate build changes to containers
  ansible.builtin.set_fact:
    service_units: "{{ _ordered | map(attribute='unit') | list }}"
    changed_units: >-
      {{ _ordered | selectattr('changed') | map(attribute='unit') | list
         + ((_ordered | selectattr('kind', 'equalto', '.container') | map(attribute='unit') | list)
            if (_ordered | selectattr('kind', 'equalto', '.build') | selectattr('changed') | list | length > 0) else []) }}
  vars:
    _ordered: >-
      {{ (_mapped | default([]) | selectattr('kind', 'equalto', '.build') | list)
         + (_mapped | default([]) | selectattr('kind', 'equalto', '.pod') | list)
         + (_mapped | default([]) | selectattr('kind', 'equalto', '.container') | list) }}

- name: Restart everything if config or a secret changed
  ansible.builtin.set_fact:
    changed_units: '{{ service_units }}'
  when: (config_copy is defined and config_copy.changed) or (secret_sync is defined and secret_sync.changed)
```

Reset `_mapped: []` alongside `service_units`/`changed_units` at the top of `units.yml`. `Restart changed units` keeps `| unique` and therefore preserves the build → pod → container order. For a `.build` unit, "restart" means rebuild: `systemctl --user -M svc@ restart <name>-build.service` runs the oneshot again; confirm Quadlet marks build units `Type=oneshot` with `RemainAfterExit=yes` so `is-active` reports `active` after success (adjust `Check unit states` to treat `active` or `exited` as up if needed).

- [ ] **Step 5: Templates get the new variables**

No template change in this task; document in `ansible/roles/service/README.md` (create it, 30 lines) the full variable contract available to `quadlet/*.j2`: `service` (with merged `config`), `svc_user`, `volumes`, `binds`, `devices_enabled`, `keep_groups`, `config_dir`, `fqdn`, `tz`, `acme`, `acme_email`, `domain`, and the idioms:

```
{% for name, b in binds.items() %}
Volume={{ b.host }}:{{ b.container }}:{{ b.mode | default('ro') }}
{% endfor %}
{% if devices_enabled %}{% for d in service.devices %}
AddDevice={{ d }}
{% endfor %}{% endif %}
{% if keep_groups %}
GroupAdd=keep-groups
{% endif %}
```

- [ ] **Step 6: host_base and bootstrap**

`ansible/roles/host_base/tasks/main.yml`: package task becomes `name: "{{ ['podman', 'passt', 'sops', 'age', 'ansible', 'git', 'openssh', 'make'] + (packages | default([])) }}"`. `bootstrap.sh`: add `make` to the `pacman -Syu` line. Add `make` to `test_host_base.py::test_packages`'s list.

- [ ] **Step 7: Run**

Before the run, set the platform in `tests/integration/molecule/test-ci/molecule.yml` to `memory_mib: 8192` and `vcpus: 4` (five services will run on it from Task 3 on). `make test-static` (green), then `make test-integration` (green; `test_binds.py` collects zero cases and is fine). Idempotence must stay clean: the group/membership tasks and bind `stat`/`file` pair must not report changes on the second run.

- [ ] **Step 8: Commit**

```bash
git add ansible bootstrap.sh tests
git commit -m "Support binds, devices, groups and per-host config in the service role; order builds first"
```

---

### Task 3: yuzukam

**Files:**

- Create: `hosts/storagebaby/services/yuzukam/service.yml`, `quadlet/yuzukam.container.j2`, `README.md`
- Create symlink: `hosts/test-a/services/yuzukam -> ../../storagebaby/services/yuzukam`
- Delete: `yuzukam/`

**Interfaces:** loopback port 3000, domain `yuzukam`, no volumes, no secrets, auto-updated.

- [ ] **Step 1: Static tests first**

Run `make test-static` after creating only the symlink and `service.yml` below: `test_render` must skip for yuzukam (no template yet), contract passes.

`service.yml`:

```yaml
name: yuzukam
port: 3000
domain: yuzukam
volumes: {}
secrets: []
backup: none
```

Symlink: `ln -s ../../storagebaby/services/yuzukam hosts/test-a/services/yuzukam`.

Confirm the Ansible discovery follows the symlink: `make molecule CMD=create`, `CMD=prepare`, `CMD=converge` and check the play output lists `yuzukam` under "Apply the service role per placed service". If `find` does not descend into the symlinked directory, set `follow: true` on both `find` tasks in `ansible/playbook.yml` (and in prepare.yml's discovery) and re-run.

- [ ] **Step 2: Unit template and README**

`quadlet/yuzukam.container.j2`:

```ini
[Unit]
Description=Yuzukam

[Container]
Image=ghcr.io/janlucaklees/yuzukam:latest
ContainerName=yuzukam
AutoUpdate=registry
PublishPort=127.0.0.1:{{ service.port }}:3000
Environment=TZ={{ tz }}
HealthCmd=wget -q --spider http://127.0.0.1:3000/ || exit 1
HealthOnFailure=kill

[Service]
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

If the image lacks `wget`, use `curl -sf` or, failing both, `HealthCmd=sh -c 'exec 3<>/dev/tcp/127.0.0.1/3000'` and note it.

`README.md`: three lines: what it is, image and auto-update, reachable at `yuzukam.<domain>`.

- [ ] **Step 3: Full run and delete the old directory**

`make test-static` green (render + dryrun now include yuzukam), `make test-integration` green: `test_service.py` must show yuzukam's unit active, container healthy and `https://127.0.0.1/` with `Host: yuzukam.test.local` answering non-5xx. Then `git rm -r yuzukam`.

- [ ] **Step 4: Commit**

```bash
git add -A hosts/storagebaby/services/yuzukam hosts/test-a/services/yuzukam
git commit -m "Migrate yuzukam to the platform"
```

---

### Task 4: stirling-pdf

**Files:**

- Create: `hosts/storagebaby/services/stirling-pdf/{service.yml,quadlet/stirling-pdf.container.j2,README.md}`, symlink on test-a
- Delete: `stirling-pdf/`

`service.yml`:

```yaml
name: stirling-pdf
port: 8180
domain: stirling
volumes:
  configs: { class: pool }
  logs: { class: pool }
  pipeline: { class: pool }
  tessdata: { class: pool }
secrets: []
backup: none
```

`quadlet/stirling-pdf.container.j2`:

```ini
[Unit]
Description=Stirling PDF

[Container]
Image=docker.stirlingpdf.com/stirlingtools/stirling-pdf:latest
ContainerName=stirling-pdf
AutoUpdate=registry
PublishPort=127.0.0.1:{{ service.port }}:8080
Environment=TZ={{ tz }}
Environment=SECURITY_ENABLELOGIN=true
Volume={{ volumes.configs }}:/configs
Volume={{ volumes.logs }}:/logs
Volume={{ volumes.pipeline }}:/pipeline
Volume={{ volumes.tessdata }}:/usr/share/tessdata
HealthCmd=curl -sf http://127.0.0.1:8080/ || exit 1
HealthOnFailure=kill

[Service]
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

README: carry over the old `stirling-pdf/README.md` content about what it does, minus the reconciler paragraphs. Steps and commit as Task 3 (`git rm -r stirling-pdf` after green; message "Migrate stirling-pdf to the platform"). Stirling is Java and slow to start: if the health check flaps on first start, add `HealthStartPeriod=120s`.

---

### Task 5: jellyfin

**Files:**

- Create: `hosts/storagebaby/services/jellyfin/{service.yml,quadlet/jellyfin.container.j2,README.md}`, symlink on test-a
- Delete: `jellyfin/`

`service.yml`:

```yaml
name: jellyfin
port: 8096
domain: jellyfin
volumes:
  config: { class: pool }
binds:
  media: { host: /pool/shared/media, container: /media, mode: ro, group: media }
devices: [/dev/dri]
groups: [render, video]
config:
  published_url: jellyfin.janlucaklees.de
secrets: []
backup: none
```

`quadlet/jellyfin.container.j2`:

```ini
[Unit]
Description=Jellyfin

[Container]
Image=lscr.io/linuxserver/jellyfin:latest
ContainerName=jellyfin
AutoUpdate=registry
PublishPort=127.0.0.1:{{ service.port }}:8096
UserNS=keep-id:uid=1000,gid=1000
{% if keep_groups %}
GroupAdd=keep-groups
{% endif %}
Environment=TZ={{ tz }}
Environment=PUID=1000
Environment=PGID=1000
Environment=JELLYFIN_PublishedServerUrl={{ service.config.published_url }}
Volume={{ volumes.config }}:/config
{% for name, b in binds.items() %}
Volume={{ b.host }}:{{ b.container }}:{{ b.mode | default('ro') }}
{% endfor %}
{% if devices_enabled %}
{% for d in service.devices %}
AddDevice={{ d }}
{% endfor %}
{% endif %}
HealthCmd=curl -sf http://127.0.0.1:8096/health || exit 1
HealthOnFailure=kill

[Service]
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

README: image, auto-update, GPU passthrough gated by `gpu`, the `media` bind and its group, the one-time operator command on storagebaby (`chgrp -R media /pool/shared/media && chmod -R g+rX /pool/shared/media`, not run by the role), and the note that PUID/PGID 1000 inside maps to `svc-jellyfin` on the host via keep-id.

Verification specific to this task: `test_binds.py` now collects jellyfin and must pass (group-readable probe file readable through `podman exec jellyfin`). If keep-groups does not survive linuxserver's s6 user switch, report it: the fallback is documenting `o+rX` on the media tree instead of the group, and the README changes accordingly. Steps and commit as Task 3 (`git rm -r jellyfin`, "Migrate jellyfin to the platform").

---

### Task 6: paperless-upload (host build)

**Files:**

- Create: `hosts/storagebaby/services/paperless-upload/{service.yml,quadlet/paperless-upload.build.j2,quadlet/paperless-upload.container.j2,config/build/Containerfile,config/build/index.ts,secrets.sops.yaml,README.md}`, symlink on test-a
- Delete: `paperless-upload/`

`service.yml`:

```yaml
name: paperless-upload
volumes: {}
binds:
  scans: { host: /pool/shared/scans, container: /data, mode: rw, group: scans }
config:
  paperless_url: https://paperless.home.klees.io
secrets: [paperless_token]
backup: none
```

`config/build/Containerfile`: the old `paperless-upload/Dockerfile` verbatim. `config/build/index.ts`: the old `index.ts` with `const PAPERLESS_URL = Bun.env.PAPERLESS_URL ?? 'https://paperless.example.com'`, `WATCH_DIR = '/data/in'`, `PROCESSED_DIR = '/data/processed'` (the bind mounts the scans tree at `/data`; the old compose mounted the tree itself at `/data/in`, so on storagebaby the incoming folder is now `/pool/shared/scans/in` and processed files land in `/pool/shared/scans/processed`; document this and confirm with the operator before the first real deploy).

`quadlet/paperless-upload.build.j2`:

```ini
[Unit]
Description=Build the paperless-upload image

[Build]
ImageTag=localhost/paperless-upload:latest
File={{ config_dir }}/build/Containerfile
SetWorkingDirectory=file
```

`quadlet/paperless-upload.container.j2`:

```ini
[Unit]
Description=Paperless uploader (watches the scans folder)

[Container]
Image=paperless-upload.build
ContainerName=paperless-upload
UserNS=keep-id:uid=1000,gid=1000
GroupAdd=keep-groups
Environment=TZ={{ tz }}
Environment=PAPERLESS_URL={{ service.config.paperless_url }}
Secret=paperless_token
{% for name, b in binds.items() %}
Volume={{ b.host }}:{{ b.container }}:{{ b.mode | default('rw') }}
{% endfor %}
HealthCmd=test -d /data/in
HealthOnFailure=kill

[Service]
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

Secrets: encrypt the real token from the main clone at `/home/jlk/Projects/StorageBaby/paperless-upload/paperless-token.secret` (never print it) into `secrets.sops.yaml` using `--filename-override hosts/storagebaby/services/paperless-upload/secrets.sops.yaml` so the storagebaby rule applies (operator key only). The test host gets a generated value from prepare.

Verification: after converge, `paperless-upload-build.service` is active (or `exited` with success), the container is healthy, and a second converge changes nothing. Change one comment in `index.ts`, converge again: the build unit and then the container restart (the unit's `ActiveEnterTimestampMonotonic` moves), proving Task 2's propagation. Capture that in the report; add it as a test only if it fits `test_deploy.py`'s pattern without a second VM change. Then `git rm -r paperless-upload`; commit "Migrate paperless-upload to the platform as a host build".

---

### Task 7: kopia server

**Files:**

- Create: `hosts/storagebaby/services/kopia/{service.yml,quadlet/kopia.container.j2,config/start.sh,secrets.sops.yaml,README.md}`, symlink on test-a
- Modify: `hosts/test-a/host.yml` (`service_config: { kopia: { repository: filesystem } }`)
- Delete: `kopia/`

`service.yml`:

```yaml
name: kopia
port: 51515
domain: kopia
volumes:
  config: { class: pool }
  cache: { class: fast }
  logs: { class: fast }
  repo: { class: fast }
config:
  server_username: jlk
  repository: s3
  s3_endpoint: s3.eu-central-003.backblazeb2.com
  s3_bucket: storagebaby-kopia
secrets: [server_password, repository_password, b2_key_id, b2_application_key]
backup: none
```

(`s3_endpoint` and `s3_bucket` are placeholders to confirm with the operator; they are not secret.)

`config/start.sh`:

```sh
#!/bin/sh
set -eu

export KOPIA_PASSWORD="$(cat /run/secrets/repository_password)"
server_password="$(cat /run/secrets/server_password)"

if [ ! -f /app/config/repository.config ]; then
	case "$KOPIA_REPOSITORY" in
		s3)
			kopia repository connect s3 \
				--bucket="$KOPIA_S3_BUCKET" \
				--endpoint="$KOPIA_S3_ENDPOINT" \
				--access-key="$(cat /run/secrets/b2_key_id)" \
				--secret-access-key="$(cat /run/secrets/b2_application_key)"
			;;
		filesystem)
			if [ -f /app/repo/kopia.repository.f ] || [ -d /app/repo/kopia.blobcfg ] || [ -n "$(ls -A /app/repo 2> /dev/null)" ]; then
				kopia repository connect filesystem --path=/app/repo
			else
				kopia repository create filesystem --path=/app/repo
			fi
			;;
		*)
			echo "unknown KOPIA_REPOSITORY: $KOPIA_REPOSITORY" >&2
			exit 2
			;;
	esac
fi

exec kopia server start \
	--insecure \
	--address='http://0.0.0.0:51515' \
	--server-username="$KOPIA_SERVER_USERNAME" \
	--server-password="$server_password"
```

`quadlet/kopia.container.j2`:

```ini
[Unit]
Description=Kopia repository server

[Container]
Image=docker.io/kopia/kopia:0.23.1
ContainerName=kopia
PublishPort=127.0.0.1:{{ service.port }}:51515
Entrypoint=/bin/sh
Exec={{ config_dir }}/start.sh
Environment=TZ={{ tz }}
Environment=KOPIA_SERVER_USERNAME={{ service.config.server_username }}
Environment=KOPIA_REPOSITORY={{ service.config.repository }}
Environment=KOPIA_S3_ENDPOINT={{ service.config.s3_endpoint | default('') }}
Environment=KOPIA_S3_BUCKET={{ service.config.s3_bucket | default('') }}
Secret=server_password
Secret=repository_password
Secret=b2_key_id
Secret=b2_application_key
Volume={{ config_dir }}/start.sh:{{ config_dir }}/start.sh:ro
Volume={{ volumes.config }}:/app/config
Volume={{ volumes.cache }}:/app/cache
Volume={{ volumes.logs }}:/app/logs
Volume={{ volumes.repo }}:/app/repo
HealthCmd=curl -sf http://127.0.0.1:51515/ || exit 1
HealthOnFailure=kill

[Service]
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

Secrets file for storagebaby: generate strong random values for `server_password` and `repository_password` (these become the real ones: state in README that the repository password cannot be recovered and must be backed up outside the repo), and the literal `REPLACE_ME` for `b2_key_id` and `b2_application_key`. Encrypt with the storagebaby rule. README: what it is, the declared connection, the four secrets, `make sops FILE=...` to fill the B2 values, the UI at `kopia.<domain>`, filesystem backend for test hosts via `service_config`, clients arrive in Phase 3.

Verification: on `test-a`, kopia comes up with a filesystem repository (`podman exec kopia kopia repository status` succeeds), the UI answers through Traefik with 401 or 200 (basic auth), idempotence clean. Delete `kopia/`; commit "Migrate the kopia server to the platform with a declared repository connection".

---

### Task 8: Docs and cleanup

- Delete `watchtower/`, `ofelia/` (`git rm -r`). Delete `install.sh` at the repo root (superseded by bootstrap and Ansible; storage parts move to Phase 4 roles, note this in CLAUDE.md's storage section as "install.sh removed; Phase 4 ports these to Ansible").
- README: a services table (name, domain, update policy, phase), the shared-tree permission step, the Kopia B2 secrets step.
- CLAUDE.md "Managing services": mention `binds`, `devices`, `groups`, `config` and `service_config`, and the test-host symlink convention.
- `make fmt-check`, `make test-static`, `make test-integration` green. Commit "Document the Phase 2 services and remove the retired Docker directories".

---

## Self-review notes

- Spec coverage: §2 keys (T1, T2), §3 services (T3–T7), §4 role changes (T2), §5 scenario (T3 symlink verification, T7 service_config, VM size: add `memory_mib: 8192`, `vcpus: 4` to `molecule.yml` in T2 Step 8 before the first five-service run), §6 order followed, §7 open item surfaces in T5 README and the final report.
- Names: `binds`, `devices_enabled`, `keep_groups`, `extra_groups`, `service.config`, `service_config` consistent between T1 tests, T2 role and T5–T7 templates.
