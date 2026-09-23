# Phase 3: Pods, Backup Clients and Timers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring paperless, openarchiver, immich and nextcloud onto the platform as Podman pods with role-generated Kopia backup sidecars, database dump timers, multi-route services and post-change hooks, all proven on the test VM with generated secrets.

**Architecture:** Unchanged platform: the `service` role turns a service folder into a rootless user with rendered Quadlet units. Phase 3 teaches the role four things: several Traefik routes per service, secrets shared between services on one host (`host_secrets`), commands to run after a change restarted a service (`hooks.after_change`), and plain systemd user units for timers. The `backup` block, dormant since Phase 1, now produces a Kopia client container inside the service's pod from a role-owned template, and the Kopia server registers the client users from a per-host secret set.

**Tech Stack:** As before. New: Quadlet `.pod` units, `Secret=...,type=env`, systemd user timers, kopia server users.

**Spec:** `docs/superpowers/specs/2026-09-23-phase-3-pods-and-backups-design.md`

## Global Constraints

- Rootless only, one user per service, loopback publishing only, nothing privileged, no hostname in roles/templates/tests/tasks (the host name reaches templates only as the variable `hostname`).
- Tests never use real data or secrets. Test hosts generate every `secrets` and `host_secrets` value. storagebaby secrets files hold real values only where they already exist in the checkout (OpenArchiver's six `.secret` files); every other unknown value is the literal `REPLACE_ME`, listed in the README operator steps.
- A service with `backup` must define exactly one `.pod.j2` named `<name>.pod.j2`; the role adds the sidecar `<name>-backup.container`, the implicit `fast` volumes `backup-config` and `backup-cache`, and requires `kopia_password` in `host_secrets`.
- Every container template: `ContainerName=<name>-<part>`, `Pod=<name>.pod` (in pod services), `HealthCmd`, `HealthOnFailure=kill`, `Restart=always`. Every `PublishPort=` lives on the `.pod.j2` and starts `127.0.0.1:`.
- Everything on deploy: hooks run through the role, never by hand; a version bump is a template change.
- Placement: `hosts/test-a/services/<name>` symlinks for all four pods; `hosts/test-ci/services/` for the CI subset.
- Tests are the verification: `make test-static` and `make test-integration` green with idempotence after every task; each service task deletes its old directory in the same commit.
- Commands via `make` only; never docker/virsh mutations/molecule directly; never escalate container privileges. Commit per task on `podman-platform` (standing permission); do not push.

---

### Task 1: Contracts and role — routes, host_secrets, hooks, timers, backup sidecar

**Files:**

- Modify: `tests/static/test_contract.py`, `tests/static/test_ports.py`, `tests/static/test_secrets.py`, `tests/static/test_render.py`, `tests/static/test_quadlet_conventions.py`
- Modify: `ansible/roles/service/tasks/{main,host,secrets,units,render}.yml`, `ansible/roles/service/templates/traefik-route.yml.j2`, `ansible/roles/service/README.md`
- Create: `ansible/roles/service/templates/kopia-client.container.j2`, `ansible/roles/service/files/kopia-client.sh`, `ansible/roles/service/tasks/hooks.yml`, `ansible/roles/service/tasks/timers.yml`
- Modify: `Makefile` (pod-aware `start|stop|restart|ps|logs`)
- Modify: `hosts/shared/services/traefik/service.yml` unchanged in content, but its route file name changes (see Step 4)

**Interfaces:**

- Produces role facts: `routes` (list of `{domain, port, fqdn}`; derived from `routes` or the `domain`+`port` shorthand), `hostname` (`inventory_hostname`), `timer_units` (list of `<stem>.timer`), `backup_enabled` (bool).
- Produces: route files `/etc/storagebaby/traefik/dynamic.d/<name>-<domain>.yml`, router name `<name>-<domain>`; the old `<name>.yml` is removed.
- Produces: podman secrets from `host_secrets` (`<secret>: <set>.<key>`), read from `hosts/<host>/secrets/<set>.sops.yaml` decrypted on the host, synced with the same helper, counted in `secret_sync.changed`.
- Produces: `hooks.after_change` execution via `hooks.yml` after unit restarts: for each hook, wait until `podman healthcheck run <container>` succeeds (30 × 10 s), then `podman exec [-u <user>] <container> sh -c '<command>'` as the service user; `when: unit_changed` (default) runs only if `changed_units` is non-empty, `when: always` every converge.
- Produces: timers from `quadlet/*.timer.j2` + `*.service.j2` rendered to `/home/svc-<name>/.config/systemd/user/`, enabled and started, restarted on change.
- Produces: `<name>-backup.container` from the role template when `service.backup != 'none'`, plus `kopia-client.sh` in `config_dir`.
- Template variables added: `routes`, `hostname`, `backup_config_volume`, `backup_cache_volume`.

- [ ] **Step 1: Static tests first**

`tests/static/test_contract.py`, add after the existing checks inside `test_service_contract`:

```python
    routes = spec.get("routes")
    if routes is not None:
        assert "domain" not in spec and "port" not in spec, f"{p.name}: use either routes or domain+port"
        assert isinstance(routes, list) and routes, f"{p.name}: routes must be a non-empty list"
        for r in routes:
            assert set(r) == {"domain", "port"}, f"{p.name}: route entries need exactly domain and port"
            assert isinstance(r["port"], int)
        assert len({r["domain"] for r in routes}) == len(routes), f"{p.name}: duplicate route domains"
    for secret, ref in spec.get("host_secrets", {}).items():
        assert GROUP_RE.match(secret), f"{p.name}: invalid host secret name {secret}"
        assert re.match(r"^[a-z0-9-]+\.[A-Za-z0-9_-]+$", ref), f"{p.name}: host secret {secret} must reference <set>.<key>"
        assert secret not in spec["secrets"], f"{p.name}: {secret} declared in both secrets and host_secrets"
    for hook in spec.get("hooks", {}).get("after_change", []):
        assert {"container", "command"} <= set(hook), f"{p.name}: hook needs container and command"
        assert hook.get("when", "unit_changed") in {"unit_changed", "always"}
    if spec["backup"] != "none":
        pods = list((p.dir / "quadlet").glob("*.pod.j2"))
        assert [q.name for q in pods] == [f"{p.name}.pod.j2"], f"{p.name}: backup requires exactly {p.name}.pod.j2"
        unknown = set(spec["backup"]["paths"]) - set(spec["volumes"])
        assert not unknown, f"{p.name}: backup paths not in volumes: {unknown}"
        assert "kopia_password" in spec.get("host_secrets", {}), f"{p.name}: backup requires host_secrets.kopia_password"
    timers = {q.name[:-9] for q in (p.dir / "quadlet").glob("*.timer.j2")}
    services = {q.name[:-11] for q in (p.dir / "quadlet").glob("*.service.j2")}
    assert timers == services, f"{p.name}: every .timer.j2 needs a matching .service.j2 and vice versa"
```

Add a module-level helper in `tests/static/conftest.py`:

```python
def route_ports(spec: dict) -> list[int]:
    if "routes" in spec:
        return [r["port"] for r in spec["routes"]]
    return [spec["port"]] if "port" in spec else []
```

`tests/static/test_ports.py`: use `route_ports(p.spec)` and record every port per host.

`tests/static/test_secrets.py`, add:

```python
@pytest.mark.parametrize("p", placements(), ids=lambda p: f"{p.host}/{p.name}")
def test_host_secret_references_resolve(p):
    for secret, ref in p.spec.get("host_secrets", {}).items():
        set_name, key = ref.split(".", 1)
        set_file = HOSTS / p.host / "secrets" / f"{set_name}.sops.yaml"
        if p.host.startswith("test-"):
            pytest.skip("test hosts generate their secret sets")
        assert set_file.exists(), f"{p.host}: missing secret set {set_file.name} for {p.name}.{secret}"
        assert key in set(load_yaml(set_file)) - {"sops"}, f"{p.host}: {set_name} has no key {key}"
```

`tests/static/test_quadlet_conventions.py`, add:

```python
@pytest.mark.parametrize("p", [p for p in placements() if list((p.dir / "quadlet").glob("*.pod.j2"))], ids=lambda p: f"{p.host}/{p.name}")
def test_pod_publishes_exactly_the_route_ports(p):
    pod = next((p.dir / "quadlet").glob("*.pod.j2")).read_text()
    published = re.findall(r"^PublishPort=127\.0\.0\.1:(\d+):", pod, flags=re.M)
    assert sorted(int(x) for x in published) == sorted(route_ports(p.spec))
    assert not re.search(r"^PublishPort=(?!127\.0\.0\.1:)", pod, flags=re.M)
    for tpl in (p.dir / "quadlet").glob("*.container.j2"):
        text = tpl.read_text()
        assert re.search(rf"^Pod={p.name}\.pod$", text, flags=re.M), f"{tpl.name} must join {p.name}.pod"
        assert not re.search(r"^PublishPort=", text, flags=re.M), f"{tpl.name}: publish ports on the pod, not the container"
```

`tests/static/test_render.py`: the expected unit set for a service is the template names minus `.j2` plus `<name>-backup.container` when `backup != 'none'`; timers render under `<out>/<host>/<name>/timers/`; route files are `traefik-dynamic.d/<name>-<domain>.yml` for every route.

Run `make test-static`: the traefik route test fails (file name changed), everything else passes. That is the RED for this task.

- [ ] **Step 2: Role facts (main.yml)**

Append to `ansible/roles/service/tasks/main.yml` after the bind/device facts:

```yaml
- name: Reset the per-service route accumulator
  ansible.builtin.set_fact:
    routes_out: []

- name: Derive hostname, backup flag and raw routes
  ansible.builtin.set_fact:
    hostname: '{{ inventory_hostname }}'
    backup_enabled: "{{ service.backup != 'none' }}"
    _route_specs: "{{ service.routes | default(([{'domain': service.domain, 'port': service.port}] if service.domain is defined else [])) }}"

- name: Resolve route fqdns
  ansible.builtin.set_fact:
    routes_out: "{{ routes_out + [item | combine({'fqdn': item.domain ~ '.' ~ domain})] }}"
  loop: '{{ _route_specs }}'
  loop_control:
    label: '{{ item.domain }}'

- name: Routes and the single-route fqdn kept for existing templates
  ansible.builtin.set_fact:
    routes: '{{ routes_out }}'
    fqdn: "{{ (routes_out | first).fqdn if routes_out | length > 0 else '' }}"

- name: Implicit backup volumes
  ansible.builtin.set_fact:
    volumes: "{{ volumes | combine({'backup-config': storage_roots.fast ~ '/' ~ service.name ~ '/backup-config', 'backup-cache': storage_roots.fast ~ '/' ~ service.name ~ '/backup-cache'}) }}"
  when: backup_enabled | bool
```

- [ ] **Step 3: Host secrets (secrets.yml)**

Append to `ansible/roles/service/tasks/secrets.yml` (and change its include condition in `host.yml` to `service.secrets | length > 0 or service.host_secrets | default({}) | length > 0`; guard the existing per-service file tasks with `when: service.secrets | length > 0`):

```yaml
- name: Host secret sets referenced by this service
  ansible.builtin.set_fact:
    _sets: "{{ service.host_secrets | default({}) | dict2items | map(attribute='value') | map('regex_replace', '\\..*$', '') | unique | list }}"

- name: Copy referenced secret sets to the host
  ansible.builtin.copy:
    src: '{{ storagebaby_repo_root }}/hosts/{{ inventory_hostname }}/secrets/{{ item }}.sops.yaml'
    dest: '/etc/storagebaby/set-{{ item }}.secrets.sops.yaml'
    owner: root
    group: root
    mode: '0600'
  loop: '{{ _sets }}'

- name: Decrypt secret sets on the host
  ansible.builtin.command: sops --decrypt --output-type json /etc/storagebaby/set-{{ item }}.secrets.sops.yaml
  environment:
    SOPS_AGE_KEY_FILE: /etc/storagebaby/age.key
  loop: '{{ _sets }}'
  register: _set_decrypted
  changed_when: false
  failed_when: false
  no_log: true

- name: Secret sets decrypted
  ansible.builtin.assert:
    that: item.rc == 0
    fail_msg: 'sops failed to decrypt secret set {{ item.item }}: {{ item.stderr }}'
  loop: '{{ _set_decrypted.results }}'
  loop_control:
    label: '{{ item.item }}'

- name: Parse secret sets
  ansible.builtin.set_fact:
    _set_values: '{{ (_set_values | default({})) | combine({item.item: (item.stdout | from_json)}) }}'
  loop: '{{ _set_decrypted.results }}'
  loop_control:
    label: '{{ item.item }}'
  no_log: true

- name: Sync host secrets into podman
  ansible.builtin.command:
    cmd: /usr/local/sbin/podman-secret-sync {{ svc_user }} {{ item.key }}
    stdin: "{{ _set_values[item.value.split('.', 1)[0]][item.value.split('.', 1)[1]] }}"
    stdin_add_newline: false
  loop: '{{ service.host_secrets | default({}) | dict2items }}'
  loop_control:
    label: '{{ item.key }}'
  register: host_secret_sync
  changed_when: "host_secret_sync.stdout | trim == 'CHANGED'"
  no_log: true
```

Missing keys fail with a Jinja undefined error under `no_log`; add an `assert` that every referenced key exists in `_set_values` with a message naming set and key (no values). In `units.yml`, the "Restart everything if config or a secret changed" condition also considers `host_secret_sync is defined and host_secret_sync.changed`; reset `host_secret_sync: {changed: false}` with the other per-service resets in `host.yml`; also reset `_set_values: {}`.

- [ ] **Step 4: Routes (units.yml, render.yml, route template)**

Replace the single route task in `units.yml` and `render.yml` with a loop over `routes`:

```yaml
- name: Render traefik routes
  ansible.builtin.template:
    src: traefik-route.yml.j2
    dest: '/etc/storagebaby/traefik/dynamic.d/{{ service.name }}-{{ item.domain }}.yml'
    owner: root
    group: root
    mode: '0644'
  loop: '{{ routes }}'
  loop_control:
    loop_var: route
    label: '{{ route.domain }}'

- name: Remove the pre-Phase-3 single route file
  ansible.builtin.file:
    path: '/etc/storagebaby/traefik/dynamic.d/{{ service.name }}.yml'
    state: absent
```

`traefik-route.yml.j2` uses `route.fqdn`, `route.port`, router name `{{ service.name }}-{{ route.domain }}`, service name likewise; `service.route.internal`/`wildcard_cert` still come from `service.route` (traefik's shorthand keeps them). Update `test_service.py::test_route_rendered` to loop over routes and expect the new file names (Task 2 adjusts the harness; keep this task's integration run green by updating that one test here).

- [ ] **Step 5: Timers (timers.yml)**

`ansible/roles/service/tasks/timers.yml`, included from `host.yml` after `units.yml`:

```yaml
- name: User unit directory
  ansible.builtin.file:
    path: '/home/{{ svc_user }}/.config/systemd/user'
    state: directory
    owner: '{{ svc_user }}'
    group: '{{ svc_user }}'
    mode: '0755'

- name: Render timer and service units
  ansible.builtin.template:
    src: '{{ item }}'
    dest: "/home/{{ svc_user }}/.config/systemd/user/{{ item | basename | regex_replace('\\.j2$', '') }}"
    owner: '{{ svc_user }}'
    group: '{{ svc_user }}'
    mode: '0644'
  loop: "{{ lookup('fileglob', service_dir ~ '/quadlet/*.timer.j2', wantlist=True) + lookup('fileglob', service_dir ~ '/quadlet/*.service.j2', wantlist=True) }}"
  loop_control:
    label: '{{ item | basename }}'
  register: timer_render

- name: Timer unit names
  ansible.builtin.set_fact:
    timer_units: "{{ lookup('fileglob', service_dir ~ '/quadlet/*.timer.j2', wantlist=True) | map('basename') | map('regex_replace', '\\.j2$', '') | list }}"

- name: Reload the user manager for timers
  ansible.builtin.command: systemctl --user -M {{ svc_user }}@ daemon-reload
  when: timer_render.changed
  changed_when: true

- name: Timers enabled and started
  ansible.builtin.command: systemctl --user -M {{ svc_user }}@ is-enabled {{ item }}
  loop: '{{ timer_units }}'
  register: _timer_enabled
  changed_when: false
  failed_when: false

- name: Enable timers
  ansible.builtin.command: systemctl --user -M {{ svc_user }}@ enable --now {{ item.item }}
  loop: '{{ _timer_enabled.results }}'
  loop_control:
    label: '{{ item.item }}'
  when: item.stdout | trim != 'enabled'
  changed_when: true

- name: Restart changed timers
  ansible.builtin.command: systemctl --user -M {{ svc_user }}@ restart {{ item }}
  loop: '{{ timer_units }}'
  when: timer_render.changed
  changed_when: true
```

Timer templates use `%h`-free absolute paths; `.service.j2` units run `podman exec` as the service user's manager already does (the user session has `XDG_RUNTIME_DIR`), e.g. `ExecStart=/usr/bin/podman exec paperless-database sh -c 'pg_dump -U paperless -Fc paperless > /backups/paperless.dump'`.

- [ ] **Step 6: Backup sidecar (role template + script)**

`ansible/roles/service/files/kopia-client.sh`:

```sh
#!/bin/sh
# Kopia client sidecar: connects to the platform's Kopia server through Traefik,
# applies the snapshot policy declared in service.yml, and keeps a scheduler
# running so snapshots happen at KOPIA_SNAPSHOT_TIME.
set -eu

export KOPIA_PASSWORD="$(cat /run/secrets/kopia_password)"
export KOPIA_CONFIG_PATH=/app/config/repository.config
export KOPIA_CACHE_DIRECTORY=/app/cache
export KOPIA_PERSIST_CREDENTIALS_ON_CONNECT=true
export KOPIA_USE_KEYRING=false

server_host="${KOPIA_SERVER_URL#https://}"
server_host="${server_host%%/*}"

if [ ! -f "$KOPIA_CONFIG_PATH" ]; then
	set -- kopia repository connect server \
		--url="$KOPIA_SERVER_URL" \
		--override-username="$KOPIA_CLIENT_USERNAME" \
		--override-hostname="$KOPIA_CLIENT_HOSTNAME"
	if [ "${KOPIA_PIN_SERVER_CERT:-0}" = "1" ]; then
		# Test hosts serve Traefik's self-signed default certificate: pin it.
		fingerprint="$(openssl s_client -connect "$server_host:443" -servername "$server_host" < /dev/null 2> /dev/null \
			| openssl x509 -outform DER | sha256sum | awk '{ print toupper($1) }')"
		set -- "$@" --server-cert-fingerprint="$fingerprint"
	fi
	until "$@"; do
		echo "kopia server not reachable yet, retrying in 15s" >&2
		sleep 15
	done
fi

for path in $KOPIA_PATHS; do
	kopia policy set "/data/$path" \
		--inherit=false \
		--no-manual \
		--snapshot-time="$KOPIA_SNAPSHOT_TIME" \
		--run-missed=true \
		--ignore-identical-snapshots=true \
		--keep-latest="$KOPIA_KEEP_LATEST" \
		--keep-hourly=0 \
		--keep-daily="$KOPIA_KEEP_DAILY" \
		--keep-weekly="$KOPIA_KEEP_WEEKLY" \
		--keep-monthly="$KOPIA_KEEP_MONTHLY" \
		--keep-annual="$KOPIA_KEEP_ANNUAL"
done

exec kopia server start \
	--address='http://127.0.0.1:51516' \
	--insecure \
	--without-password \
	--no-ui \
	--no-grpc
```

`ansible/roles/service/templates/kopia-client.container.j2`:

```ini
[Unit]
Description={{ service.name }} backup client (kopia)

[Container]
Image=docker.io/kopia/kopia:0.23.1
ContainerName={{ service.name }}-backup
Pod={{ service.name }}.pod
Entrypoint=/bin/sh
Exec={{ config_dir }}/kopia-client.sh
Environment=TZ={{ tz }}
Environment=KOPIA_SERVER_URL=https://kopia.{{ domain }}
Environment=KOPIA_CLIENT_USERNAME={{ service.name }}
Environment=KOPIA_CLIENT_HOSTNAME={{ hostname }}
Environment=KOPIA_PIN_SERVER_CERT={{ '0' if acme else '1' }}
Environment=KOPIA_PATHS={{ service.backup.paths | join(' ') }}
Environment=KOPIA_SNAPSHOT_TIME={{ service.backup.schedule }}
Environment=KOPIA_KEEP_LATEST={{ service.backup.retention.latest }}
Environment=KOPIA_KEEP_DAILY={{ service.backup.retention.daily }}
Environment=KOPIA_KEEP_WEEKLY={{ service.backup.retention.weekly }}
Environment=KOPIA_KEEP_MONTHLY={{ service.backup.retention.monthly }}
Environment=KOPIA_KEEP_ANNUAL={{ service.backup.retention.annual }}
Secret=kopia_password
Volume={{ config_dir }}/kopia-client.sh:{{ config_dir }}/kopia-client.sh:ro
Volume={{ volumes['backup-config'] }}:/app/config
Volume={{ volumes['backup-cache'] }}:/app/cache
{% for path in service.backup.paths %}
Volume={{ volumes[path] }}:/data/{{ path }}:ro
{% endfor %}
HealthCmd=kopia repository status
HealthOnFailure=kill
HealthStartPeriod=180s

[Service]
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

In `units.yml` (and `render.yml`), after rendering the service's own quadlets, when `backup_enabled`: copy `kopia-client.sh` into `config_dir` (owner svc, 0644), render the template to `{{ unit_dir }}/{{ service.name }}-backup.container`, register `backup_render`, and append its result to `quadlet_render.results`-equivalent handling (simplest: build `_all_results` as `quadlet_render.results + ([backup_render] if backup_enabled else [])` and use that for the unit map).

- [ ] **Step 7: Hooks (hooks.yml)**

`ansible/roles/service/tasks/hooks.yml`, included at the end of `units.yml` when `service.hooks.after_change | default([]) | length > 0`:

```yaml
- name: Hooks to run this converge
  ansible.builtin.set_fact:
    _hooks: >-
      {{ service.hooks.after_change | default([])
         | selectattr('when', 'defined') | selectattr('when', 'equalto', 'always') | list
         + (service.hooks.after_change | default([]) | rejectattr('when', 'defined') | list
            + service.hooks.after_change | default([]) | selectattr('when', 'defined') | selectattr('when', 'equalto', 'unit_changed') | list
            if (changed_units | default([]) | length > 0) else []) }}

- name: Wait for hook containers to be healthy
  ansible.builtin.command: /usr/local/sbin/podman-as {{ svc_user }} podman healthcheck run {{ item.container }}
  loop: '{{ _hooks }}'
  loop_control:
    label: '{{ item.container }}'
  register: _hook_health
  retries: 30
  delay: 10
  until: _hook_health.rc == 0
  changed_when: false

- name: Run after_change hooks
  ansible.builtin.command: >-
    /usr/local/sbin/podman-as {{ svc_user }} podman exec
    {% if item.user is defined %}-u {{ item.user }}{% endif %}
    {{ item.container }} sh -c {{ item.command | quote }}
  loop: '{{ _hooks }}'
  loop_control:
    label: '{{ item.container }}: {{ item.command }}'
  changed_when: true
```

`ansible/roles/service/files/podman-as` is a 6-line helper installed once by the role (alongside `podman-secret-sync`): `cd /tmp && exec runuser -u "$1" -- env XDG_RUNTIME_DIR=/run/user/$(id -u "$1") DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u "$1")/bus "${@:2}"` (bash). Reuse it in `podman-secret-sync.sh` too if it simplifies; keep behaviour.

- [ ] **Step 8: Pod-aware Makefile wrappers**

`start|stop|restart|ps|logs SERVICE=x`: the unit is `$(SERVICE)-pod.service` if `systemctl --user -M svc-$(SERVICE)@ list-unit-files $(SERVICE)-pod.service` lists it, else `$(SERVICE).service`. Implement with a shell snippet in a `define` used by the targets; `logs` follows `_SYSTEMD_USER_UNIT=<unit>`.

- [ ] **Step 9: README and run**

Update `ansible/roles/service/README.md` for the four features and the generated sidecar. `make test-static` green; `make test-integration` green (traefik's route file is now `traefik-traefik.yml`; no pod yet, so sidecar/timer/hook code paths are dormant but rendered nowhere; idempotence clean).

- [ ] **Step 10: Commit**

```bash
git add ansible tests Makefile
git commit -m "Teach the service role routes, host secrets, hooks, timers and the kopia backup sidecar"
```

---

### Task 2: Harness — test-ci host, pod checks, secret sets, kopia client registration

**Files:**

- Create: `hosts/test-ci/host.yml`, `hosts/test-ci/services/.gitkeep` plus symlinks `yuzukam`, `kopia`, `paperless-upload` (traefik is shared and needs none; paperless is added in Task 3)
- Modify: `ansible/inventory/hosts.yml` (add `test-ci`), `tests/integration/molecule/test-ci/molecule.yml` (`name: ${MOLECULE_HOST:-test-a}`, `hostname:` same, memory default 12288), `Makefile` (`-e MOLECULE_HOST`, export), `.github/workflows/ci.yml` (`MOLECULE_HOST: test-ci`)
- Modify: `tests/integration/molecule/test-ci/prepare.yml` (generate `hosts/<host>/secrets/<set>.sops.yaml` for every set referenced by placed services, keys = union of referenced keys)
- Modify: `tests/integration/molecule/test-ci/tests/test_service.py` (pod unit active; routes loop; timers enabled; backup sidecar connected: `podman exec <name>-backup kopia repository status` ok and a triggered `kopia snapshot create /data/<first path>` appears in `podman exec kopia kopia snapshot list --all` on the server; dump timer: start `<name>-dump.service` once and assert the dump file under the `backups` volume exists), `test_deploy.py` (hook case: Task 6 adds the nextcloud marker; here add the generic mechanism: a service with `hooks` whose container unit changed must have run them, verified by a file the hook writes into a volume; the paperless pod in Task 3 gets a harmless `touch /usr/src/paperless/data/.hook-ran` hook with `when: unit_changed` for exactly this)
- Modify: `hosts/storagebaby/services/kopia/config/start.sh` (register `client_*` secrets as `<name>@<host>` users before the server starts), `service.yml` (`host_secrets` entries added per pod in Tasks 3–6), `quadlet/kopia.container.j2` (`Environment=KOPIA_CLIENT_HOSTS={{ hostname }}` and `Secret=client_<svc>` lines rendered from `service.host_secrets` keys starting with `client_`), `README.md`
- Create: `hosts/storagebaby/secrets/kopia-clients.sops.yaml` with `REPLACE_ME` values for paperless, openarchiver, immich, nextcloud (encrypted under the storagebaby rule)

**Interfaces:**

- Produces: `MOLECULE_HOST` selects the platform/host folder; `hosts/test-ci/` mirrors `test-a` with a subset.
- Produces: kopia start script registration: for each `/run/secrets/client_<name>`: `kopia server users add "<name>@$KOPIA_CLIENT_HOSTS" --user-password-file=/run/secrets/client_<name>` or `... users set ...` when it exists (check kopia 0.23's flag for a password file; if only `--user-password` exists, pass it via that flag and note it).
- Produces: `test_service.py` helpers `pod_unit(spec)`, `timers(spec_path)`, `backup_paths(spec)`.

Steps: write the new integration tests (they skip until a pod exists), the prepare changes, the harness plumbing, the kopia server registration; run `make test-static` and `make test-integration` (default host `test-a`, still five services) green; then `MOLECULE_HOST=test-ci make test-integration` green (three services + traefik). Commit "Add the CI test host, secret sets, kopia client registration and pod checks".

---

### Task 3: paperless pod

**Files:**

- Create: `hosts/storagebaby/services/paperless/{service.yml,secrets.sops.yaml,README.md}`, `quadlet/{paperless.pod.j2,paperless-app.container.j2,paperless-database.container.j2,paperless-broker.container.j2,paperless-gotenberg.container.j2,paperless-tika.container.j2,paperless-dump.timer.j2,paperless-dump.service.j2}`
- Symlinks on `test-a` and `test-ci`; kopia `service.yml` gains `client_paperless: kopia-clients.paperless`
- Delete: `paperless/` (tracked)

`service.yml`:

```yaml
name: paperless
port: 8000
domain: paperless
volumes:
  data: { class: pool }
  media: { class: pool }
  database: { class: fast }
  broker: { class: fast }
  backups: { class: fast }
config:
  database_name: paperless
  database_user: paperless
  ocr_language: deu
secrets: [database_password, secret_key]
host_secrets:
  kopia_password: kopia-clients.paperless
hooks:
  after_change:
    - {
        container: paperless-app,
        command: 'touch /usr/src/paperless/data/.hook-ran'
      }
backup:
  paths: [data, media, backups]
  schedule: '03:00'
  retention: { latest: 3, daily: 7, weekly: 4, monthly: 12, annual: 3 }
```

`quadlet/paperless.pod.j2`:

```ini
[Pod]
PodName=paperless
PublishPort=127.0.0.1:{{ service.port }}:8000
AddHost=kopia.{{ domain }}:host-gateway
```

`quadlet/paperless-database.container.j2`:

```ini
[Unit]
Description=Paperless database

[Container]
Image=docker.io/library/postgres:17-alpine
ContainerName=paperless-database
Pod=paperless.pod
AutoUpdate=registry
Environment=TZ={{ tz }}
Environment=POSTGRES_DB={{ service.config.database_name }}
Environment=POSTGRES_USER={{ service.config.database_user }}
Environment=POSTGRES_PASSWORD_FILE=/run/secrets/database_password
Secret=database_password
Volume={{ volumes.database }}:/var/lib/postgresql/data
Volume={{ volumes.backups }}:/backups
HealthCmd=pg_isready -U {{ service.config.database_user }} -d {{ service.config.database_name }}
HealthOnFailure=kill

[Service]
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

`quadlet/paperless-broker.container.j2`: `docker.io/library/redis:alpine`, `AutoUpdate=registry`, `Volume={{ volumes.broker }}:/data`, `HealthCmd=redis-cli --raw incr ping`. `quadlet/paperless-gotenberg.container.j2`: `docker.io/gotenberg/gotenberg:8`, `AutoUpdate=registry`, `Exec=gotenberg --chromium-disable-javascript=true --chromium-allow-list=file:///tmp/.*`, `HealthCmd=curl -sf http://127.0.0.1:3000/health || exit 1` (verify the image has curl; else `wget`). `quadlet/paperless-tika.container.j2`: `docker.io/apache/tika:latest`, `AutoUpdate=registry`, `HealthCmd=curl -sf http://127.0.0.1:9998/tika || exit 1` (tika image lacks curl: use `HealthCmd=sh -c 'exec 3<>/dev/tcp/127.0.0.1/9998'` if needed).

`quadlet/paperless-app.container.j2`:

```ini
[Unit]
Description=Paperless-ngx
After=paperless-database.service paperless-broker.service
Wants=paperless-database.service paperless-broker.service

[Container]
Image=ghcr.io/paperless-ngx/paperless-ngx:2.20.15
ContainerName=paperless-app
Pod=paperless.pod
Environment=TZ={{ tz }}
Environment=PAPERLESS_REDIS=redis://127.0.0.1:6379
Environment=PAPERLESS_DBHOST=127.0.0.1
Environment=PAPERLESS_DBNAME={{ service.config.database_name }}
Environment=PAPERLESS_DBUSER={{ service.config.database_user }}
Environment=PAPERLESS_DBPASS_FILE=/run/secrets/database_password
Environment=PAPERLESS_TIKA_ENABLED=1
Environment=PAPERLESS_TIKA_GOTENBERG_ENDPOINT=http://127.0.0.1:3000
Environment=PAPERLESS_TIKA_ENDPOINT=http://127.0.0.1:9998
Environment=PAPERLESS_OCR_LANGUAGE={{ service.config.ocr_language }}
Environment=PAPERLESS_URL=https://{{ routes[0].fqdn }}
Environment=PAPERLESS_TRUSTED_PROXIES={{ service.config.trusted_proxies | default('127.0.0.1') }}
Environment=PAPERLESS_SECRET_KEY_FILE=/run/secrets/secret_key
Secret=database_password
Secret=secret_key
Volume={{ volumes.data }}:/usr/src/paperless/data
Volume={{ volumes.media }}:/usr/src/paperless/media
HealthCmd=curl -fs -S --max-time 2 http://127.0.0.1:8000 || exit 1
HealthOnFailure=kill
HealthStartPeriod=120s

[Service]
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

`quadlet/paperless-dump.timer.j2`:

```ini
[Unit]
Description=Daily paperless database dump

[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

`quadlet/paperless-dump.service.j2`:

```ini
[Unit]
Description=Dump the paperless database into the backups volume

[Service]
Type=oneshot
ExecStart=/usr/bin/podman exec paperless-database sh -c 'pg_dump -U {{ service.config.database_user }} -Fc {{ service.config.database_name }} > /backups/{{ service.config.database_name }}.dump.tmp && mv /backups/{{ service.config.database_name }}.dump.tmp /backups/{{ service.config.database_name }}.dump'
```

Secrets file for storagebaby: `database_password: REPLACE_ME`, `secret_key: REPLACE_ME` (operator fills; README says the database password must match the migrated database, and that the secret key must be the live one or sessions and tokens are invalidated).

Verification on `test-a`: pod and all five parts active and healthy; route answers (paperless `/` → 302 to login); dump service run once produces `/backups/paperless.dump` in the backups volume; backup sidecar connected and a triggered snapshot listed on the kopia server; hook marker exists after the first converge and, in `test_deploy`, reappears after a unit change (delete it, change the app unit, deploy, assert present). Determine `trusted_proxies` empirically (what source address the app logs for a request through Traefik) and set it in `config`; document. Delete `paperless/`; commit "Migrate paperless to the platform as a pod with dump timer and backup client".

---

### Task 4: openarchiver pod

`service.yml`: port 3000, domain `openarchiver`, volumes `data pool, database fast, cache fast, meilisearch fast, backups fast`, config `database_name/database_user: openarchiver`, secrets `[database_password, redis_password, meili_master_key, jwt_secret, encryption_key, storage_encryption_key]` (real values from the six `openarchiver/*.secret` files in the checkout, encrypted under the storagebaby rule; never printed), `host_secrets: {kopia_password: kopia-clients.openarchiver}`, backup paths `[data, backups]`.

Pod: `PublishPort=127.0.0.1:{{ service.port }}:3000`, `AddHost`. Parts: `openarchiver-database` (postgres 17-alpine, auto, `POSTGRES_PASSWORD_FILE`, backups volume at `/backups`), `openarchiver-cache` (`docker.io/valkey/valkey:8-alpine`, auto, `Exec=sh -c 'valkey-server --requirepass "$(cat /run/secrets/redis_password)"'`, health `valkey-cli --no-auth-warning -a "$(cat /run/secrets/redis_password)" ping` via `HealthCmd=sh -c '...'`), `openarchiver-meilisearch` (`getmeili/meilisearch:v1.38`, pinned, `Secret=meili_master_key,type=env,target=MEILI_MASTER_KEY`, `MEILI_ENV=production`, volume `/meili_data`, health `curl -sf http://127.0.0.1:7700/health`), `openarchiver-tika` (`apache/tika:3.2.2.0-full`, pinned), `openarchiver-app` (`logiclabshq/open-archiver:v0.6.0`, pinned; env-type secrets for `REDIS_PASSWORD`, `MEILI_MASTER_KEY`, `JWT_SECRET`, `ENCRYPTION_KEY`, `STORAGE_ENCRYPTION_KEY`; `DATABASE_URL` assembled by `Entrypoint=/bin/sh` + `Exec=-c 'export DATABASE_URL="postgresql://{{ service.config.database_user }}:$(cat /run/secrets/database_password)@127.0.0.1:5432/{{ service.config.database_name }}"; exec <image entrypoint and cmd>'` where the implementer reads the image's entrypoint/cmd with `podman image inspect` on the VM; `MEILI_HOST=http://127.0.0.1:7700`, `REDIS_HOST=127.0.0.1`, `TIKA_URL=http://127.0.0.1:9998`, `APP_URL`/`ORIGIN` from `routes[0].fqdn`, `STORAGE_LOCAL_ROOT_PATH=/var/data/open-archiver` with `Volume={{ volumes.data }}:/var/data/open-archiver`; health `curl -sf http://127.0.0.1:3000/`), dump timer/service as paperless. Verification as Task 3 (no hook). Delete the untracked `openarchiver/` from disk. Commit "Migrate openarchiver to the platform as a pod".

---

### Task 5: immich pod

`service.yml`: port 2283, domain `immich`, volumes `upload pool, database fast, model-cache fast`, config `database_name: postgres`, `database_user: immich`, `version: v2.7.5`, secrets `[database_password]` (REPLACE_ME), `host_secrets.kopia_password`, backup paths `[upload]` (Immich writes its own database dumps into the upload tree; README says to enable them under Administration → Backup, 02:00).

Parts: `immich-database` (`ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0@sha256:5f6a838e4e44c8e0e019d0ebfe3ee8952b69afc2809b2c25f7b0119641978e91`, pinned, `POSTGRES_INITDB_ARGS=--data-checksums`, `POSTGRES_PASSWORD_FILE`), `immich-cache` (`docker.io/library/redis:alpine`, auto), `immich-server` (`ghcr.io/immich-app/immich-server:{{ service.config.version }}`, `DB_HOSTNAME=127.0.0.1`, `DB_PASSWORD_FILE`, `REDIS_HOSTNAME=127.0.0.1`, `IMMICH_TRUSTED_PROXIES` from config, `Volume={{ volumes.upload }}:/usr/src/app/upload`, `Volume=/etc/localtime:/etc/localtime:ro`, health `curl -sf http://127.0.0.1:2283/api/server/ping || exit 1`, `HealthStartPeriod=120s`), `immich-ml` (`ghcr.io/immich-app/immich-machine-learning:{{ service.config.version }}`, `Volume={{ volumes['model-cache'] }}:/cache`, health `curl -sf http://127.0.0.1:3003/ping`), pod publishes 2283. No dump timer (Immich does its own). Delete the untracked `immich/`. Commit "Migrate immich to the platform as a pod".

---

### Task 6: nextcloud pod

`service.yml`:

```yaml
name: nextcloud
routes:
  - { domain: nextcloud, port: 8280 }
  - { domain: collabora, port: 9980 }
volumes:
  html: { class: pool }
  database: { class: fast }
  backups: { class: fast }
config:
  database_name: nextcloud
  database_user: oc_jlk
  version: 33-fpm-alpine
secrets: [database_password, collabora_username, collabora_password]
host_secrets:
  kopia_password: kopia-clients.nextcloud
hooks:
  after_change:
    - {
        container: nextcloud-app,
        user: www-data,
        command: 'php occ maintenance:mode --off'
      }
    - {
        container: nextcloud-app,
        user: www-data,
        command: 'php occ db:add-missing-columns'
      }
    - {
        container: nextcloud-app,
        user: www-data,
        command: 'php occ db:add-missing-indices'
      }
    - {
        container: nextcloud-app,
        user: www-data,
        command: 'php occ db:add-missing-primary-keys'
      }
    - {
        container: nextcloud-app,
        user: www-data,
        command: 'php occ maintenance:repair --include-expensive'
      }
backup:
  paths: [html, backups]
  schedule: '03:00'
  retention: { latest: 3, daily: 7, weekly: 4, monthly: 12, annual: 3 }
```

Pod publishes `127.0.0.1:8280:80` and `127.0.0.1:9980:9980`. Parts: `nextcloud-database` (postgres 17-alpine, auto), `nextcloud-cache` (redis, auto), `nextcloud-app` (`docker.io/library/nextcloud:{{ service.config.version }}`, `POSTGRES_HOST=127.0.0.1`, `POSTGRES_PASSWORD_FILE`, `REDIS_HOST=127.0.0.1`, `TRUSTED_PROXIES` from config, `NEXTCLOUD_TRUSTED_DOMAINS={{ routes[0].fqdn }}`, `Volume={{ volumes.html }}:/var/www/html`, the two config files from `config/` mounted read-only at their php paths, health: the fpm image serves no HTTP, so `HealthCmd=sh -c 'exec 3<>/dev/tcp/127.0.0.1/9000'` checks that php-fpm listens), `nextcloud-nginx` (`docker.io/library/nginx:alpine`, auto, `config/nginx.conf` at `/etc/nginx/nginx.conf:ro` with `fastcgi_pass 127.0.0.1:9000` instead of `nextcloud:9000`, `Volume={{ volumes.html }}:/var/www/html:ro`, `Tmpfs=/tmp`, health `curl -sf http://127.0.0.1/status.php || exit 1`), `nextcloud-collabora` (`docker.io/collabora/code:latest`, auto, `Secret=collabora_username,type=env,target=username`, `Secret=collabora_password,type=env,target=password`, `dictionaries=de_DE en_GB en_US`, `aliasgroup1=https://{{ routes[0].fqdn }}:443`, `extra_params=--o:ssl.enable=false --o:ssl.termination=true`, health `curl -sf http://127.0.0.1:9980/hosting/discovery`), `nextcloud-cron.timer.j2` (`OnCalendar=*:0/5`) + `nextcloud-cron.service.j2` (`podman exec -u www-data nextcloud-app php -f /var/www/html/cron.php`), dump timer/service. The collabora route needs `route.wildcard_cert` false and the standard loadBalancer; the nextcloud route as well; the old compose used `certresolver: letsencrypt` on collabora, superseded by the wildcard.

Verification: both routes answer (nextcloud `/status.php` JSON with `installed`; collabora `/hosting/discovery` 200), hooks ran after the first converge (`occ status` shows `maintenance: false`), cron timer enabled, dump file, backup snapshot. First-run install: the image installs Nextcloud on first start only with admin credentials or via the web installer; for the test, set `NEXTCLOUD_ADMIN_USER`/`NEXTCLOUD_ADMIN_PASSWORD` from two extra secrets `admin_user`, `admin_password` (declared; REPLACE_ME on storagebaby where an install already exists and the variables are ignored) so `occ` commands can run. Delete `nextcloud/`. Commit "Migrate nextcloud to the platform as a pod with routes, hooks and cron".

---

### Task 7: Docs, operator steps, cleanup

- README services table gains the four pods; operator steps gain: the `kopia-clients` set and the per-service `REPLACE_ME` values (paperless database password + secret key, nextcloud database password + collabora credentials + admin values, immich database password); data-migration table rows for the four stacks (old docker named volumes live under `/var/lib/docker/volumes/<stack>_<vol>/_data`; the recipe copies them to the new paths and fixes ownership as Phase 2 documented; databases: either copy the data directory with the same password or restore from a dump); a note that Kopia clients register on the next kopia restart after the set changes.
- CLAUDE.md: routes, host_secrets, hooks, timers, backup sidecar, pod conventions, `make` wrappers pod-aware, test-ci host.
- Phase 1 spec §5 pointer to the Phase 3 spec; Phase 3 spec status line.
- `make fmt-check`, `make test-static`, `make test-integration` (test-a) and `MOLECULE_HOST=test-ci make test-integration` green. Commit "Document the Phase 3 pods, backups and operator steps".

---

## Self-review notes

- Spec coverage: §2 keys (T1 tests+role), §3 pods (T3–T6), §4 kopia (T1 sidecar, T2 registration), §5 tests (T1 static, T2 integration, T3 hook case), §6 order, §7 operator items (T7).
- Names consistent: `routes[].fqdn`, `hostname`, `backup_enabled`, `volumes['backup-config']`, `podman-as`, `host_secret_sync`, `timer_units`, `<name>-backup`, `<name>-dump`, `kopia-clients.<service>`, `client_<service>`.
