# Phase 1: Platform Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the git-driven Podman platform end to end with Traefik as the first service: repo layout, devtools image, static and integration test suites, host bootstrap, pull-based deploy, host_base and service roles.

**Architecture:** Every host is converged by one Ansible playbook run as root, either by `ansible-pull` on the host (production) or by Molecule against Arch KVM virtual machines on the host's libvirt (tests). Services are folders under `hosts/<host>/services/` or `hosts/shared/services/`; the generic `service` role turns a folder into a rootless Podman user, resolved volumes, Podman secrets, rendered Quadlet units and a Traefik route. Nothing is installed on the operator's machine; all tooling runs in the devtools Docker image.

**Tech Stack:** Arch Linux targets, Podman 6 + Quadlet, systemd --user, Ansible (full `ansible` package on hosts, `ansible-core` in devtools), sops + age, Molecule with libvirt/QEMU VMs (generic driver, custom create/destroy), pytest + pytest-testinfra, Traefik v3, prettier.

**Spec:** `docs/superpowers/specs/2026-09-21-gitops-podman-platform-design.md`

## Global Constraints

- Rootless Podman only. One system user `svc-<name>` per service. No container runs as root on the host, no container gets a socket.
- Every migrated service binds only `127.0.0.1:<port>`; Traefik on `Network=host` is the only way in. Ports are declared in `service.yml` and unique per host.
- Placement is the folder: `hosts/<host>/services/<name>/` or `hosts/shared/services/<name>/`. No role, template or task may contain a hostname.
- Secrets: sops + age at rest, Podman `file`-driver secrets at runtime. CI never holds a private key. Test hosts get generated secrets under `hosts/test-*/secrets/` (gitignored). Test hosts are libvirt VMs; never a privileged container.
- Volumes: resolved from `storage_roots` classes `pool` and `fast` in `hosts/<host>/host.yml`, optional `volume_overrides`. No Podman named volumes.
- All commands run through the root `Makefile` in the devtools image. Never install packages on the operator machine. Never run `docker`/`podman` commands against the operator machine outside `make` targets.
- Tests are the verification. Every task ends with `make test-static` green, and from Task 5 on with `make test-integration` green. No manual poking that is not captured as a test.
- Deviation from the spec, agreed during planning: test hosts live at `hosts/test-a/` (real host folders, so the deploy path is production-identical), and a host may override a service's secrets with `hosts/<host>/secrets/<service>.sops.yaml`. Tests use that override for every placed service.
- Commits: one commit per task, on branch `podman-platform`, only if the operator has granted standing permission for this branch. Otherwise stop at each commit step and ask.

---

### Task 1: Devtools image and Makefile test targets

**Files:**

- Modify: `devtools/Dockerfile`
- Create: `devtools/requirements.txt`, `devtools/requirements.yml`
- Modify: `Makefile`
- Modify: `.gitignore`, `.prettierignore`
- Create: `tests/static/test_smoke.py`

**Interfaces:**

- Produces: `make devtools` (build image), `make test-static`, `make test-integration [SCENARIO=test-ci]`, `make molecule CMD=<subcommand> [SCENARIO=...]`, `make test`, `make sops FILE=<path>` (edit a sops file with the operator key), `make devtools-shell`.
- Produces: image `storagebaby-devtools` with prettier, python venv (`ansible-core`, `molecule`, `molecule-plugins[docker]`, `pytest`, `pytest-testinfra`, `ansible-lint`, `yamllint`, `pyyaml`), `podman` (for `/usr/lib/podman/quadlet`), `sops`, `age`, `git`, `openssh`, and Ansible collections `community.docker`, `community.general`, `ansible.posix`.

- [ ] **Step 1: Write the smoke test**

`tests/static/test_smoke.py`:

```python
import shutil
import subprocess


def test_tooling_present():
    for tool in ["ansible-playbook", "molecule", "sops", "age", "age-keygen", "prettier", "git"]:
        assert shutil.which(tool), f"{tool} missing from devtools image"
    assert shutil.which("quadlet", path="/usr/lib/podman"), "quadlet generator missing"


def test_ansible_collections_present():
    out = subprocess.run(["ansible-galaxy", "collection", "list"], capture_output=True, text=True, check=True).stdout
    for coll in ["community.docker", "community.general", "ansible.posix"]:
        assert coll in out, f"{coll} not installed"
```

- [ ] **Step 2: Write the image**

`devtools/requirements.txt`:

```
ansible-core
molecule
molecule-plugins[docker]
pytest
pytest-testinfra
ansible-lint
yamllint
pyyaml
```

`devtools/requirements.yml`:

```yaml
collections:
  - name: community.docker
  - name: community.general
  - name: ansible.posix
```

`devtools/Dockerfile`:

```dockerfile
FROM docker.io/library/archlinux:base

RUN pacman -Syu --noconfirm --needed \
	python python-pip nodejs npm git openssh \
	podman sops age \
	&& pacman -Scc --noconfirm

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:/opt/devtools/node_modules/.bin:${PATH}"

WORKDIR /opt/devtools
COPY requirements.txt requirements.yml ./
RUN pip install --no-cache-dir -r requirements.txt \
	&& ansible-galaxy collection install -r requirements.yml

COPY package.json package-lock.json ./
RUN npm ci

WORKDIR /repo
```

- [ ] **Step 3: Write the Makefile**

Replace `Makefile` (keep `pull`, `push`, `install-hooks` as they are):

```make
include .env

DEVTOOLS_IMAGE := storagebaby-devtools
SCENARIO ?= test-ci
DEVTOOLS_RUN := docker run --rm -t \
	-v $(CURDIR):/repo -w /repo \
	-v /var/run/docker.sock:/var/run/docker.sock \
	-v storagebaby-molecule-cache:/root/.cache/molecule \
	-v $(HOME)/.config/sops/age:/root/.config/sops/age \
	-e HOME=/root \
	$(DEVTOOLS_IMAGE)

.PHONY: pull
pull:
	rsync -aHAXvh --exclude-from='.rsyncignore' $(SERVER):$(REMOTE_PATH) ./

.PHONY: push
push:
	rsync -aHAXvh --exclude-from='.rsyncignore' ./ $(SERVER):$(REMOTE_PATH)

.PHONY: devtools
devtools:
	docker build -t $(DEVTOOLS_IMAGE) devtools

.PHONY: devtools-shell
devtools-shell:
	$(subst --rm -t,--rm -it,$(DEVTOOLS_RUN)) bash

.PHONY: format
format:
	$(DEVTOOLS_RUN) prettier --ignore-unknown --write .

.PHONY: fmt-check
fmt-check:
	$(DEVTOOLS_RUN) prettier --ignore-unknown --check .

.PHONY: test
test: test-static test-integration

.PHONY: test-static
test-static:
	$(DEVTOOLS_RUN) pytest tests/static -v

.PHONY: test-integration
test-integration:
	$(DEVTOOLS_RUN) sh -c 'cd tests/integration && molecule test -s $(SCENARIO)'

.PHONY: molecule
molecule:
	$(DEVTOOLS_RUN) sh -c 'cd tests/integration && molecule $(CMD) -s $(SCENARIO)'

.PHONY: test-clean
test-clean:
	$(DEVTOOLS_RUN) sh -c 'cd tests/integration && molecule destroy -s $(SCENARIO)'
	docker volume prune -f

.PHONY: sops
sops:
	$(subst --rm -t,--rm -it,$(DEVTOOLS_RUN)) sops $(FILE)

.PHONY: install-hooks
install-hooks:
	lefthook install
```

Note: `$(HOME)/.config/sops/age` must exist before `docker run` or Docker creates it root-owned. Task 3 creates it.

- [ ] **Step 4: Ignore files**

Append to `.gitignore`:

```
__pycache__/
.pytest_cache/
hosts/test-*/secrets/
```

Append to `.prettierignore`:

```
*.sops.yaml
hosts/test-*/secrets/
```

- [ ] **Step 5: Build and run**

Run: `mkdir -p ~/.config/sops/age && make devtools && make test-static`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add devtools Makefile .gitignore .prettierignore tests/static/test_smoke.py
git commit -m "Rebuild devtools image with Ansible, Molecule, sops and test targets"
```

---

### Task 2: Repo layout and service contract tests

**Files:**

- Create: `hosts/storagebaby/host.yml`, `hosts/test-a/host.yml`
- Create: `hosts/shared/services/traefik/service.yml` (contract only; unit template comes in Task 8)
- Create: `ansible/inventory/hosts.yml`, `ansible/ansible.cfg`
- Create: `tests/static/conftest.py`, `tests/static/test_contract.py`, `tests/static/test_ports.py`
- Delete: `reconciler/`, `PORTS.md`, `traefik/ansible-vars.yml`, `traefik/quadlet/`, `traefik/setup.sh`, `traefik/dynamic.d/`, `traefik/docker-compose.yml`, `traefik/Makefile`, `traefik/README.md`, `traefik/config/`, `traefik/secrets/`, `ansible/playbook.yml`, `ansible/roles/`, `ansible/inventory.ini`, `stirling-pdf/ansible-vars.yml`, `stirling-pdf/setup.sh`, `stirling-pdf/Makefile`
- Keep untouched for later phases: every other top-level service dir, `stirling-pdf/quadlet/`, `stirling-pdf/traefik/`, `snapraid/`, `samba/`.

**Interfaces:**

- Produces: `tests/static/conftest.py` with `REPO: Path`, `load_yaml(path) -> dict`, `host_names() -> list[str]` (host dirs except `shared`), `placements() -> list[Placement]` where `Placement` is a dataclass `(host: str, name: str, dir: Path, spec: dict)`, one entry per (host, service) including shared services expanded onto every host.
- Produces: `host.yml` keys: `domain`, `acme` (bool), `acme_email`, `tz`, `storage_roots: {pool, fast}`, `volume_overrides: {}`, `deploy_timer` (bool).
- Produces: `service.yml` keys: `name`, `port`, optional `domain`, optional `route: {internal, wildcard_cert}`, `volumes: {<name>: {class}}`, `secrets: [..]`, `backup` (dict or the string `none`).

- [ ] **Step 1: Write the loader and the contract tests**

`tests/static/conftest.py`:

```python
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
HOSTS = REPO / "hosts"


def load_yaml(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


def host_names() -> list[str]:
    return sorted(p.name for p in HOSTS.iterdir() if p.is_dir() and p.name != "shared")


@dataclass(frozen=True)
class Placement:
    host: str
    name: str
    dir: Path
    spec: dict


def _services_in(dir_: Path) -> list[tuple[str, Path, dict]]:
    out = []
    for spec_path in sorted(dir_.glob("*/service.yml")):
        out.append((spec_path.parent.name, spec_path.parent.resolve(), load_yaml(spec_path)))
    return out


def placements() -> list[Placement]:
    shared = _services_in(HOSTS / "shared" / "services")
    result = []
    for host in host_names():
        for name, dir_, spec in shared + _services_in(HOSTS / host / "services"):
            result.append(Placement(host, name, dir_, spec))
    return result
```

`tests/static/test_contract.py`:

```python
import pytest

from conftest import placements

REQUIRED = {"name", "port", "volumes", "secrets", "backup"}
CLASSES = {"pool", "fast"}


@pytest.mark.parametrize("p", placements(), ids=lambda p: f"{p.host}/{p.name}")
def test_service_contract(p):
    missing = REQUIRED - set(p.spec)
    assert not missing, f"{p.name}: missing keys {missing}"
    assert p.spec["name"] == p.dir.name, "name must equal the folder name"
    assert isinstance(p.spec["port"], int)
    for vol, cfg in p.spec["volumes"].items():
        assert cfg.get("class") in CLASSES, f"volume {vol} has no valid class"
    assert isinstance(p.spec["secrets"], list)
    assert p.spec["backup"] == "none" or {"paths", "schedule", "retention"} <= set(p.spec["backup"])


def test_at_least_one_placement():
    assert placements()
```

`tests/static/test_ports.py`:

```python
from collections import defaultdict

from conftest import host_names, placements


def test_loopback_ports_unique_per_host():
    for host in host_names():
        seen = defaultdict(list)
        for p in placements():
            if p.host == host:
                seen[p.spec["port"]].append(p.name)
        dupes = {port: names for port, names in seen.items() if len(names) > 1}
        assert not dupes, f"{host}: port collisions {dupes}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `make test-static`
Expected: FAIL, `hosts/` does not exist (`placements()` raises or `test_at_least_one_placement` fails).

- [ ] **Step 3: Create hosts and the Traefik contract**

`hosts/storagebaby/host.yml`:

```yaml
domain: home.klees.io
acme: true
acme_email: email@janlucaklees.de
tz: Europe/Berlin
storage_roots:
  pool: /pool/apps
  fast: /var/lib/storagebaby/fast
volume_overrides: {}
deploy_timer: true
```

`hosts/test-a/host.yml`:

```yaml
domain: test.local
acme: false
acme_email: test@test.local
tz: Europe/Berlin
storage_roots:
  pool: /srv/pool
  fast: /srv/fast
volume_overrides: {}
deploy_timer: false
```

`hosts/shared/services/traefik/service.yml`:

```yaml
name: traefik
port: 8080
domain: traefik
route:
  internal: api@internal
  wildcard_cert: true
volumes:
  letsencrypt: { class: fast }
secrets: [porkbun_api_key, porkbun_secret_api_key]
backup: none
```

`ansible/inventory/hosts.yml`:

```yaml
all:
  hosts:
    storagebaby:
      ansible_connection: local
    test-a:
      ansible_connection: local
```

`ansible/ansible.cfg`:

```ini
[defaults]
inventory = inventory/hosts.yml
roles_path = roles
stdout_callback = yaml
interpreter_python = auto_silent
```

- [ ] **Step 4: Delete the superseded files**

```bash
git rm -r reconciler PORTS.md traefik ansible/playbook.yml ansible/roles ansible/inventory.ini \
	stirling-pdf/ansible-vars.yml stirling-pdf/setup.sh stirling-pdf/Makefile 2> /dev/null
rm -rf reconciler PORTS.md traefik ansible/roles ansible/inventory.ini ansible/playbook.yml \
	stirling-pdf/ansible-vars.yml stirling-pdf/setup.sh stirling-pdf/Makefile
```

(Untracked files need plain `rm`; tracked ones `git rm`. Old `traefik/README.md` content about ACME gotchas is preserved in Task 8's README.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `make test-static`
Expected: contract test passes for `storagebaby/traefik` and `test-a/traefik`, ports test passes.

- [ ] **Step 6: Commit**

```bash
git add -A hosts ansible tests/static
git commit -m "Introduce per-host layout with Traefik as first service contract"
```

---

### Task 3: Secrets with sops + age

**Files:**

- Create: `.sops.yaml`
- Create: `hosts/shared/services/traefik/secrets.sops.yaml`
- Create: `tests/static/test_secrets.py`
- Operator machine: `~/.config/sops/age/keys.txt` (created if missing; never committed)

**Interfaces:**

- Produces: `.sops.yaml` with a YAML anchor per key (`&operator`, later `&host_<name>`) and one `creation_rules` entry per path prefix.
- Produces: secrets file schema: flat mapping `secret_name: value`, encrypted; key names remain plaintext.
- Consumed by: Task 7 (service role decrypts with `sops --decrypt`), Task 5 (test secrets generated in the same file format).

- [ ] **Step 1: Write the tests**

`tests/static/test_secrets.py`:

```python
import re
import subprocess

import pytest
import yaml

from conftest import HOSTS, REPO, load_yaml, placements

SOPS_CONFIG = load_yaml(REPO / ".sops.yaml")


def tracked_sops_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "*.sops.yaml"], capture_output=True, text=True, check=True)
    return sorted(line for line in out.stdout.splitlines() if line)


def expected_recipients(relpath: str) -> set[str]:
    for rule in SOPS_CONFIG["creation_rules"]:
        if re.search(rule["path_regex"], relpath):
            return {k for group in rule["key_groups"] for k in group["age"]}
    raise AssertionError(f"{relpath} matches no creation rule in .sops.yaml")


@pytest.mark.parametrize("relpath", tracked_sops_files())
def test_recipients_match_sops_config(relpath):
    doc = load_yaml(REPO / relpath)
    actual = {entry["recipient"] for entry in doc["sops"]["age"]}
    assert actual == expected_recipients(relpath)


@pytest.mark.parametrize("p", placements(), ids=lambda p: f"{p.host}/{p.name}")
def test_declared_secrets_exist_in_file(p):
    if not p.spec["secrets"]:
        return
    doc = load_yaml(p.dir / "secrets.sops.yaml")
    assert set(p.spec["secrets"]) <= set(doc) - {"sops"}


@pytest.mark.parametrize("p", placements(), ids=lambda p: f"{p.host}/{p.name}")
def test_template_secrets_are_declared(p):
    used = set()
    for tpl in (p.dir / "quadlet").glob("*.j2"):
        used |= set(re.findall(r"^Secret=([A-Za-z0-9_.-]+)", tpl.read_text(), flags=re.M))
    assert used <= set(p.spec["secrets"]), f"undeclared secrets in templates: {used - set(p.spec['secrets'])}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `make test-static`
Expected: FAIL, `.sops.yaml` missing.

- [ ] **Step 3: Create the operator key if missing**

Run: `test -f ~/.config/sops/age/keys.txt || make devtools-shell` then inside: `age-keygen -o /root/.config/sops/age/keys.txt` (the directory is bind-mounted from the operator's home). Then `age-keygen -y /root/.config/sops/age/keys.txt` prints the public key. Exit the shell.

- [ ] **Step 4: Write `.sops.yaml`**

Replace `<operator-pubkey>` with the printed key:

```yaml
keys:
  - &operator <operator-pubkey>
  # Host keys are added here by the operator after bootstrap.sh prints them,
  # followed by `make sops FILE=<file> ` → `sops updatekeys` on affected files.
creation_rules:
  - path_regex: ^hosts/storagebaby/.*\.sops\.yaml$
    key_groups:
      - age: [*operator]
  - path_regex: ^hosts/shared/.*\.sops\.yaml$
    key_groups:
      - age: [*operator]
```

- [ ] **Step 5: Encrypt the real Traefik secrets**

The values live in the main clone at `/home/jlk/Projects/StorageBaby/traefik/secrets/porkbun_api_key.secret` and `porkbun_secret_api_key.secret` (gitignored, 8 bytes each, no newline). Build the plaintext in the scratchpad and encrypt with a filename override so the `shared` rule applies:

```bash
S=/tmp/claude-1000/-home-jlk-Projects-StorageBaby/53e86ace-967b-46ce-a95e-7dcd0d9650b2/scratchpad
printf 'porkbun_api_key: "%s"\nporkbun_secret_api_key: "%s"\n' \
	"$(cat /home/jlk/Projects/StorageBaby/traefik/secrets/porkbun_api_key.secret)" \
	"$(cat /home/jlk/Projects/StorageBaby/traefik/secrets/porkbun_secret_api_key.secret)" > "$S/traefik-secrets.yaml"
docker run --rm -v "$S":/in -v "$(pwd)":/repo -w /repo storagebaby-devtools \
	sops --encrypt --filename-override hosts/shared/services/traefik/secrets.sops.yaml /in/traefik-secrets.yaml \
	> hosts/shared/services/traefik/secrets.sops.yaml
rm "$S/traefik-secrets.yaml"
```

Verify round trip: `make sops FILE=hosts/shared/services/traefik/secrets.sops.yaml` opens the decrypted file in an editor; quit without saving.

- [ ] **Step 6: Run tests to verify they pass**

Run: `make test-static`
Expected: all pass. `test_template_secrets_are_declared` passes trivially until Task 8 adds templates.

- [ ] **Step 7: Commit**

```bash
git add .sops.yaml hosts/shared/services/traefik/secrets.sops.yaml tests/static/test_secrets.py
git commit -m "Encrypt secrets with sops and age, per host path"
```

---

### Task 4: bootstrap.sh and the deploy units

**Files:**

- Create: `bootstrap.sh`
- Create: `ansible/roles/host_base/files/storagebaby-deploy.service`, `ansible/roles/host_base/files/storagebaby-deploy.timer`
- Create: `tests/static/test_bootstrap.py`

**Interfaces:**

- Produces: `bootstrap.sh --repo <url> [--branch stable] [--local]`. Writes `/etc/storagebaby/{age.key,age.pub,deploy_key,deploy_key.pub,deploy.conf}`, installs the two units into `/etc/systemd/system/`, enables the timer unless `--local`. Prints the two public keys last. Idempotent: never overwrites an existing key.
- Produces: `/etc/storagebaby/deploy.conf` with `REPO_URL`, `BRANCH`, `EXTRA_ARGS` (empty by default; tests may set it).
- Consumed by: Task 5 prepare (runs it with `--local`), Task 6 host_base (re-installs the same unit files from `files/` so they stay under git control), Task 9 deploy test.

- [ ] **Step 1: Write the static test**

`tests/static/test_bootstrap.py`:

```python
import subprocess

from conftest import REPO

UNIT_DIR = REPO / "ansible/roles/host_base/files"


def test_bootstrap_is_valid_bash():
    subprocess.run(["bash", "-n", str(REPO / "bootstrap.sh")], check=True)


def test_bootstrap_embeds_the_same_units_host_base_installs():
    script = (REPO / "bootstrap.sh").read_text()
    for unit in ["storagebaby-deploy.service", "storagebaby-deploy.timer"]:
        body = (UNIT_DIR / unit).read_text().strip()
        assert body in script, f"{unit} in bootstrap.sh drifted from host_base/files/{unit}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test-static`
Expected: FAIL, `bootstrap.sh` missing.

- [ ] **Step 3: Write the units**

`ansible/roles/host_base/files/storagebaby-deploy.service`:

```ini
[Unit]
Description=StorageBaby deploy (ansible-pull of the stable branch)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/storagebaby/deploy.conf
Environment=GIT_SSH_COMMAND=ssh -i /etc/storagebaby/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new
ExecStart=/usr/bin/ansible-pull --url ${REPO_URL} --checkout ${BRANCH} --directory /var/lib/storagebaby/repo --inventory ansible/inventory/hosts.yml --limit %H --only-if-changed $EXTRA_ARGS ansible/playbook.yml
```

`ansible/roles/host_base/files/storagebaby-deploy.timer`:

```ini
[Unit]
Description=Run the StorageBaby deploy periodically

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Write bootstrap.sh**

```bash
#!/bin/bash
# One-time host setup. Run as root on a fresh Arch host:
#   bootstrap.sh --repo git@github.com:janlucaklees/StorageBaby.git [--branch stable] [--local]
# --local: do not enable the deploy timer (tests and dry runs).
set -euo pipefail

REPO_URL=""
BRANCH="stable"
LOCAL=0
while [ $# -gt 0 ]; do
	case "$1" in
		--repo)
			REPO_URL="$2"
			shift 2
			;;
		--branch)
			BRANCH="$2"
			shift 2
			;;
		--local)
			LOCAL=1
			shift
			;;
		*)
			echo "unknown argument: $1" >&2
			exit 2
			;;
	esac
done
[ -n "$REPO_URL" ] || {
	echo "--repo is required" >&2
	exit 2
}
[ "$(id -u)" -eq 0 ] || {
	echo "run as root" >&2
	exit 2
}

pacman -Syu --noconfirm --needed git ansible sops age podman passt openssh

install -d -m 0700 /etc/storagebaby
if [ ! -f /etc/storagebaby/age.key ]; then
	age-keygen -o /etc/storagebaby/age.key 2> /dev/null
	chmod 0600 /etc/storagebaby/age.key
fi
age-keygen -y /etc/storagebaby/age.key > /etc/storagebaby/age.pub
if [ ! -f /etc/storagebaby/deploy_key ]; then
	ssh-keygen -q -t ed25519 -N '' -C "storagebaby-deploy@$(hostname)" -f /etc/storagebaby/deploy_key
fi
cat > /etc/storagebaby/deploy.conf << EOF
REPO_URL=${REPO_URL}
BRANCH=${BRANCH}
EXTRA_ARGS=
EOF
chmod 0600 /etc/storagebaby/deploy.conf

cat > /etc/systemd/system/storagebaby-deploy.service << 'EOF'
[Unit]
Description=StorageBaby deploy (ansible-pull of the stable branch)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/storagebaby/deploy.conf
Environment=GIT_SSH_COMMAND=ssh -i /etc/storagebaby/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new
ExecStart=/usr/bin/ansible-pull --url ${REPO_URL} --checkout ${BRANCH} --directory /var/lib/storagebaby/repo --inventory ansible/inventory/hosts.yml --limit %H --only-if-changed $EXTRA_ARGS ansible/playbook.yml
EOF

cat > /etc/systemd/system/storagebaby-deploy.timer << 'EOF'
[Unit]
Description=Run the StorageBaby deploy periodically

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
if [ "$LOCAL" -eq 0 ]; then
	systemctl enable --now storagebaby-deploy.timer
fi

echo
echo "Add this as a read-only deploy key on the repository:"
cat /etc/storagebaby/deploy_key.pub
echo
echo "Add this age recipient to .sops.yaml under this host's rule, then run sops updatekeys:"
cat /etc/storagebaby/age.pub
```

`chmod +x bootstrap.sh`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `make test-static`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add bootstrap.sh ansible/roles/host_base/files tests/static/test_bootstrap.py
git commit -m "Add one-time host bootstrap and pull-based deploy units"
```

---

### Task 5: Molecule scenario on libvirt/QEMU VMs and the rootless-Podman gate

**Why VMs, not containers:** a first attempt used a privileged Arch systemd Docker container. Its systemd started udev, whose coldplug re-announced every host device and killed the operator's Wayland session. Test hosts are therefore real KVM virtual machines managed by the host's libvirt. The devtools container only holds the tooling and talks to the host's libvirt socket; it is never privileged and gets no device passthrough.

**Host prerequisites (operator-installed, already done on the dev machine):** `qemu-base libvirt dnsmasq iptables-nft`, `libvirtd.service` enabled, libvirt `default` NAT network active (`virbr0`, 192.168.122.0/24), socket at `/var/run/libvirt/libvirt-sock`, images directory `/var/lib/libvirt/images`.

**Files:**

- Modify: `devtools/Dockerfile` (add `libvirt-python qemu-img cdrtools`; create the venv with `--system-site-packages` so the venv sees the pacman-installed libvirt bindings)
- Modify: `devtools/requirements.yml` (add `community.libvirt`)
- Modify: `Makefile` (replace `DEVTOOLS_RUN_DOCKER` with `DEVTOOLS_RUN_VM`)
- Create: `tests/integration/molecule/test-ci/molecule.yml`, `create.yml`, `destroy.yml`, `prepare.yml`, `converge.yml`
- Create: `tests/integration/molecule/templates/domain.xml.j2`, `tests/integration/molecule/templates/user-data.j2`, `tests/integration/molecule/templates/meta-data.j2`
- Create: `tests/integration/molecule/test-ci/tests/test_rootless_podman.py`
- Create: `tests/integration/pytest.ini`
- Delete: the uncommitted Docker-driver leftovers `tests/integration/molecule/Dockerfile.j2`, `tests/integration/molecule/test-ci/tests/test_rootless_podman.py`. Reuse the uncommitted `prepare.yml` and `converge.yml` where they match the text below.

**Interfaces:**

- Produces: `make test-integration` and `make molecule CMD=...` running against libvirt VMs; the devtools container runs with `--network host` (libvirt's NAT firewall rejects new connections from the Docker bridge to the VM network) and mounts `/var/run/libvirt/libvirt-sock` and `/var/lib/libvirt/images`.
- Produces: platform `test-a` (hostname `test-a`), Arch cloud image, 2 vCPUs, 4096 MiB, 20 GiB overlay disk, root SSH via a per-scenario ed25519 key. Ansible connects as `root` over SSH; no `become` needed.
- Produces: `prepare.yml` that runs `bootstrap.sh --local --repo /srv/storagebaby.git` and records the host age public key in the fact `storagebaby_age_pub`. Task 7 extends it with secret generation; Task 9 with the bare repo.
- Produces: files under `/var/lib/libvirt/images/storagebaby/`: `arch-cloudimg.qcow2` (cached base, downloaded once), `<instance>.qcow2` (overlay), `<instance>-seed.iso`.
- Produces: the go/no-go for rootless Podman as a system user inside the VM. If `test_rootless_podman.py` cannot pass, STOP and report.

- [ ] **Step 1: Write the gate test**

`tests/integration/molecule/test-ci/tests/test_rootless_podman.py`:

```python
def run_as(host, user, cmd):
    uid = host.user(user).uid
    return host.run(f"runuser -u {user} -- env XDG_RUNTIME_DIR=/run/user/{uid} {cmd}")


def test_probe_user_can_run_rootless_container(host):
    assert host.user("svc-probe").exists
    assert host.run("loginctl show-user svc-probe -p Linger").stdout.strip() == "Linger=yes"
    r = run_as(host, "svc-probe", "podman run --rm docker.io/library/alpine:3 id -u")
    assert r.rc == 0, r.stderr
    assert r.stdout.strip() == "0"


def test_probe_user_manager_reachable_from_root(host):
    r = host.run("systemctl --user -M svc-probe@ is-system-running --wait")
    assert r.stdout.strip() in {"running", "degraded"}, r.stderr


def test_rootless_container_can_bind_port_80(host):
    assert host.run("sysctl -n net.ipv4.ip_unprivileged_port_start").stdout.strip() == "80"
    r = run_as(host, "svc-probe", "podman run --rm --network host docker.io/library/alpine:3 sh -c 'nc -l -p 80 -s 127.0.0.1 -w 1 </dev/null >/dev/null & sleep 0.5; kill %1 2>/dev/null; echo ok'")
    assert r.rc == 0, r.stderr
```

- [ ] **Step 2: Devtools image and Makefile**

`devtools/Dockerfile`: add `libvirt-python qemu-img cdrtools` to the pacman line and change the venv line to `RUN python -m venv --system-site-packages /opt/venv`. `devtools/requirements.yml`: add `- name: community.libvirt`.

`Makefile`: replace the `DEVTOOLS_RUN_DOCKER` variable with

```make
LIBVIRT_SOCK := /var/run/libvirt/libvirt-sock
LIBVIRT_IMAGES := /var/lib/libvirt/images
DEVTOOLS_RUN_VM := docker run --rm -t --network host \
	-v $(CURDIR):/repo -w /repo \
	-v $(LIBVIRT_SOCK):$(LIBVIRT_SOCK) \
	-v $(LIBVIRT_IMAGES):$(LIBVIRT_IMAGES) \
	-v $(MOLECULE_CACHE):/root/.cache/molecule \
	-e HOME=/root \
	$(DEVTOOLS_IMAGE)
```

and use `DEVTOOLS_RUN_VM` in `test-integration`, `molecule`, `test-clean`. Nothing privileged, no `--device`.

- [ ] **Step 3: Write the scenario**

`tests/integration/pytest.ini`:

```ini
[pytest]
addopts = -v
```

`tests/integration/molecule/test-ci/molecule.yml`:

```yaml
driver:
  name: default
  options:
    managed: true
platforms:
  - name: test-a
    hostname: test-a
    memory_mib: 4096
    vcpus: 2
    disk: 20G
provisioner:
  name: ansible
  env:
    ANSIBLE_ROLES_PATH: /repo/ansible/roles
  inventory:
    group_vars:
      all:
        storagebaby_repo_root: /repo
        ansible_user: root
        ansible_ssh_common_args: -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
  playbooks:
    create: create.yml
    destroy: destroy.yml
    prepare: prepare.yml
    converge: converge.yml
verifier:
  name: testinfra
scenario:
  test_sequence:
    - destroy
    - create
    - prepare
    - converge
    - idempotence
    - verify
    - destroy
```

`tests/integration/molecule/templates/meta-data.j2`:

```
instance-id: {{ item.name }}
local-hostname: {{ item.hostname }}
```

`tests/integration/molecule/templates/user-data.j2`:

```yaml
#cloud-config
disable_root: false
ssh_pwauth: false
users:
  - name: root
    ssh_authorized_keys:
      - { { ssh_pubkey } }
growpart:
  mode: auto
  devices: ['/']
```

`tests/integration/molecule/templates/domain.xml.j2`:

```xml
<domain type="kvm">
  <name>{{ item.name }}</name>
  <memory unit="MiB">{{ item.memory_mib }}</memory>
  <vcpu>{{ item.vcpus }}</vcpu>
  <os>
    <type arch="x86_64" machine="q35">hvm</type>
  </os>
  <features><acpi/><apic/></features>
  <cpu mode="host-passthrough"/>
  <devices>
    <disk type="file" device="disk">
      <driver name="qemu" type="qcow2"/>
      <source file="{{ images_dir }}/{{ item.name }}.qcow2"/>
      <target dev="vda" bus="virtio"/>
    </disk>
    <disk type="file" device="cdrom">
      <driver name="qemu" type="raw"/>
      <source file="{{ images_dir }}/{{ item.name }}-seed.iso"/>
      <target dev="sda" bus="sata"/>
      <readonly/>
    </disk>
    <interface type="network">
      <source network="default"/>
      <model type="virtio"/>
    </interface>
    <serial type="pty"><target port="0"/></serial>
    <console type="pty"><target type="serial" port="0"/></console>
    <rng model="virtio"><backend model="random">/dev/urandom</backend></rng>
  </devices>
</domain>
```

`tests/integration/molecule/test-ci/create.yml`:

```yaml
- name: Create libvirt VMs
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    libvirt_uri: qemu:///system
    images_dir: /var/lib/libvirt/images/storagebaby
    base_image_url: https://geo.mirror.pkgbuild.com/images/latest/Arch-Linux-x86_64-cloudimg.qcow2
    base_image: '{{ images_dir }}/arch-cloudimg.qcow2'
    ssh_key: '{{ molecule_ephemeral_directory }}/id_ed25519'
  tasks:
    - name: Images directory
      ansible.builtin.file:
        path: '{{ images_dir }}'
        state: directory
        mode: '0755'

    - name: Cached Arch cloud image (downloaded once)
      ansible.builtin.get_url:
        url: '{{ base_image_url }}'
        dest: '{{ base_image }}'
        mode: '0644'
        force: false

    - name: Per-scenario SSH key
      ansible.builtin.command: ssh-keygen -q -t ed25519 -N '' -f {{ ssh_key }}
      args:
        creates: '{{ ssh_key }}'

    - name: Read public key
      ansible.builtin.set_fact:
        ssh_pubkey: "{{ lookup('file', ssh_key ~ '.pub') }}"

    - name: Overlay disks
      ansible.builtin.command: qemu-img create -f qcow2 -b {{ base_image }} -F qcow2 {{ images_dir }}/{{ item.name }}.qcow2 {{ item.disk }}
      args:
        creates: '{{ images_dir }}/{{ item.name }}.qcow2'
      loop: '{{ molecule_yml.platforms }}'
      loop_control:
        label: '{{ item.name }}'

    - name: Seed directories
      ansible.builtin.file:
        path: '{{ molecule_ephemeral_directory }}/seed-{{ item.name }}'
        state: directory
        mode: '0755'
      loop: '{{ molecule_yml.platforms }}'
      loop_control:
        label: '{{ item.name }}'

    - name: Seed meta-data
      ansible.builtin.template:
        src: ../templates/meta-data.j2
        dest: '{{ molecule_ephemeral_directory }}/seed-{{ item.name }}/meta-data'
        mode: '0644'
      loop: '{{ molecule_yml.platforms }}'
      loop_control:
        label: '{{ item.name }}'

    - name: Seed user-data
      ansible.builtin.template:
        src: ../templates/user-data.j2
        dest: '{{ molecule_ephemeral_directory }}/seed-{{ item.name }}/user-data'
        mode: '0644'
      loop: '{{ molecule_yml.platforms }}'
      loop_control:
        label: '{{ item.name }}'

    - name: Seed ISOs
      ansible.builtin.command: >-
        mkisofs -output {{ images_dir }}/{{ item.name }}-seed.iso -volid cidata -joliet -rock
        {{ molecule_ephemeral_directory }}/seed-{{ item.name }}/user-data
        {{ molecule_ephemeral_directory }}/seed-{{ item.name }}/meta-data
      args:
        creates: '{{ images_dir }}/{{ item.name }}-seed.iso'
      loop: '{{ molecule_yml.platforms }}'
      loop_control:
        label: '{{ item.name }}'

    - name: Define domains
      community.libvirt.virt:
        uri: '{{ libvirt_uri }}'
        command: define
        xml: "{{ lookup('template', '../templates/domain.xml.j2') }}"
      loop: '{{ molecule_yml.platforms }}'
      loop_control:
        label: '{{ item.name }}'

    - name: Start domains
      community.libvirt.virt:
        uri: '{{ libvirt_uri }}'
        name: '{{ item.name }}'
        state: running
      loop: '{{ molecule_yml.platforms }}'
      loop_control:
        label: '{{ item.name }}'

    - name: Wait for a DHCP lease
      ansible.builtin.command: virsh -c {{ libvirt_uri }} domifaddr {{ item.name }} --source lease
      register: _ifaddr
      retries: 60
      delay: 3
      until: _ifaddr.stdout is search('ipv4')
      changed_when: false
      loop: '{{ molecule_yml.platforms }}'
      loop_control:
        label: '{{ item.name }}'

    - name: Extract addresses
      ansible.builtin.set_fact:
        instance_conf: >-
          {{ (instance_conf | default([])) + [{
               'instance': item.item.name,
               'address': (item.stdout | regex_search('ipv4\\s+(\\d+\\.\\d+\\.\\d+\\.\\d+)', '\\1') | first),
               'user': 'root',
               'port': 22,
               'identity_file': ssh_key }] }}
      loop: '{{ _ifaddr.results }}'
      loop_control:
        label: '{{ item.item.name }}'

    - name: Wait for SSH
      ansible.builtin.wait_for:
        host: '{{ item.address }}'
        port: 22
        search_regex: OpenSSH
        timeout: 300
      loop: '{{ instance_conf }}'
      loop_control:
        label: '{{ item.instance }}'

    - name: Write instance config
      ansible.builtin.copy:
        content: '{{ instance_conf | to_json }}'
        dest: '{{ molecule_instance_config }}'
        mode: '0600'
```

`tests/integration/molecule/test-ci/destroy.yml`:

```yaml
- name: Destroy libvirt VMs
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    libvirt_uri: qemu:///system
    images_dir: /var/lib/libvirt/images/storagebaby
  tasks:
    - name: List domains
      community.libvirt.virt:
        uri: '{{ libvirt_uri }}'
        command: list_vms
      register: _vms

    - name: Stop domains
      community.libvirt.virt:
        uri: '{{ libvirt_uri }}'
        name: '{{ item.name }}'
        state: destroyed
      loop: '{{ molecule_yml.platforms }}'
      loop_control:
        label: '{{ item.name }}'
      when: item.name in _vms.list_vms

    - name: Undefine domains
      community.libvirt.virt:
        uri: '{{ libvirt_uri }}'
        name: '{{ item.name }}'
        command: undefine
      loop: '{{ molecule_yml.platforms }}'
      loop_control:
        label: '{{ item.name }}'
      when: item.name in _vms.list_vms

    - name: Remove overlay and seed
      ansible.builtin.file:
        path: '{{ images_dir }}/{{ item.0.name }}{{ item.1 }}'
        state: absent
      loop: "{{ molecule_yml.platforms | product(['.qcow2', '-seed.iso']) | list }}"
      loop_control:
        label: '{{ item.0.name }}{{ item.1 }}'

    - name: Clear instance config
      ansible.builtin.copy:
        content: '[]'
        dest: '{{ molecule_instance_config }}'
        mode: '0600'
```

`tests/integration/molecule/test-ci/prepare.yml`:

```yaml
- name: Bootstrap the test host like a real one
  hosts: all
  gather_facts: false
  tasks:
    - name: Wait for cloud-init to finish
      ansible.builtin.command: cloud-init status --wait
      changed_when: false
      failed_when: false

    - name: Copy bootstrap.sh
      ansible.builtin.copy:
        src: /repo/bootstrap.sh
        dest: /usr/local/sbin/storagebaby-bootstrap
        mode: '0755'

    - name: Run bootstrap in local mode
      ansible.builtin.command: /usr/local/sbin/storagebaby-bootstrap --local --repo /srv/storagebaby.git
      changed_when: true

    - name: Read the host's age public key
      ansible.builtin.slurp:
        src: /etc/storagebaby/age.pub
      register: _age_pub

    - name: Record it
      ansible.builtin.set_fact:
        storagebaby_age_pub: '{{ (_age_pub.content | b64decode).strip() }}'
        cacheable: true
```

`tests/integration/molecule/test-ci/converge.yml` (spike version, replaced in Task 8):

```yaml
- name: Rootless Podman spike
  hosts: all
  gather_facts: false
  tasks:
    - name: Allow unprivileged ports from 80
      ansible.posix.sysctl:
        name: net.ipv4.ip_unprivileged_port_start
        value: '80'
        sysctl_set: true
        state: present

    - name: Create probe user
      ansible.builtin.user:
        name: svc-probe
        system: true
        shell: /usr/bin/nologin
        create_home: true

    - name: Check subordinate ids
      ansible.builtin.shell: grep -q '^svc-probe:' /etc/subuid && grep -q '^svc-probe:' /etc/subgid
      register: _subids
      changed_when: false
      failed_when: false

    - name: Allocate subordinate ids
      ansible.builtin.command: usermod --add-subids svc-probe
      when: _subids.rc != 0

    - name: Enable linger
      ansible.builtin.command: loginctl enable-linger svc-probe
      args:
        creates: /var/lib/systemd/linger/svc-probe

    - name: Wait for the user manager
      ansible.builtin.command: systemctl --user -M svc-probe@ is-system-running --wait
      register: _mgr
      retries: 15
      delay: 2
      until: _mgr.rc == 0 or 'degraded' in _mgr.stdout
      changed_when: false
```

- [ ] **Step 4: Rebuild and run**

Run: `make devtools && make test-integration`
Expected: create downloads the cloud image (once, ~500 MB), boots the VM, prepare runs bootstrap (pacman inside the VM, several minutes), converge and idempotence pass, verify runs 3 tests, destroy removes the domain and files.

Debug loop: `make molecule CMD=create`, `CMD=prepare`, `CMD=converge`, `CMD=verify`, `CMD=login` (SSH into the VM), `CMD=destroy`. From the host the operator can watch with `virsh list` and `virsh console test-a`.

Known failure modes: (a) `community.libvirt.virt` cannot import `libvirt` → the venv was not created with `--system-site-packages`; (b) no DHCP lease → `default` network not active on the host or `dnsmasq` missing; (c) SSH refused → cloud-init still running, or `disable_root` not honored; check `virsh console`; (d) `qemu-img`/`mkisofs` missing → pacman line. If rootless Podman itself fails inside the VM, report BLOCKED with `podman info` output as the probe user.

- [ ] **Step 5: Static suite still green**

Run: `make test-static`. Expected: 15 passed.

- [ ] **Step 6: Commit**

```bash
git add devtools Makefile tests/integration
git commit -m "Add Molecule scenario on libvirt VMs proving rootless Podman on a bootstrapped Arch host"
```

---

### Task 6: host_base role

**Files:**

- Create: `ansible/roles/host_base/tasks/main.yml`, `ansible/roles/host_base/handlers/main.yml`
- Create: `ansible/playbook.yml`
- Create: `tests/integration/molecule/test-ci/tests/test_host_base.py`
- Modify: `tests/integration/molecule/test-ci/converge.yml` (import the playbook, keep the probe user tasks in a separate play until Task 8 removes them)

**Interfaces:**

- Produces: `ansible/playbook.yml` with vars `storagebaby_repo_root` (default `{{ playbook_dir }}/..`), `render_only` (default false), `render_output` (default ""), pre_tasks loading `hosts/<inventory_hostname>/host.yml` and setting `placed_services` (list of absolute `service.yml` paths, shared first), then role `host_base` (skipped when `render_only`), then `include_role: service` per placed service with `service` and `service_dir` vars.
- Produces on the host: packages, sysctl, `/etc/storagebaby/traefik/dynamic.d` (root:root 0755), `/etc/containers/systemd/users` (0755), deploy units from `files/` and timer state from `deploy_timer`.

- [ ] **Step 1: Write the tests**

`tests/integration/molecule/test-ci/tests/test_host_base.py`:

```python
import pytest


@pytest.mark.parametrize("pkg", ["podman", "passt", "sops", "age", "ansible", "git"])
def test_packages(host, pkg):
    assert host.package(pkg).is_installed


def test_unprivileged_ports(host):
    assert host.run("sysctl -n net.ipv4.ip_unprivileged_port_start").stdout.strip() == "80"


def test_platform_dirs(host):
    d = host.file("/etc/storagebaby/traefik/dynamic.d")
    assert d.is_directory and d.mode == 0o755
    assert host.file("/etc/containers/systemd/users").is_directory


def test_deploy_units_installed_but_timer_off_for_test_host(host):
    assert host.file("/etc/systemd/system/storagebaby-deploy.service").exists
    assert host.service("storagebaby-deploy.timer").is_enabled is False


def test_age_key_present(host):
    f = host.file("/etc/storagebaby/age.key")
    assert f.exists and f.mode == 0o600 and f.user == "root"
```

- [ ] **Step 2: Run verify to see them fail**

Run: `make molecule CMD=verify`
Expected: `test_platform_dirs` and `test_deploy_units_installed_but_timer_off_for_test_host` fail (the timer is enabled? no: `--local` left it disabled; dirs are missing).

- [ ] **Step 3: Write the role and playbook**

`ansible/roles/host_base/tasks/main.yml`:

```yaml
- name: Install platform packages
  community.general.pacman:
    name: [podman, passt, sops, age, ansible, git, openssh]
    state: present

- name: Allow unprivileged binding from port 80 (rootless Traefik on host network)
  ansible.posix.sysctl:
    name: net.ipv4.ip_unprivileged_port_start
    value: '80'
    sysctl_set: true
    state: present
    reload: true

- name: Require the host age key from bootstrap
  ansible.builtin.stat:
    path: /etc/storagebaby/age.key
  register: _age_key
  failed_when: not _age_key.stat.exists

- name: Platform directories
  ansible.builtin.file:
    path: '{{ item }}'
    state: directory
    owner: root
    group: root
    mode: '0755'
  loop:
    - /etc/storagebaby
    - /etc/storagebaby/traefik
    - /etc/storagebaby/traefik/dynamic.d
    - /etc/containers/systemd/users
    - /var/lib/storagebaby

- name: Keep /etc/storagebaby private except for the traefik dynamic dir
  ansible.builtin.file:
    path: /etc/storagebaby
    mode: '0711'

- name: Deploy units
  ansible.builtin.copy:
    src: '{{ item }}'
    dest: '/etc/systemd/system/{{ item }}'
    mode: '0644'
  loop: [storagebaby-deploy.service, storagebaby-deploy.timer]
  notify: systemd daemon-reload

- name: Flush handlers
  ansible.builtin.meta: flush_handlers

- name: Deploy timer state
  ansible.builtin.systemd:
    name: storagebaby-deploy.timer
    enabled: '{{ deploy_timer }}'
    state: "{{ 'started' if deploy_timer else 'stopped' }}"
```

`ansible/roles/host_base/handlers/main.yml`:

```yaml
- name: systemd daemon-reload
  ansible.builtin.systemd:
    daemon_reload: true
```

`ansible/playbook.yml`:

```yaml
- name: Converge a StorageBaby host
  hosts: all
  become: true
  gather_facts: false
  vars:
    storagebaby_repo_root: '{{ playbook_dir }}/..'
    render_only: false
    render_output: ''
  pre_tasks:
    - name: Load host configuration
      ansible.builtin.include_vars:
        file: '{{ storagebaby_repo_root }}/hosts/{{ inventory_hostname }}/host.yml'

    - name: Discover placed services (shared first, then this host's)
      ansible.builtin.set_fact:
        placed_services: >-
          {{ lookup('fileglob', storagebaby_repo_root ~ '/hosts/shared/services/*/service.yml', wantlist=True) | sort
             + lookup('fileglob', storagebaby_repo_root ~ '/hosts/' ~ inventory_hostname ~ '/services/*/service.yml', wantlist=True) | sort }}
  roles:
    - role: host_base
      when: not render_only
  tasks:
    - name: Apply the service role per placed service
      ansible.builtin.include_role:
        name: service
      vars:
        service: "{{ lookup('file', item) | from_yaml }}"
        service_dir: '{{ item | dirname }}'
      loop: '{{ placed_services }}'
      loop_control:
        label: '{{ item | dirname | basename }}'
```

Create an empty placeholder role so the include resolves before Task 7: `ansible/roles/service/tasks/main.yml` containing:

```yaml
- name: Service role placeholder (Task 7 replaces this)
  ansible.builtin.debug:
    msg: 'would converge {{ service.name }} from {{ service_dir }}'
```

`tests/integration/molecule/test-ci/converge.yml`: put the existing spike tasks in a first play unchanged, then append:

```yaml
- import_playbook: /repo/ansible/playbook.yml
```

- [ ] **Step 4: Run the scenario**

Run: `make test-integration`
Expected: converge and idempotence pass, all host_base tests and the nested-Podman tests pass. If `Deploy timer state` is not idempotent (Ansible reports changed on the second run), replace `state:` with `enabled` only for the false case.

- [ ] **Step 5: Static check of the playbook**

Add to `tests/static/test_smoke.py`:

```python
def test_playbook_syntax():
    subprocess.run(["ansible-playbook", "--syntax-check", "-i", "storagebaby,", "ansible/playbook.yml"], check=True, cwd=str(Path(__file__).resolve().parents[2]))
```

with `from pathlib import Path` at the top. Run: `make test-static`. Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add ansible tests
git commit -m "Add host_base role and site playbook with service discovery"
```

---

### Task 7: service role

**Files:**

- Replace: `ansible/roles/service/tasks/main.yml`
- Create: `ansible/roles/service/tasks/render.yml`, `ansible/roles/service/tasks/host.yml`, `ansible/roles/service/tasks/secrets.yml`, `ansible/roles/service/tasks/units.yml`
- Create: `ansible/roles/service/files/podman-secret-sync.sh`
- Create: `ansible/roles/service/templates/traefik-route.yml.j2`
- Create: `ansible/roles/service/defaults/main.yml`
- Create: `tests/static/test_render.py`
- Modify: `tests/integration/molecule/test-ci/prepare.yml` (generate test secrets)

**Interfaces:**

- Consumes: `service` (dict), `service_dir` (path on the controller), `render_only`, `render_output`, host vars from `host.yml`, `storagebaby_repo_root`.
- Produces template variables for `quadlet/*.j2`: `service` (the contract), `svc_user` (`svc-<name>`), `volumes` (dict name → absolute host path), `config_dir` (`/etc/storagebaby/<name>`), `fqdn`, `tz`, `acme`, `acme_email`, `domain`.
- Produces on the host: user, subids, linger, volume dirs, `/etc/storagebaby/<name>/` from `config/`, Podman secrets, units in `/etc/containers/systemd/users/<uid>/`, route file `/etc/storagebaby/traefik/dynamic.d/<name>.yml`, units started, changed units restarted, `podman-auto-update.timer` enabled.
- Produces: `podman-secret-sync.sh <user> <secret>` reading the value on stdin, printing `CHANGED` or `UNCHANGED`.
- Secrets file lookup order: `hosts/<host>/secrets/<name>.sops.yaml`, then `<service_dir>/secrets.sops.yaml`.

- [ ] **Step 1: Write the render test**

`tests/static/test_render.py`:

```python
import subprocess
from pathlib import Path

import pytest

from conftest import REPO, host_names, placements


@pytest.fixture(scope="session")
def rendered(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("render")
    for host in host_names():
        subprocess.run(
            ["ansible-playbook", "-i", f"{host},", "-c", "local", "ansible/playbook.yml",
             "-e", "render_only=true", "-e", f"render_output={out / host}", "-e", "ansible_become=false"],
            check=True, cwd=str(REPO),
        )
    return out


@pytest.mark.parametrize("p", placements(), ids=lambda p: f"{p.host}/{p.name}")
def test_rendered_units_pass_quadlet_dryrun(rendered, p):
    unit_dir = rendered / p.host / p.name
    templates = list((p.dir / "quadlet").glob("*.j2"))
    if not templates:
        pytest.skip("no quadlet templates yet")
    assert sorted(f.name for f in unit_dir.iterdir()) == sorted(t.name[:-3] for t in templates)
    r = subprocess.run(["/usr/lib/podman/quadlet", "-dryrun", "-user"], env={"QUADLET_UNIT_DIRS": str(unit_dir), "PATH": "/usr/bin"},
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("p", [p for p in placements() if "domain" in p.spec], ids=lambda p: f"{p.host}/{p.name}")
def test_route_rendered(rendered, p):
    route = rendered / p.host / "traefik-dynamic.d" / f"{p.name}.yml"
    assert route.exists()
    assert f"Host(`{p.spec['domain']}." in route.read_text()
```

- [ ] **Step 2: Run to verify it fails**

Run: `make test-static`
Expected: FAIL, `render_only` renders nothing yet (assertion on unit_dir).

- [ ] **Step 3: Write the role**

`ansible/roles/service/defaults/main.yml`:

```yaml
render_only: false
render_output: ''
volume_overrides: {}
```

`ansible/roles/service/tasks/main.yml`:

```yaml
- name: Derive service facts
  ansible.builtin.set_fact:
    svc_user: 'svc-{{ service.name }}'
    config_dir: '/etc/storagebaby/{{ service.name }}'
    fqdn: "{{ (service.domain ~ '.' ~ domain) if service.domain is defined else '' }}"
    volumes: {}

- name: Resolve volume paths
  ansible.builtin.set_fact:
    volumes: >-
      {{ volumes | combine({ item.key:
           (volume_overrides[service.name ~ '/' ~ item.key]
            | default(storage_roots[item.value.class] ~ '/' ~ service.name ~ '/' ~ item.key)) }) }}
  loop: '{{ service.volumes | default({}) | dict2items }}'
  loop_control:
    label: '{{ item.key }}'

- name: Resolve the secrets file (host override first)
  ansible.builtin.set_fact:
    secrets_file: >-
      {{ [storagebaby_repo_root ~ '/hosts/' ~ inventory_hostname ~ '/secrets/' ~ service.name ~ '.sops.yaml',
          service_dir ~ '/secrets.sops.yaml']
         | select('exists') | first | default('') }}

- name: Render only
  ansible.builtin.include_tasks: render.yml
  when: render_only

- name: Converge host
  ansible.builtin.include_tasks: host.yml
  when: not render_only
```

Note: the `exists` test on lookup paths runs on the controller, which is where both files live in every mode (ansible-pull: the checkout; Molecule: `/repo`).

`ansible/roles/service/tasks/render.yml`:

```yaml
- name: Render output dirs
  ansible.builtin.file:
    path: '{{ item }}'
    state: directory
    mode: '0755'
  loop:
    - '{{ render_output }}/{{ service.name }}'
    - '{{ render_output }}/traefik-dynamic.d'

- name: Render quadlet units
  ansible.builtin.template:
    src: '{{ item }}'
    dest: "{{ render_output }}/{{ service.name }}/{{ item | basename | regex_replace('\\.j2$', '') }}"
    mode: '0644'
  loop: "{{ lookup('fileglob', service_dir ~ '/quadlet/*.j2', wantlist=True) }}"
  loop_control:
    label: '{{ item | basename }}'

- name: Render traefik route
  ansible.builtin.template:
    src: traefik-route.yml.j2
    dest: '{{ render_output }}/traefik-dynamic.d/{{ service.name }}.yml'
    mode: '0644'
  when: service.domain is defined
```

`ansible/roles/service/tasks/host.yml`:

```yaml
- name: Service user
  ansible.builtin.user:
    name: '{{ svc_user }}'
    system: true
    shell: /usr/bin/nologin
    create_home: true

- name: Look up uid
  ansible.builtin.getent:
    database: passwd
    key: '{{ svc_user }}'

- name: Record uid and unit dir
  ansible.builtin.set_fact:
    svc_uid: '{{ getent_passwd[svc_user][1] }}'
    unit_dir: '/etc/containers/systemd/users/{{ getent_passwd[svc_user][1] }}'

- name: Check subordinate ids
  ansible.builtin.shell: grep -q '^{{ svc_user }}:' /etc/subuid && grep -q '^{{ svc_user }}:' /etc/subgid
  register: _subids
  changed_when: false
  failed_when: false

- name: Allocate subordinate ids
  ansible.builtin.command: usermod --add-subids {{ svc_user }}
  when: _subids.rc != 0

- name: Enable linger
  ansible.builtin.command: loginctl enable-linger {{ svc_user }}
  args:
    creates: /var/lib/systemd/linger/{{ svc_user }}

- name: Wait for the user manager
  ansible.builtin.command: systemctl --user -M {{ svc_user }}@ is-system-running --wait
  register: _mgr
  retries: 15
  delay: 2
  until: _mgr.rc == 0 or 'degraded' in _mgr.stdout
  changed_when: false

- name: Volume directories
  ansible.builtin.file:
    path: '{{ item.value }}'
    state: directory
    owner: '{{ svc_user }}'
    group: '{{ svc_user }}'
    mode: '0750'
  loop: '{{ volumes | dict2items }}'
  loop_control:
    label: '{{ item.key }} -> {{ item.value }}'

- name: Config directory
  ansible.builtin.file:
    path: '{{ config_dir }}'
    state: directory
    owner: '{{ svc_user }}'
    group: '{{ svc_user }}'
    mode: '0755'

- name: Config files
  ansible.builtin.copy:
    src: '{{ service_dir }}/config/'
    dest: '{{ config_dir }}/'
    owner: '{{ svc_user }}'
    group: '{{ svc_user }}'
    mode: 'u=rwX,go=rX'
  register: config_copy
  when: (service_dir ~ '/config') is directory

- name: Secrets
  ansible.builtin.include_tasks: secrets.yml
  when: service.secrets | length > 0

- name: Units
  ansible.builtin.include_tasks: units.yml
```

`ansible/roles/service/tasks/secrets.yml`:

```yaml
- name: Require a secrets file
  ansible.builtin.assert:
    that: secrets_file != ''
    fail_msg: '{{ service.name }} declares secrets but no secrets.sops.yaml was found'

- name: Copy the secrets file to the host
  ansible.builtin.copy:
    src: '{{ secrets_file }}'
    dest: '/etc/storagebaby/{{ service.name }}.secrets.sops.yaml'
    owner: root
    group: root
    mode: '0600'

- name: Decrypt on the host
  ansible.builtin.command: sops --decrypt --output-type json /etc/storagebaby/{{ service.name }}.secrets.sops.yaml
  environment:
    SOPS_AGE_KEY_FILE: /etc/storagebaby/age.key
  register: _decrypted
  changed_when: false
  no_log: true

- name: Parse
  ansible.builtin.set_fact:
    _secrets: '{{ _decrypted.stdout | from_json }}'
  no_log: true

- name: All declared secrets present
  ansible.builtin.assert:
    that: service.secrets | difference(_secrets.keys() | list) | length == 0
    fail_msg: 'missing in secrets file: {{ service.secrets | difference(_secrets.keys() | list) }}'

- name: Install the sync helper
  ansible.builtin.copy:
    src: podman-secret-sync.sh
    dest: /usr/local/sbin/podman-secret-sync
    mode: '0755'

- name: Sync secrets into podman
  ansible.builtin.command:
    cmd: /usr/local/sbin/podman-secret-sync {{ svc_user }} {{ item }}
    stdin: '{{ _secrets[item] }}'
    stdin_add_newline: false
  loop: '{{ service.secrets }}'
  register: secret_sync
  changed_when: "'CHANGED' in secret_sync.stdout"
  no_log: true
```

`ansible/roles/service/files/podman-secret-sync.sh`:

```bash
#!/bin/bash
# usage: podman-secret-sync <user> <secret-name>   (value on stdin)
# Creates or replaces a rootless Podman secret for <user>. Prints CHANGED or UNCHANGED.
set -euo pipefail
user=$1
name=$2
uid=$(id -u "$user")
value=$(cat)

run() { runuser -u "$user" -- env XDG_RUNTIME_DIR="/run/user/$uid" "$@"; }

if run podman secret exists "$name"; then
	current=$(run podman secret inspect --showsecret --format '{{.SecretData}}' "$name")
	if [ "$current" = "$value" ]; then
		echo UNCHANGED
		exit 0
	fi
fi
printf '%s' "$value" | run podman secret create --replace "$name" -
echo CHANGED
```

`ansible/roles/service/tasks/units.yml`:

```yaml
- name: Unit directory
  ansible.builtin.file:
    path: '{{ unit_dir }}'
    state: directory
    owner: root
    group: root
    mode: '0755'

- name: Render quadlet units
  ansible.builtin.template:
    src: '{{ item }}'
    dest: "{{ unit_dir }}/{{ item | basename | regex_replace('\\.j2$', '') }}"
    owner: root
    group: root
    mode: '0644'
  loop: "{{ lookup('fileglob', service_dir ~ '/quadlet/*.j2', wantlist=True) }}"
  loop_control:
    label: '{{ item | basename }}'
  register: quadlet_render

- name: Render traefik route
  ansible.builtin.template:
    src: traefik-route.yml.j2
    dest: '/etc/storagebaby/traefik/dynamic.d/{{ service.name }}.yml'
    owner: root
    group: root
    mode: '0644'
  when: service.domain is defined

- name: Map rendered files to systemd unit names
  ansible.builtin.set_fact:
    service_units: '{{ (service_units | default([])) + [unit_name] }}'
    changed_units: '{{ (changed_units | default([])) + ([unit_name] if item.changed else []) }}'
  vars:
    _base: '{{ item.dest | basename }}'
    _stem: '{{ (_base | splitext)[0] }}'
    _ext: '{{ (_base | splitext)[1] }}'
    unit_name: "{{ _stem ~ {'.container': '.service', '.pod': '-pod.service', '.build': '-build.service'}[_ext] }}"
  loop: '{{ quadlet_render.results }}'
  loop_control:
    label: '{{ item.dest | basename }}'
  when: (item.dest | basename | splitext)[1] in ['.container', '.pod', '.build']

- name: Restart everything if config or a secret changed
  ansible.builtin.set_fact:
    changed_units: '{{ service_units }}'
  when: (config_copy is defined and config_copy.changed) or (secret_sync is defined and secret_sync.changed)

- name: Reload the user manager
  ansible.builtin.command: systemctl --user -M {{ svc_user }}@ daemon-reload
  when: quadlet_render.changed
  changed_when: true

- name: Restart changed units
  ansible.builtin.command: systemctl --user -M {{ svc_user }}@ restart {{ item }}
  loop: '{{ changed_units | default([]) | unique }}'
  changed_when: true

- name: Check unit states
  ansible.builtin.command: systemctl --user -M {{ svc_user }}@ is-active {{ item }}
  loop: '{{ service_units | default([]) }}'
  register: _active
  changed_when: false
  failed_when: false

- name: Start inactive units
  ansible.builtin.command: systemctl --user -M {{ svc_user }}@ start {{ item.item }}
  loop: '{{ _active.results }}'
  loop_control:
    label: '{{ item.item }}'
  when: item.stdout.strip() != 'active'
  changed_when: true

- name: Check auto-update timer
  ansible.builtin.command: systemctl --user -M {{ svc_user }}@ is-enabled podman-auto-update.timer
  register: _au
  changed_when: false
  failed_when: false

- name: Enable auto-update timer
  ansible.builtin.command: systemctl --user -M {{ svc_user }}@ enable --now podman-auto-update.timer
  when: _au.stdout.strip() != 'enabled'
  changed_when: true

- name: Reset per-service unit facts
  ansible.builtin.set_fact:
    service_units: []
    changed_units: []
```

`ansible/roles/service/templates/traefik-route.yml.j2`:

```yaml
http:
  routers:
    {{ service.name }}:
      rule: "Host(`{{ fqdn }}`)"
      entryPoints: [websecure]
      service: {{ service.route.internal | default(service.name) }}
{% if acme and (service.route.wildcard_cert | default(false)) %}
      tls:
        certResolver: porkbun
        domains:
          - main: "{{ domain }}"
            sans: ["*.{{ domain }}"]
{% else %}
      tls: {}
{% endif %}
{% if service.route.internal is not defined %}
  services:
    {{ service.name }}:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:{{ service.port }}"
{% endif %}
```

- [ ] **Step 4: Generate test secrets in prepare**

Append to `tests/integration/molecule/test-ci/prepare.yml` a second play:

```yaml
- name: Generate throwaway secrets for every placed service, encrypted to the host key
  hosts: all
  gather_facts: false
  vars:
    _hosts_root: '{{ storagebaby_repo_root }}/hosts'
  tasks:
    - name: Discover shared services (fileglob cannot expand a wildcard directory; use find)
      ansible.builtin.find:
        paths: '{{ _hosts_root }}/shared/services'
        patterns: service.yml
        recurse: true
        depth: 2
        follow: true
      register: _shared
      delegate_to: localhost
      become: false

    - name: Discover this host's services
      ansible.builtin.find:
        paths: '{{ _hosts_root }}/{{ inventory_hostname }}/services'
        patterns: service.yml
        recurse: true
        depth: 2
        follow: true
      register: _own
      delegate_to: localhost
      become: false

    - name: Placed services, shared first
      ansible.builtin.set_fact:
        _placed: "{{ (_shared.files | map(attribute='path') | sort) + (_own.files | map(attribute='path') | sort) }}"

    - name: Secrets override dir on the controller
      ansible.builtin.file:
        path: '{{ _hosts_root }}/{{ inventory_hostname }}/secrets'
        state: directory
        mode: '0755'
      delegate_to: localhost

    - name: Write and encrypt one secrets file per service
      ansible.builtin.shell: |
        set -euo pipefail
        {% for key in (lookup('file', item) | from_yaml).secrets %}
        printf '%s: "%s"\n' '{{ key }}' "$(head -c 24 /dev/urandom | base64 | tr -d '/+=')" >> "$TMP"
        {% endfor %}
        sops --encrypt --age {{ storagebaby_age_pub }} --input-type yaml --output-type yaml "$TMP" > "{{ _hosts_root }}/{{ inventory_hostname }}/secrets/{{ item | dirname | basename }}.sops.yaml"
      args:
        executable: /bin/bash
      environment:
        TMP: '/tmp/{{ inventory_hostname }}-{{ item | dirname | basename }}-secrets.yaml'
      loop: '{{ _placed }}'
      loop_control:
        label: '{{ item | dirname | basename }}'
      when: (lookup('file', item) | from_yaml).secrets | length > 0
      delegate_to: localhost
      changed_when: true
```

(`$TMP` is removed at the start of each run: prepend `rm -f "$TMP"` as the first line after `set`.)

- [ ] **Step 5: Run static tests**

Run: `make test-static`
Expected: `test_render.py` skips (no templates yet), everything else passes, the playbook syntax check passes.

- [ ] **Step 6: Run the scenario**

Run: `make test-integration`
Expected: converge creates `svc-traefik` with no units yet (no templates), the route file for traefik is rendered, idempotence passes. Nested-Podman and host_base tests still pass.

- [ ] **Step 7: Commit**

```bash
git add ansible/roles/service tests
git commit -m "Add generic service role: user, volumes, secrets, quadlet rendering, routes"
```

---

### Task 8: Traefik on the new pattern

**Files:**

- Create: `hosts/shared/services/traefik/quadlet/traefik.container.j2`
- Create: `hosts/shared/services/traefik/README.md`
- Modify: `tests/integration/molecule/test-ci/converge.yml` (drop the spike play; keep only the import)
- Modify: `tests/integration/molecule/test-ci/tests/test_rootless_podman.py` (probe user → `svc-traefik`)
- Create: `tests/integration/molecule/test-ci/tests/test_traefik.py`

**Interfaces:**

- Consumes: template variables from Task 7, secrets `porkbun_api_key`, `porkbun_secret_api_key`, volume `letsencrypt`, host vars `acme`, `acme_email`, `tz`.
- Produces: `traefik.service` under `svc-traefik`, listening on 80/443 on all interfaces and 8080 on loopback, file provider on `/etc/storagebaby/traefik/dynamic.d`, dashboard at `https://traefik.<domain>/dashboard/`.

- [ ] **Step 1: Write the tests**

`tests/integration/molecule/test-ci/tests/test_traefik.py`:

```python
def run_as(host, user, cmd):
    uid = host.user(user).uid
    return host.run(f"runuser -u {user} -- env XDG_RUNTIME_DIR=/run/user/{uid} {cmd}")


def test_unit_active(host):
    assert host.run("systemctl --user -M svc-traefik@ is-active traefik.service").stdout.strip() == "active"


def test_container_healthy(host):
    r = run_as(host, "svc-traefik", "podman healthcheck run traefik")
    assert r.rc == 0, r.stderr


def test_secrets_mounted(host):
    r = run_as(host, "svc-traefik", "podman exec traefik ls /run/secrets")
    assert {"porkbun_api_key", "porkbun_secret_api_key"} <= set(r.stdout.split())


def test_dashboard_via_https(host):
    r = host.run("curl -sk -o /dev/null -w '%{http_code}' -H 'Host: traefik.test.local' https://127.0.0.1/dashboard/")
    assert r.stdout.strip() == "200"


def test_http_redirects_to_https(host):
    r = host.run("curl -s -o /dev/null -w '%{http_code}' -H 'Host: traefik.test.local' http://127.0.0.1/")
    assert r.stdout.strip() in {"301", "302", "308"}


def test_letsencrypt_volume_owned_by_service_user(host):
    f = host.file("/srv/fast/traefik/letsencrypt")
    assert f.is_directory and f.user == "svc-traefik"


def test_no_root_containers(host):
    assert host.run("podman ps -q").stdout.strip() == ""


def test_auto_update_timer_enabled(host):
    assert host.run("systemctl --user -M svc-traefik@ is-enabled podman-auto-update.timer").stdout.strip() == "enabled"
```

Rewrite `test_rootless_podman.py` to use `svc-traefik` instead of `svc-probe` (same three tests; the port-80 test stays because Traefik holds 80 now: change it to assert the sysctl only and that `ss -ltnp` shows `:80` and `:443` owned by a `traefik` process).

- [ ] **Step 2: Run verify to see them fail**

Run: `make molecule CMD=verify`
Expected: traefik tests fail (no unit).

- [ ] **Step 3: Write the unit template and README**

`hosts/shared/services/traefik/quadlet/traefik.container.j2`:

```ini
[Unit]
Description=Traefik reverse proxy

[Container]
Image=docker.io/library/traefik:v3
ContainerName=traefik
AutoUpdate=registry
Network=host
Exec=--api --ping=true \
  --providers.file.directory=/etc/traefik/dynamic.d --providers.file.watch=true \
  --entrypoints.web.address=:80 --entrypoints.websecure.address=:443 \
  --entrypoints.traefik.address=127.0.0.1:{{ service.port }} \
  --entrypoints.web.http.redirections.entrypoint.to=websecure \
  --entrypoints.web.http.redirections.entrypoint.scheme=https{% if acme %} \
  --certificatesresolvers.porkbun.acme.email={{ acme_email }} \
  --certificatesresolvers.porkbun.acme.storage=/letsencrypt/acme.json \
  --certificatesresolvers.porkbun.acme.dnschallenge.provider=porkbun \
  --certificatesresolvers.porkbun.acme.dnschallenge.resolvers=1.1.1.1:53,8.8.8.8:53{% endif %}

Environment=TZ={{ tz }}
Environment=PORKBUN_API_KEY_FILE=/run/secrets/porkbun_api_key
Environment=PORKBUN_SECRET_API_KEY_FILE=/run/secrets/porkbun_secret_api_key
Secret=porkbun_api_key
Secret=porkbun_secret_api_key
HealthCmd=wget --spider -q http://127.0.0.1:{{ service.port }}/ping || exit 1
HealthOnFailure=kill
Volume={{ volumes.letsencrypt }}:/letsencrypt
Volume=/etc/storagebaby/traefik/dynamic.d:/etc/traefik/dynamic.d:ro

[Service]
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

`hosts/shared/services/traefik/README.md`:

```markdown
# Traefik

Rootless Traefik v3 on the host network under `svc-traefik`. It is the only
process on 80/443. Every other service binds `127.0.0.1:<port>` and gets a
route file rendered by the `service` role into
`/etc/storagebaby/traefik/dynamic.d/<name>.yml` from its `service.yml`
(`domain`, `port`, optional `route.internal` / `route.wildcard_cert`).

## Certificates

With `acme: true` in the host's `host.yml`, the dashboard router carries the
`porkbun` resolver and the wildcard domains, which is what triggers issuance;
all other routers just say `tls: {}` and inherit the wildcard cert. State lives
in the `letsencrypt` volume (`fast` class). With `acme: false` Traefik serves
its self-signed default certificate (test hosts).

ACME is router-driven, not entrypoint-driven: only a router with a
`certResolver` and `domains` triggers a request. lego checks DNS propagation
through public resolvers (`1.1.1.1`, `8.8.8.8`) because a local resolver that
is authoritative for the domain never sees the TXT record.

## Secrets

`porkbun_api_key`, `porkbun_secret_api_key` from `secrets.sops.yaml`, mounted
at `/run/secrets/*` and read through lego's `_FILE` convention.
```

`tests/integration/molecule/test-ci/converge.yml` becomes exactly:

```yaml
- import_playbook: /repo/ansible/playbook.yml
```

- [ ] **Step 4: Run static tests**

Run: `make test-static`
Expected: `test_render.py` renders `traefik.container` for both hosts and `quadlet -dryrun` passes; `test_template_secrets_are_declared` passes.

- [ ] **Step 5: Run the scenario**

Run: `make test-integration`
Expected: all tests pass, idempotence passes. Known failure modes: (a) `is-active` not `active` → `journalctl --user -M svc-traefik@ -u traefik.service` inside the container via `make molecule CMD=login`; (b) 80/443 bind refused → sysctl not applied in the container network namespace; (c) health check fails because the image lacks `wget` → switch `HealthCmd` to `traefik healthcheck --ping`.

- [ ] **Step 6: Commit**

```bash
git add hosts/shared/services/traefik tests
git commit -m "Run Traefik rootless on Quadlet with a file-provider route per service"
```

---

### Task 9: Deploy path and idempotence proof

**Files:**

- Modify: `tests/integration/molecule/test-ci/prepare.yml` (bare repo from the working tree)
- Create: `tests/integration/molecule/test-ci/tests/test_deploy.py`

**Interfaces:**

- Produces on the test host: `/srv/src` (a git checkout of the working tree, branch `stable`) and `/srv/storagebaby.git` (bare) which `deploy.conf` already points at.
- Produces: proof that `storagebaby-deploy.service` converges from the bare repo, and that a change to one service restarts exactly that service.

- [ ] **Step 1: Write the deploy test**

`tests/integration/molecule/test-ci/tests/test_deploy.py`:

```python
import pytest


def active_since(host, user, unit):
    return host.run(f"systemctl --user -M {user}@ show {unit} -p ActiveEnterTimestampMonotonic --value").stdout.strip()


@pytest.mark.order(-1)
def test_deploy_service_is_a_noop_when_nothing_changed(host):
    before = active_since(host, "svc-traefik", "traefik.service")
    r = host.run("systemctl start storagebaby-deploy.service")
    assert r.rc == 0, host.run("journalctl -u storagebaby-deploy.service --no-pager | tail -50").stdout
    assert host.file("/var/lib/storagebaby/repo/ansible/playbook.yml").exists
    assert active_since(host, "svc-traefik", "traefik.service") == before


@pytest.mark.order(-1)
def test_deploy_restarts_only_the_changed_service(host):
    before = active_since(host, "svc-traefik", "traefik.service")
    r = host.run(
        "cd /srv/src && sed -i 's/^tz: .*/tz: Europe\\/Vienna/' hosts/test-a/host.yml "
        "&& git -c user.name=t -c user.email=t@t commit -qam 'change tz' && git push -q origin stable"
    )
    assert r.rc == 0, r.stderr
    r = host.run("systemctl start storagebaby-deploy.service")
    assert r.rc == 0, host.run("journalctl -u storagebaby-deploy.service --no-pager | tail -50").stdout
    assert active_since(host, "svc-traefik", "traefik.service") != before
    assert host.run("systemctl --user -M svc-traefik@ is-active traefik.service").stdout.strip() == "active"
```

Add `pytest-order` to `devtools/requirements.txt` and rebuild (`make devtools`) so the deploy tests run last.

- [ ] **Step 2: Run verify to see them fail**

Run: `make molecule CMD=verify`
Expected: FAIL, `/srv/storagebaby.git` does not exist.

- [ ] **Step 3: Seed the bare repo in prepare**

Append a third play to `prepare.yml`:

```yaml
- name: Seed a local git remote from the working tree
  hosts: all
  gather_facts: false
  tasks:
    - name: Archive the working tree on the controller (no .git, no caches)
      ansible.builtin.command:
        cmd: tar -C /repo --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache --exclude=node_modules -czf /tmp/{{ inventory_hostname }}-src.tgz .
      delegate_to: localhost
      changed_when: true

    - name: Create /srv/src
      ansible.builtin.file:
        path: /srv/src
        state: directory
        mode: '0755'

    - name: Copy the archive
      ansible.builtin.copy:
        src: /tmp/{{ inventory_hostname }}-src.tgz
        dest: /tmp/src.tgz
        mode: '0644'

    - name: Extract
      ansible.builtin.command: tar -C /srv/src -xzf /tmp/src.tgz
      changed_when: true

    - name: Init repo and bare remote
      ansible.builtin.shell: |
        set -e
        cd /srv/src
        git init -q -b stable
        git -c user.name=t -c user.email=t@t add -A
        git -c user.name=t -c user.email=t@t commit -qm 'seed'
        git clone -q --bare /srv/src /srv/storagebaby.git
        git remote add origin /srv/storagebaby.git
      args:
        creates: /srv/storagebaby.git
```

- [ ] **Step 4: Run the scenario**

Run: `make test-integration`
Expected: everything passes including the two deploy tests. The first deploy run clones into `/var/lib/storagebaby/repo` and reports no changes; the second restarts Traefik because its rendered unit's `TZ` changed.

- [ ] **Step 5: Commit**

```bash
git add devtools/requirements.txt tests
git commit -m "Prove the pull-based deploy path end to end in the integration scenario"
```

---

### Task 10: CI workflow, README, spec touch-up

**Files:**

- Create: `.github/workflows/ci.yml`
- Modify: `README.md`, `CLAUDE.md` (sections "Managing Docker services", "Formatting", "Deployment")
- Modify: `docs/superpowers/specs/2026-09-21-gitops-podman-platform-design.md` (record the two agreed deviations: test hosts under `hosts/test-*`, per-host secrets override)

- [ ] **Step 1: Write the workflow**

`.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [master, podman-platform]
  pull_request:

jobs:
  static:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: make devtools
      - run: make fmt-check
      - run: make test-static

  integration:
    runs-on: ubuntu-latest
    needs: static
    steps:
      - uses: actions/checkout@v4
      - run: make devtools
      - run: make test-integration SCENARIO=test-ci

  promote:
    runs-on: ubuntu-latest
    needs: integration
    if: github.ref == 'refs/heads/master' && github.event_name == 'push'
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Fast-forward stable to the tested commit
        run: git push origin HEAD:refs/heads/stable
```

Note: the integration job needs libvirt with KVM on the runner. Before `make test-integration` add: `- run: sudo apt-get update && sudo apt-get install -y qemu-kvm libvirt-daemon-system dnsmasq-base && sudo systemctl start libvirtd && sudo virsh net-start default || true && sudo chmod 666 /dev/kvm /var/run/libvirt/libvirt-sock`. GitHub-hosted Ubuntu runners expose `/dev/kvm`.

- [ ] **Step 2: Rewrite README.md**

```markdown
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

Each service folder holds `service.yml` (port, domain, volumes, secrets,
backup policy), `quadlet/*.j2` (Podman Quadlet units), optional `config/`, and
`secrets.sops.yaml`.

## New host

As root on a fresh Arch install:

    curl -fsSL https://raw.githubusercontent.com/janlucaklees/StorageBaby/stable/bootstrap.sh | bash -s -- --repo git@github.com:janlucaklees/StorageBaby.git

Add the printed deploy key to the repository, add the printed age recipient to
`.sops.yaml` under the host's rule, run `make sops FILE=...` → `sops updatekeys`
on the affected secrets, commit, push. The host pulls `stable` every 5 minutes.

## Working on the repo

    make devtools          # build the tooling image (once)
    make test-static       # contract, secrets, render checks
    make test-integration  # Molecule scenario test-ci (needs Docker)
    make sops FILE=hosts/shared/services/traefik/secrets.sops.yaml

Nothing besides Docker and lefthook is needed on the workstation.
```

Update `CLAUDE.md`: replace the "Managing Docker services" section with a pointer to the layout above and the `make` targets, replace "Deployment" with the bootstrap + `stable` flow, keep storage/snapraid sections unchanged (they move in Phase 4).

- [ ] **Step 3: Spec touch-up**

In the spec's section 8, replace the sentence starting "Their folders live at `tests/integration/hosts/<name>/`" with: "Test hosts are ordinary host folders `hosts/test-a/` etc., so the deploy path is production-identical; they place services by symlinking into `hosts/storagebaby/services/`." In section 6 add the bullet: "A host may override a service's secrets with `hosts/<host>/secrets/<service>.sops.yaml`; tests use this for every placed service, encrypted to the test host's bootstrap key."

- [ ] **Step 4: Run everything**

Run: `make fmt-check && make test`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add .github README.md CLAUDE.md docs
git commit -m "Add CI with stable promotion and document the new workflow"
```

---

## Self-review notes

- Spec coverage for Phase 1: §2 layout (T2), §3 bootstrap/deploy (T4, T9), §4 host config (T2, T6), §5 service contract and role (T7), §6 secrets (T3, T7), §7 Traefik (T8), §8 tests static + integration + pipeline (T1–T10), §10 step 1 complete. §5 timers, `.pod`, `.build`, `UserNS=keep-id`, stale unit removal, and §9 maintenance hooks are Phase 2–4 and are not in this plan.
- Names used across tasks: `svc_user`, `volumes`, `config_dir`, `fqdn`, `service`, `service_dir`, `storagebaby_repo_root`, `render_only`, `render_output`, `placed_services`, `deploy_timer`, `storagebaby_age_pub`, `podman-secret-sync` — consistent between T6, T7, T8, T9.
