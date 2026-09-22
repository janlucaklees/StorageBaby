# Podman/Quadlet Pilot (Traefik + Stirling-PDF) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the first two services on the new Podman/Quadlet/per-user pattern — Traefik (migrated) and Stirling-PDF (new) — proving the whole design end to end before touching the rest of the fleet.

**Architecture:** Ansible provisions one dedicated unprivileged Linux user per service (rootless Podman, own subuid/subgid range, lingering `systemd --user` instance). Each service's Quadlet unit files live in its own repo folder and are symlinked directly into that user's `~/.config/containers/systemd/` — no copy step, the repo is the live config. Traefik runs with `Network=host`, keeps discovering the existing Docker fleet via `docker.sock` + labels unchanged, and discovers migrated services via a file provider watching `traefik/dynamic.d/`, populated by the reconciler _copying_ each service's own `traefik/dynamic.yml` into it (a symlink would dangle inside the container's mount namespace — see Task 3).

**Tech Stack:** Ansible (host provisioning), Podman 6.1 + Quadlet (container runtime, already installed), systemd --user (per-service process supervision), Traefik v3 (reverse proxy, dual docker+file providers), Cockpit + cockpit-podman (visibility UI).

**Spec:** `docs/superpowers/specs/2026-08-27-podman-migration-design.md`

## Global Constraints

- Single NAS box, no clustering (spec §3).
- Rootless Podman only; one dedicated system user per service, no shared user across services (spec §2).
- No shared Podman network for service discovery — every migrated service binds its app port to `127.0.0.1:<port>` only, and is reached by Traefik via a file-provider snippet, never via container-IP/label discovery (spec §7). This is a structural limitation of rootless per-user namespaces, not a style choice.
- The still-on-Docker fleet (Jellyfin, Paperless, Nextcloud, Yuzukam, Watchtower, Ofelia, Samba) must remain fully functional throughout this plan. Every task that touches Traefik must end with a full smoke-test of existing routes before being considered done.
  - **One deliberate exception, found by the final review:** `paperless/` and `nextcloud/` derive their trusted-proxy env var from `docker inspect`-ing Traefik's container IP on `traefik_network`. Once Traefik moves to `Network=host` it has no such IP, and its source address as seen by those containers becomes the bridge's _gateway_ IP. Leaving those files untouched is what breaks them on their next restart, so both Makefiles now derive the gateway (`docker network inspect -f '{{ (index .IPAM.Config 0).Gateway }}' traefik_network`) and the var is renamed `TRAEFIK_PROXY_IP` in the Makefiles and their compose files. No running container is touched — only the value computed for the next `make start`.
- Each service folder stays self-contained: Quadlet unit(s), `setup.sh`, `Makefile`, and (if externally reachable) its own `traefik/dynamic.yml` all live inside that service's own directory.
- No placeholders in secrets or config — reuse the real values already present in `stirling-pdf/docker-compose.yml` and `traefik/docker-compose.yml` (image names, ports, volume paths, domains, env vars) when translating to Quadlet.
- Package installs and sysctl changes happen only via the Ansible playbook created in Task 1, never as ad hoc one-off shell commands.

---

### Task 1: Ansible host bootstrap (per-service users + base packages)

**Files:**

- Create: `ansible/ansible.cfg`
- Create: `ansible/inventory.ini`
- Create: `ansible/playbook.yml`
- Create: `ansible/group_vars/all.yml`
- Create: `ansible/roles/host_base/tasks/main.yml`
- Create: `ansible/roles/service_user/tasks/main.yml`

**Interfaces:**

- Produces: a Linux user `svc-<name>` per entry in `service_users` (group_vars list), each with `loginctl enable-linger` active and a subuid/subgid range in `/etc/subuid`/`/etc/subgid`. Membership in the `docker` group is granted **per-service**, via each entry's `docker_group` flag — only `svc-traefik` needs it (to read `docker.sock` in Task 3). The `docker` group is root-equivalent, so handing it to every service user would defeat the point of per-service confinement.
- Produces: `podman`, `cockpit`, `cockpit-podman` installed via `yay`; `net.ipv4.ip_unprivileged_port_start=80` set persistently (required for Traefik, run by an unprivileged user, to bind ports 80/443 in `Network=host` mode — see Task 3).
- Consumed by: Task 2 (`svc-stirling` must exist before its Quadlet unit can run), Task 3 (`svc-traefik` must exist, with `docker` group membership, before Traefik's Quadlet unit can run).

- [ ] **Step 1: Confirm ansible is available, install if not**

```bash
which ansible-playbook || yay -S --needed --noconfirm ansible
```

- [ ] **Step 2: Write the inventory and ansible.cfg**

`ansible/inventory.ini`:

```ini
[nas]
localhost ansible_connection=local
```

`ansible/ansible.cfg`:

```ini
[defaults]
inventory = inventory.ini
host_key_checking = False
```

- [ ] **Step 3: Declare the services this round**

`ansible/group_vars/all.yml`:

```yaml
# One entry per service that gets its own dedicated unprivileged user.
#
# docker_group: whether svc-<name> joins the host `docker` group. Only Traefik
# needs it (to read /var/run/docker.sock and keep discovering the still-on-Docker
# fleet by label). The docker group is effectively root-equivalent, so it is
# granted per-service rather than blanket — handing it to every service user
# would defeat the point of per-service confinement.
service_users:
  - name: traefik
    docker_group: true
  - name: stirling-pdf
    docker_group: false
```

- [ ] **Step 4: Write the host_base role (packages + sysctl)**

`ansible/roles/host_base/tasks/main.yml`:

```yaml
- name: Install podman, cockpit, and cockpit-podman
  community.general.pacman:
    name:
      - podman
      - cockpit
      - cockpit-podman
    state: present
  become: true

- name: Enable and start cockpit socket
  ansible.builtin.systemd:
    name: cockpit.socket
    enabled: true
    state: started
  become: true

- name: Allow unprivileged binding of ports 80+ (rootless Traefik needs 80/443 in host network mode)
  ansible.posix.sysctl:
    name: net.ipv4.ip_unprivileged_port_start
    value: '80'
    sysctl_set: true
    state: present
    reload: true
  become: true

- name: Allow traversal into the repo checkout for service users (needed for Quadlet units, bind mounts, and secrets under this path to be readable by svc-* users, whose home directories are elsewhere)
  ansible.builtin.file:
    path: '{{ item }}'
    state: directory
    mode: 'o+x'
  loop:
    - /home/jlk
    - /home/jlk/Projects
  become: true
```

Note: `community.general` and `ansible.posix` collections are required — install with `ansible-galaxy collection install community.general ansible.posix` as part of this step if not already present.

Note on the traversal fix: found during Task 3's review — Arch's default `HOME_MODE` (0700) means `svc-traefik`/`svc-stirling-pdf` can't traverse into `/home/jlk/Projects/StorageBaby/...` at all by default, which would silently break the Traefik bind mount (Task 3), the Quadlet-unit symlinks (Tasks 2/3), and setup.sh's secret-file reads (Task 3) — none of those failures would show up until actually run on the real host, since this whole plan runs in author-only mode. `o+x` grants traversal only (not listing or read) — least-privilege for "can reach a named path below here," not "can browse the directory." This assumes `jlk`'s home is `/home/jlk`, matching this repo's own path; adjust if the real clone lives elsewhere.

- [ ] **Step 5: Write the service_user role (parameterized, one user per invocation)**

`ansible/roles/service_user/tasks/main.yml`:

```yaml
- name: 'Create service user svc-{{ item.name }}'
  ansible.builtin.user:
    name: 'svc-{{ item.name }}'
    system: true
    shell: /usr/bin/bash
    create_home: true
  become: true

# The docker group is root-equivalent (full access to the Docker socket), so it
# is granted only to the service that actually needs it — svc-traefik, which
# reads docker.sock to keep discovering the still-on-Docker fleet by label.
- name: 'Add svc-{{ item.name }} to the docker group'
  ansible.builtin.user:
    name: 'svc-{{ item.name }}'
    groups: docker
    append: true
  become: true
  when: item.docker_group | default(false)

# ansible.builtin.user with system: true runs `useradd -r`, which does NOT write
# /etc/subuid or /etc/subgid (useradd(8): only `-F/--add-subids` allocates them
# for a system user, and the module has no way to pass that flag). Without a
# subordinate id range, rootless Podman cannot create a user namespace at all,
# so nothing this pilot builds would start.
#
# `usermod --add-subids` picks a free range from SUB_UID_MIN/SUB_UID_COUNT in
# login.defs, so there is no manual range bookkeeping here. It is additive, not
# idempotent, hence the guard below — re-running the playbook must not append a
# second range.
- name: 'Check whether svc-{{ item.name }} already has subordinate id ranges'
  ansible.builtin.shell:
    cmd: "grep -q '^svc-{{ item.name }}:' /etc/subuid && grep -q '^svc-{{ item.name }}:' /etc/subgid"
  become: true
  register: _subid_present
  changed_when: false
  failed_when: false

- name: 'Allocate subordinate uid/gid ranges for svc-{{ item.name }}'
  ansible.builtin.command:
    cmd: 'usermod --add-subids svc-{{ item.name }}'
  become: true
  when: _subid_present.rc != 0

- name: 'Enable lingering for svc-{{ item.name }}'
  ansible.builtin.command:
    cmd: 'loginctl enable-linger svc-{{ item.name }}'
  become: true
  changed_when: true

- name: 'Ensure svc-{{ item.name }} has a systemd --user config dir'
  ansible.builtin.file:
    path: '/home/svc-{{ item.name }}/.config'
    state: directory
    owner: 'svc-{{ item.name }}'
    group: 'svc-{{ item.name }}'
    mode: '0755'
  become: true
```

- [ ] **Step 6: Write the playbook wiring both roles together**

`ansible/playbook.yml`:

```yaml
- name: StorageBaby host bootstrap
  hosts: nas
  tasks:
    - name: Apply host_base role
      ansible.builtin.include_role:
        name: host_base

    - name: Apply service_user role per declared service
      ansible.builtin.include_role:
        name: service_user
      loop: '{{ service_users }}'
      loop_control:
        label: 'svc-{{ item.name }}'
```

- [ ] **Step 7: Run the playbook**

```bash
cd ansible && ansible-playbook playbook.yml
```

Expected: no failures. `docker` group is expected to exist already (Docker is currently installed).

- [ ] **Step 8: Verify the result**

```bash
id svc-traefik && id svc-stirling-pdf
loginctl show-user svc-traefik -p Linger
loginctl show-user svc-stirling-pdf -p Linger
grep '^svc-traefik:' /etc/subuid /etc/subgid
grep '^svc-stirling-pdf:' /etc/subuid /etc/subgid
id -nG svc-traefik
id -nG svc-stirling-pdf
pacman -Q podman cockpit cockpit-podman
sysctl net.ipv4.ip_unprivileged_port_start
systemctl is-active cockpit.socket
stat -c '%a' /home/jlk /home/jlk/Projects
```

Expected: both users exist with `Linger=yes`; each `grep` prints exactly one line from `/etc/subuid` and one from `/etc/subgid` (a second line for the same user means the `--add-subids` idempotency guard is broken); `svc-traefik`'s groups include `docker` and `svc-stirling-pdf`'s do **not**; both packages are installed, the sysctl reads `80`, cockpit.socket is active.

- [ ] **Step 9: Re-run the playbook to confirm idempotency**

```bash
cd ansible && ansible-playbook playbook.yml
```

Expected: `changed=0` for every task except the `enable-linger` command (which has no idempotent check in this simple form — acceptable for a two-user pilot; revisit with a proper check when the fleet grows in a later phase).

- [ ] **Step 10: Commit**

```bash
git add ansible/
git commit -m "Add Ansible host bootstrap: per-service users, cockpit, unprivileged low ports"
```

---

### Task 2: Stirling-PDF as a new Quadlet service (standalone, not yet routed through Traefik)

Built and verified in isolation first — lowest blast radius, proves the Quadlet+per-user pattern works at all before Task 3 touches the shared, critical Traefik.

**Files:**

- Create: `stirling-pdf/quadlet/stirling-pdf.container`
- Create: `stirling-pdf/setup.sh`
- Modify: `stirling-pdf/Makefile`
- Delete: `stirling-pdf/docker-compose.yml` (never run; superseded by the Quadlet unit)
- Create: `PORTS.md`

**Interfaces:**

- Produces: `svc-stirling-pdf` running Stirling-PDF, reachable at `127.0.0.1:8180` on the host. Port `8180` is the value Task 3/4 (Traefik's dynamic config) must reference.
- Consumes: `svc-stirling-pdf` user from Task 1.

- [ ] **Step 1: Record the port assignment**

`PORTS.md` (repo root):

```markdown
# Loopback port registry

Every migrated service binds its app port to 127.0.0.1 only (see
docs/superpowers/specs/2026-08-27-podman-migration-design.md §7).
Ports are assigned here to avoid collisions.

| Port | Bind      | Service      | Notes                                                     |
| ---- | --------- | ------------ | --------------------------------------------------------- |
| 80   | all       | traefik      | HTTP entrypoint, redirects to 443                         |
| 443  | all       | traefik      | HTTPS entrypoint                                          |
| 8080 | 127.0.0.1 | traefik      | Internal `traefik` entrypoint (`/ping`), loopback-only    |
| 8180 | 127.0.0.1 | stirling-pdf |                                                           |
| 9090 | all       | cockpit      | `cockpit.socket`, enabled by the Ansible `host_base` role |
```

(Traefik's and cockpit's rows land here once Tasks 1/3 exist; Traefik runs with `Network=host`, so those are host binds rather than container port publications.)

- [ ] **Step 2: Write the Quadlet unit**

`stirling-pdf/quadlet/stirling-pdf.container`:

```ini
[Unit]
Description=Stirling PDF
# No network-online.target here: systemd --user instances have no such target
# (it is a system-manager concept), so the line would be a silent no-op.
# Quadlet already injects podman-user-wait-network-online.service for user
# units — see podman-systemd.unit(5).

[Container]
Image=docker.stirlingpdf.com/stirlingtools/stirling-pdf:latest
ContainerName=stirling-pdf
AutoUpdate=registry
PublishPort=127.0.0.1:8180:8080
Environment=TZ=Europe/Berlin
Environment=SECURITY_ENABLELOGIN=true
Volume=/pool/apps/stirling-pdf/volumes/configs:/configs
Volume=/pool/apps/stirling-pdf/volumes/logs:/logs
Volume=/pool/apps/stirling-pdf/volumes/pipeline:/pipeline
Volume=/pool/apps/stirling-pdf/volumes/tessdata:/usr/share/tessdata
HealthCmd=curl -f http://localhost:8080 || exit 1
HealthOnFailure=kill

[Service]
Restart=on-failure

[Install]
WantedBy=default.target
```

- [ ] **Step 3: Write setup.sh (directories + symlink, one-time)**

`stirling-pdf/setup.sh`:

```bash
#!/bin/bash
set -euo pipefail

SERVICE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER=svc-stirling-pdf

# Create and own the bind-mount directories on the pool
for dir in configs logs pipeline tessdata; do
	sudo mkdir -p "/pool/apps/stirling-pdf/volumes/${dir}"
done
sudo chown -R "${USER}:${USER}" /pool/apps/stirling-pdf/volumes

# Symlink the Quadlet unit directory into the service user's systemd search path
sudo -u "${USER}" mkdir -p "/home/${USER}/.config/containers"
sudo -u "${USER}" ln -sfn "${SERVICE_ROOT}/quadlet" "/home/${USER}/.config/containers/systemd"

# Reload that user's systemd instance so it picks up the new unit
sudo -u "${USER}" env XDG_RUNTIME_DIR="/run/user/$(id -u ${USER})" systemctl --user daemon-reload

# AutoUpdate=registry on the Quadlet unit only labels the container; the timer
# that actually polls the registry is a static shipped unit and is not activated
# by the label's presence. Enable it explicitly.
sudo -u "${USER}" env XDG_RUNTIME_DIR="/run/user/$(id -u ${USER})" systemctl --user enable --now podman-auto-update.timer
```

Commit this with the executable bit set (`chmod +x stirling-pdf/setup.sh` before Step 9), matching `traefik/setup.sh` and the rest of the repo's scripts.

- [ ] **Step 4: Run setup.sh**

```bash
chmod +x stirling-pdf/setup.sh
cd stirling-pdf && ./setup.sh
```

- [ ] **Step 5: Rewrite the Makefile to drive the per-user systemd instance**

`stirling-pdf/Makefile`:

```makefile
USER := svc-stirling-pdf
RUN_AS := sudo -u $(USER) env XDG_RUNTIME_DIR=/run/user/$(shell id -u $(USER))

.PHONY: start
start:
	$(RUN_AS) systemctl --user start stirling-pdf.service

.PHONY: ps
ps:
	$(RUN_AS) systemctl --user status stirling-pdf.service

.PHONY: logs
logs:
	$(RUN_AS) journalctl --user-unit stirling-pdf.service -f

.PHONY: stop
stop:
	$(RUN_AS) systemctl --user stop stirling-pdf.service

.PHONY: remove
remove:
	$(RUN_AS) systemctl --user stop stirling-pdf.service

.PHONY: clean
clean:
	$(RUN_AS) systemctl --user stop stirling-pdf.service
	sudo rm -rf /pool/apps/stirling-pdf/volumes
```

- [ ] **Step 6: Start it and verify**

```bash
cd stirling-pdf && make start
sleep 5
make ps
curl -f http://127.0.0.1:8180
```

Expected: `make ps` shows `active (running)`; the `curl` returns Stirling-PDF's HTML.

- [ ] **Step 7: Verify auto-update wiring**

```bash
sudo -u svc-stirling-pdf env XDG_RUNTIME_DIR=/run/user/$(id -u svc-stirling-pdf) systemctl --user list-timers | grep podman-auto-update
```

Expected: `podman-auto-update.timer` listed and active. `AutoUpdate=registry` only _labels_ the container — it does not activate the timer, which ships as a static unit; `setup.sh` (Step 3) is what enables it.

- [ ] **Step 8: Remove the superseded compose file**

```bash
git rm stirling-pdf/docker-compose.yml
```

- [ ] **Step 9: Commit**

```bash
git add stirling-pdf/ PORTS.md
git commit -m "Add Stirling-PDF as a Quadlet service under svc-stirling-pdf"
```

---

### Task 3: Migrate Traefik to Podman/Quadlet (existing fleet untouched)

Highest-risk task in this plan — Traefik fronts every currently-running service. Structured so the old Docker Traefik can be restored in under a minute if anything doesn't check out.

**Files:**

- Create: `traefik/quadlet/traefik.container`
- Create: `traefik/dynamic.d/traefik-dashboard.yml`
- Create: `traefik/setup.sh`
- Modify: `traefik/Makefile`
- Modify: `stirling-pdf/setup.sh` (fixes a permission bug discovered during this task's review — see Step 2 note)
- Do not modify: `traefik/docker-compose.yml` (kept as the rollback path for this task; removed only in a later phase once this is proven stable)

**Interfaces:**

- Produces: `svc-traefik` running Traefik in `Network=host` mode, bound to ports 80/443, discovering the existing Docker fleet via `docker.sock` + labels (unchanged) and discovering migrated services via `providers.file.directory` watching `traefik/dynamic.d/` — a real repo directory, bind-mounted directly into the container (not reached through a symlink; see the note after Step 1).
- Consumes: `svc-traefik` user + `docker` group membership from Task 1.

- [ ] **Step 1: Write the Quadlet unit**

`traefik/quadlet/traefik.container`:

```ini
[Unit]
Description=Traefik reverse proxy
# No network-online.target here: systemd --user instances have no such target
# (it is a system-manager concept), so the lines would be a silent no-op.
# Quadlet already injects podman-user-wait-network-online.service for user
# units — see podman-systemd.unit(5).

[Container]
Image=docker.io/library/traefik:latest
ContainerName=traefik
Network=host
GroupAdd=keep-groups
Exec=--api --ping=true --log.level=DEBUG \
  --providers.docker=true --providers.docker.endpoint=unix:///var/run/docker.sock --providers.docker.exposedbydefault=false --providers.docker.network=traefik_network \
  --providers.file.directory=/etc/traefik/dynamic.d --providers.file.watch=true \
  --entrypoints.web.address=:80 --entrypoints.websecure.address=:443 \
  --entrypoints.traefik.address=127.0.0.1:8080 \
  --entrypoints.web.http.redirections.entrypoint.to=websecure --entrypoints.web.http.redirections.entrypoint.scheme=https \
  --certificatesresolvers.porkbun.acme.email=email@janlucaklees.de \
  --certificatesresolvers.porkbun.acme.storage=/letsencrypt/acme.json \
  --certificatesresolvers.porkbun.acme.dnschallenge.provider=porkbun \
  --certificatesresolvers.porkbun.acme.dnschallenge.resolvers=1.1.1.1:53,8.8.8.8:53
Environment=TZ=Europe/Berlin
Environment=PORKBUN_API_KEY_FILE=/run/secrets/porkbun_api_key
Environment=PORKBUN_SECRET_API_KEY_FILE=/run/secrets/porkbun_secret_api_key
Secret=porkbun_api_key
Secret=porkbun_secret_api_key
AutoUpdate=registry
HealthCmd=wget --spider -q http://localhost:8080/ping || exit 1
HealthOnFailure=kill
Volume=/var/run/docker.sock:/var/run/docker.sock:ro
Volume=/pool/apps/traefik/volumes/letsencrypt:/letsencrypt
Volume=/home/jlk/Projects/StorageBaby/traefik/dynamic.d:/etc/traefik/dynamic.d:ro

[Service]
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

Two corrections from an earlier draft of this task, caught in review — recorded here so the reasoning travels with the file:

1. **`GroupAdd=keep-groups` is required, not optional.** `docker.sock` is `root:docker` mode 0660. Task 1 puts `svc-traefik` in the `docker` group, but rootless Podman drops the invoking user's supplementary groups by default when entering the container's user namespace — without this line, Traefik's Docker provider silently fails to connect and the entire existing fleet loses its routes at cutover.
2. **The dashboard route is not expressed via a `Label=` on this unit.** Traefik's Docker provider only ever sees containers the Docker daemon knows about — a Podman container never is, regardless of `Network=` mode. A label here would be silently inert: the dashboard would 404, and worse, the wildcard cert would stop renewing (`traefik-ui-secure` is the only router carrying `certresolver`+`domains`, and it's what triggers ACME issuance — see `traefik/README.md`'s "ACME is router-driven" section). It's expressed as a first-party file-provider snippet instead (Step 1b below) — Traefik's own dynamic config, not a per-service contribution, so it needs no copy step.
3. **The implicit `traefik` entrypoint is pinned to loopback.** Under `Network=host` it would otherwise bind `:8080` on every host interface, where under Docker bridge networking it was container-network-local. `--entrypoints.traefik.address=127.0.0.1:8080` keeps `/ping` (and the healthcheck below, which is in the same network namespace) working unchanged while taking it off the LAN.
4. **`traefik/dynamic.d/` is bind-mounted directly, at its real repo path** (`/home/jlk/Projects/StorageBaby/...`, the actual clone location on the NAS — same assumption Task 5's reconciler makes), not through a per-user symlink. A symlink placed inside a bind-mounted directory is resolved _inside the container's mount namespace_ — where nothing outside that one mounted directory exists — so a symlink pointing at another service's folder elsewhere in the repo would dangle. Mounting the real directory sidesteps that; see Task 5 for how other services' snippets land in it (copied by the reconciler, not symlinked).

- [ ] **Step 1b: Write the dashboard's own dynamic config snippet**

`traefik/dynamic.d/traefik-dashboard.yml`:

```yaml
http:
  routers:
    traefik-ui-secure:
      rule: 'Host(`traefik.home.klees.io`)'
      entrypoints:
        - websecure
      tls:
        certResolver: porkbun
        domains:
          - main: 'home.klees.io'
            sans:
              - '*.home.klees.io'
      service: api@internal
```

- [ ] **Step 2: Write setup.sh**

`traefik/setup.sh`:

```bash
#!/bin/bash
set -euo pipefail

SERVICE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER=svc-traefik

sudo mkdir -p /pool/apps/traefik/volumes/letsencrypt
sudo chown -R "${USER}:${USER}" /pool/apps/traefik/volumes

# Podman secrets, created once from the existing secret files.
# Idempotent: skip creation if the secret already exists, so a re-run
# doesn't abort under set -e partway through setup.
#
# The secret material is read by root and piped in on stdin rather than handed
# to podman as a path: the *.secret files are mode 0600 and owned by jlk, so
# svc-traefik could not open them itself (the host_base role's `o+x` grant is
# directory traversal only, not file readability). Piping is also the better
# least-privilege shape — svc-traefik never needs read access to jlk's files.
if ! sudo -u "${USER}" env XDG_RUNTIME_DIR="/run/user/$(id -u ${USER})" podman secret exists porkbun_api_key; then
	sudo cat "${SERVICE_ROOT}/secrets/porkbun_api_key.secret" \
		| sudo -u "${USER}" env XDG_RUNTIME_DIR="/run/user/$(id -u ${USER})" \
			podman secret create porkbun_api_key -
fi
if ! sudo -u "${USER}" env XDG_RUNTIME_DIR="/run/user/$(id -u ${USER})" podman secret exists porkbun_secret_api_key; then
	sudo cat "${SERVICE_ROOT}/secrets/porkbun_secret_api_key.secret" \
		| sudo -u "${USER}" env XDG_RUNTIME_DIR="/run/user/$(id -u ${USER})" \
			podman secret create porkbun_secret_api_key -
fi

# Quadlet unit directory — created AS svc-traefik throughout, so every
# directory in the chain is actually writable by the user that needs to
# symlink into it (a `sudo mkdir` here, owned by root, would make the
# following `ln` fail with permission denied).
sudo -u "${USER}" mkdir -p "/home/${USER}/.config/containers"
sudo -u "${USER}" ln -sfn "${SERVICE_ROOT}/quadlet" "/home/${USER}/.config/containers/systemd"

sudo -u "${USER}" env XDG_RUNTIME_DIR="/run/user/$(id -u ${USER})" systemctl --user daemon-reload

# AutoUpdate=registry on the Quadlet unit only labels the container; the timer
# that actually polls the registry is a static shipped unit and is not activated
# by the label's presence. Enable it explicitly.
sudo -u "${USER}" env XDG_RUNTIME_DIR="/run/user/$(id -u ${USER})" systemctl --user enable --now podman-auto-update.timer
```

`traefik/dynamic.d/` no longer needs a setup.sh step of its own — it's a plain repo directory, bind-mounted directly by the Quadlet unit at its real path.

The same `sudo mkdir` (root-owned) → `sudo -u svc-stirling-pdf ln -sfn` EACCES bug existed in Task 2's `stirling-pdf/setup.sh`; that task's Step 3 block above now carries the corrected `sudo -u "${USER}" mkdir -p` form, and this task's file list keeps `stirling-pdf/setup.sh` because the fix was applied here.

- [ ] **Step 2b: Commit setup.sh as executable**

```bash
chmod +x traefik/setup.sh
```

Commit this with the executable bit set (Step 9) rather than relying on a separate chmod step at run time.

- [ ] **Step 3: Run setup.sh**

```bash
cd traefik && ./setup.sh
```

- [ ] **Step 4: Snapshot the existing letsencrypt volume so the new Traefik doesn't re-issue certs**

Confirm the actual Docker volume name first — Compose derives it from the project (directory) name by default, expected to be `traefik_letsencrypt`, but don't assume:

```bash
docker volume ls | grep -i letsencrypt
```

Use whatever name that prints in place of `traefik_letsencrypt` below:

```bash
docker run --rm -v traefik_letsencrypt:/from -v /pool/apps/traefik/volumes/letsencrypt:/to alpine sh -c "cp -a /from/. /to/"
sudo chown -R svc-traefik:svc-traefik /pool/apps/traefik/volumes/letsencrypt
```

Expected: `/pool/apps/traefik/volumes/letsencrypt/acme.json` exists with the same content as the running Docker volume, owned by `svc-traefik`.

- [ ] **Step 5: Stop the old Docker Traefik, start the new one**

```bash
cd traefik && docker compose stop
sudo -u svc-traefik env XDG_RUNTIME_DIR=/run/user/$(id -u svc-traefik) systemctl --user start traefik.service
sleep 5
sudo -u svc-traefik env XDG_RUNTIME_DIR=/run/user/$(id -u svc-traefik) systemctl --user status traefik.service
```

Expected: `active (running)`.

- [ ] **Step 6: Full smoke test of the existing fleet — do not proceed until every line passes**

```bash
curl -Ik https://traefik.home.klees.io | head -1
curl -Ik https://jellyfin.home.klees.io | head -1
curl -Ik https://$(grep -oP '(?<=DOMAIN=).*' ../paperless/.env.sh 2> /dev/null || echo paperless.home.klees.io) | head -1
curl -Ik https://yuzukam.home.klees.io | head -1
```

Expected: every line returns `HTTP/2 200` (or a sane redirect/login response) — a non-200/connection error on any existing route means STOP and roll back (Step 7 shows how), not press on.

- [ ] **Step 7: Rollback path (only if Step 6 fails)**

```bash
sudo -u svc-traefik env XDG_RUNTIME_DIR=/run/user/$(id -u svc-traefik) systemctl --user stop traefik.service
cd traefik && docker compose up -d
```

Then debug the Quadlet unit before retrying Step 5 — do not leave both Traefiks running at once (port 80/443 conflict).

- [ ] **Step 8: Rewrite the Makefile**

`traefik/Makefile`:

```makefile
USER := svc-traefik
RUN_AS := sudo -u $(USER) env XDG_RUNTIME_DIR=/run/user/$(shell id -u $(USER))

.PHONY: start
start:
	$(RUN_AS) systemctl --user start traefik.service

.PHONY: ps
ps:
	$(RUN_AS) systemctl --user status traefik.service

.PHONY: logs
logs:
	$(RUN_AS) journalctl --user-unit traefik.service -f

.PHONY: stop
stop:
	$(RUN_AS) systemctl --user stop traefik.service

.PHONY: remove
remove:
	$(RUN_AS) systemctl --user stop traefik.service

# Wipes everything under volumes/ except letsencrypt/ — destroying acme.json
# would force a full re-issuance against Let's Encrypt's rate limits on the
# next start.
.PHONY: clean
clean:
	$(RUN_AS) systemctl --user stop traefik.service
	find /pool/apps/traefik/volumes -mindepth 1 -maxdepth 1 ! -name letsencrypt -exec sudo rm -rf {} +
```

- [ ] **Step 9: Commit**

```bash
git add traefik/quadlet traefik/dynamic.d traefik/setup.sh traefik/Makefile stirling-pdf/setup.sh
git commit -m "Migrate Traefik to Podman/Quadlet under svc-traefik, existing fleet untouched"
```

---

### Task 4: Wire Stirling-PDF into the new Traefik and verify end to end

**Files:**

- Create: `stirling-pdf/traefik/dynamic.yml` (the authored source, committed)
- Generate: `traefik/dynamic.d/stirling-pdf.yml` (a copy of the above — see Step 2; **not** committed, see Step 2b)
- Modify: `.gitignore`

**Interfaces:**

- Consumes: `svc-stirling-pdf` running on `127.0.0.1:8180` (Task 2), `svc-traefik` running with `providers.file.directory` watching `traefik/dynamic.d/` bind-mounted directly from the repo (Task 3).

- [ ] **Step 1: Write the dynamic config snippet**

`stirling-pdf/traefik/dynamic.yml`:

```yaml
http:
  routers:
    stirling-pdf-secure:
      rule: 'Host(`stirling.home.klees.io`)'
      entrypoints:
        - websecure
      tls: true
      service: stirling-pdf

  services:
    stirling-pdf:
      loadBalancer:
        servers:
          - url: 'http://127.0.0.1:8180'
```

- [ ] **Step 2: Copy it into Traefik's watched directory**

Copied, not symlinked — Task 3's note explains why (a symlink here would dangle inside the container's mount namespace). `stirling-pdf/traefik/dynamic.yml` stays the authored source (this is what Task 5's reconciler watches for future edits); `traefik/dynamic.d/stirling-pdf.yml` is the live copy Traefik actually reads, regenerated by the reconciler and deliberately untracked (Step 2b).

```bash
cp stirling-pdf/traefik/dynamic.yml traefik/dynamic.d/stirling-pdf.yml
```

- [ ] **Step 2b: Keep the copy out of git**

The copy is _derived state_, not tracked state. If it were committed as well, the first authored change without a matching manual re-copy would leave a locally-modified tracked file, and Task 5's `git pull --ff-only` would fail permanently ("local changes would be overwritten") — silently breaking reconciliation for both services, with nothing but a quietly failing timer to signal it.

Append to the repo-root `.gitignore`:

```
# Traefik's file-provider directory is derived state: reconciler/reconcile.sh
# copies each migrated service's own <service>/traefik/dynamic.yml in here on
# change. Tracking those copies too would leave the checkout permanently dirty
# the first time an authored source changes without a matching manual re-copy,
# which would wedge the reconciler's `git pull --ff-only` for good.
# traefik-dashboard.yml is the exception — it is Traefik's own first-party
# snippet, authored directly in this directory, not copied from anywhere.
traefik/dynamic.d/*.yml
!traefik/dynamic.d/traefik-dashboard.yml
```

Verify the exception holds (the dashboard snippet must stay tracked):

```bash
git check-ignore -v traefik/dynamic.d/stirling-pdf.yml traefik/dynamic.d/traefik-dashboard.yml
```

Expected: one line, for `stirling-pdf.yml` only.

- [ ] **Step 3: Verify Traefik picked it up (file provider watches automatically, no restart needed)**

```bash
sleep 2
curl -Ik https://stirling.home.klees.io | head -1
```

Expected: `HTTP/2 200`.

- [ ] **Step 4: Re-run the full existing-fleet smoke test from Task 3 Step 6**

Confirms adding a file-provider route didn't disturb docker-provider routing.

- [ ] **Step 5: Commit**

```bash
git add stirling-pdf/traefik .gitignore
git commit -m "Route stirling.home.klees.io through Traefik's file provider"
```

---

### Task 5: Minimal reconciler (git pull → restart what changed)

Closes a gap the first draft of this plan left open: spec §8 names the reconciler as a distinct piece of the design (the actual "edit a file, push, it's live" mechanism), and it wasn't otherwise covered by Tasks 1-4, which only bring services up manually via `setup.sh`/`make start`. Scoped here to exactly the two services this pilot has.

**Files:**

- Create: `reconciler/reconcile.sh`
- Create: `reconciler/storagebaby-reconcile.service`
- Create: `reconciler/storagebaby-reconcile.timer`

**Interfaces:**

- Consumes: the repo checkout path (assumed to be `/home/jlk/Projects/StorageBaby`, the actual clone location on the NAS), `svc-traefik` and `svc-stirling-pdf` from Task 1.
- Produces: nothing consumed by other tasks — this is the last piece of the pilot.

- [ ] **Step 1: Write the reconcile script**

`reconciler/reconcile.sh`:

```bash
#!/bin/bash
set -euo pipefail

REPO_DIR="/home/jlk/Projects/StorageBaby"

# This script runs as root (systemd *system* service), because it has to reach
# into several different service users' own `systemctl --user` instances.
# Git, however, must NOT run as root here:
#   - the checkout is owned by `jlk`, and git's safe.directory ownership check
#     aborts with "detected dubious ownership" on the very first command;
#   - a root-run `git pull` would write root-owned objects into jlk's
#     .git/objects, breaking jlk's own future git operations;
#   - if the remote is SSH, root has no credentials for it.
# So every git invocation is dropped to `jlk`, and only the systemctl calls
# stay root. All paths below are absolute — this script never `cd`s.
#
# -H is deliberate: whether plain `sudo -u` resets HOME depends on sudoers
# (env_reset / always_set_home), and git needs jlk's own ~/.gitconfig and
# ~/.ssh, not root's, for the pull to authenticate and behave predictably.
GIT="sudo -H -u jlk git -C ${REPO_DIR}"

BEFORE=$($GIT rev-parse HEAD)
$GIT pull --ff-only
AFTER=$($GIT rev-parse HEAD)

if [ "$BEFORE" = "$AFTER" ]; then
	echo "No changes."
	exit 0
fi

CHANGED=$($GIT diff --name-only "$BEFORE" "$AFTER")
echo "Changed files:"
echo "$CHANGED"

restart_unit() {
	local user=$1 unit=$2
	echo "Restarting ${unit} as ${user}..."
	sudo -u "$user" env XDG_RUNTIME_DIR="/run/user/$(id -u "$user")" systemctl --user daemon-reload
	sudo -u "$user" env XDG_RUNTIME_DIR="/run/user/$(id -u "$user")" systemctl --user restart "$unit"
}

if echo "$CHANGED" | grep -q '^traefik/quadlet/'; then
	restart_unit svc-traefik traefik.service
fi

if echo "$CHANGED" | grep -q '^stirling-pdf/quadlet/'; then
	restart_unit svc-stirling-pdf stirling-pdf.service
fi

# Per-service traefik/dynamic.yml files are the authored source but are not
# what Traefik reads directly (see Task 3/4) — copy each changed one into
# traefik/dynamic.d/ under its service name. traefik/dynamic.d/* itself
# needs no action: it's bind-mounted straight into Traefik, which
# hot-reloads on file change (--providers.file.watch=true).
#
# The copies written here are intentionally gitignored (see the repo-root
# .gitignore): they are generated state, and tracking them would leave the
# checkout permanently dirty and wedge the `git pull --ff-only` above the first
# time an authored source changes.
if echo "$CHANGED" | grep -q '^stirling-pdf/traefik/dynamic\.yml$'; then
	echo "Copying stirling-pdf's dynamic config..."
	cp "${REPO_DIR}/stirling-pdf/traefik/dynamic.yml" "${REPO_DIR}/traefik/dynamic.d/stirling-pdf.yml"
fi
```

- [ ] **Step 2: Write the systemd system service + timer**

`reconciler/storagebaby-reconcile.service`:

```ini
[Unit]
Description=StorageBaby reconciler (git pull, restart changed Quadlet units)

[Service]
Type=oneshot
ExecStart=/usr/local/bin/storagebaby-reconcile.sh
```

`reconciler/storagebaby-reconcile.timer`:

```ini
[Unit]
Description=Run the StorageBaby reconciler periodically

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Install it**

```bash
chmod +x reconciler/reconcile.sh
sudo cp reconciler/reconcile.sh /usr/local/bin/storagebaby-reconcile.sh
sudo cp reconciler/storagebaby-reconcile.service /etc/systemd/system/
sudo cp reconciler/storagebaby-reconcile.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now storagebaby-reconcile.timer
```

- [ ] **Step 4: Verify it end to end**

```bash
# Change something trivial and push it (a comment in the Quadlet unit is enough)
sudo systemctl start storagebaby-reconcile.service # run it once, on demand, instead of waiting for the timer
sudo journalctl -u storagebaby-reconcile.service -n 20
```

Expected: log shows the file as changed and the corresponding unit restarted; `systemctl --user status <unit>` for the affected service shows a recent restart timestamp.

- [ ] **Step 5: Commit**

```bash
git add reconciler/
git commit -m "Add minimal git-pull reconciler for traefik and stirling-pdf"
```

## Explicitly out of scope for this plan

Per spec §12: storage-maintenance hook rewrites, Jellyfin/Yuzukam/Nextcloud/Paperless migration, Watchtower/Ofelia retirement, and deleting `manage.sh`. These are scoped as a separate plan once this pilot's learnings are captured.
