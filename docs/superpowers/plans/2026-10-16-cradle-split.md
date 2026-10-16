# Cradle Split (Stage 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task, inline, in this session. Two repositories, a live host and a pre-split baseline are context a fresh subagent would not have. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the engine out of StorageBaby into a new repository, Cradle, with no behaviour change on the host beyond the rename of Cradle's own paths.

**Architecture:** Cradle is the Ansible entry point and takes a host folder as input (`-e host_dir=…`); a deploy tick fetches Cradle, the host repository and every pointed service repository, each at a branch, and converges when any moved. Cradle's tests render and converge a fixture host repository that holds today's test hosts and the real services they place, copied. StorageBaby keeps `hosts/`, its secrets and services, and loses everything else.

**Tech Stack:** Ansible (ansible-core 2.21), Podman Quadlet, Molecule + testinfra in a KVM VM, pytest, sops/age, mise, Docker devtools image, GitHub Actions, `gh`.

**Spec:** `docs/superpowers/specs/2026-10-16-cradle-split-design.md` (this repository; copied into Cradle in Task 10).

## Global Constraints

- Cradle lives at `/home/jlk/Projects/cradle`, GitHub `janlucaklees/cradle`, **private** at first. StorageBaby work happens on branch `cradle-split` in a worktree under `.claude/worktrees/cradle-split`.
- **No behaviour change on the host** except the rename: render parity (Task 13) is the proof.
- Paths on a host are Cradle's and nothing else's: `/etc/cradle`, `/var/lib/cradle`, `/opt/cradle`, `cradle-deploy.{service,timer}`, `cradle-svc`, `10-cradle-hosts.conf`, `70-cradle-render.rules`. `storage-maintenance.*` units keep their names. **`/var/lib/storagebaby/fast` is a host-declared data path and is never moved.**
- Deploy config is five explicit values: `CRADLE_REPO`, `CRADLE_REF`, `HOST_REPO`, `HOST_REF`, `HOST_PATH`. The hostname is never consulted by the engine.
- One Molecule run at a time on this workstation. No Molecule run while the live host is being cut over.
- Commit at the end of every task, via the `commit` skill, only after the task's check passed. Never `--no-verify`. Pushes only in Tasks 11, 15 and 16.
- Run tasks through `mise run …` where one exists. The one exception is Task 1's baseline render, which uses the same docker invocation mise's `test-static` task wraps.
- JLK's age key is never read. Cradle's fixtures generate their own secrets.

---

### Task 1: Baseline render of StorageBaby

**Files:**

- Create: `/tmp/claude-1000/-home-jlk-Projects-StorageBaby/205176ee-280c-48c7-baa9-d3b66f2e4832/scratchpad/baseline/` (outside both repositories)

**Interfaces:**

- Produces: `baseline/render/` (the rendered tree) and `baseline/HEAD` (the SHA it was rendered from). Task 13 diffs against it.

- [ ] **Step 1: Record the SHA and render with the current engine**

```bash
cd /home/jlk/Projects/StorageBaby
B=/tmp/claude-1000/-home-jlk-Projects-StorageBaby/205176ee-280c-48c7-baa9-d3b66f2e4832/scratchpad/baseline
mkdir -p "$B/render"
git rev-parse HEAD > "$B/HEAD"
docker run --rm -t -v "$PWD":/repo -v "$B/render":/out -w /repo -e HOME=/root storagebaby-devtools \
	ansible-playbook -i StorageBaby, -c local ansible/playbook.yml \
	-e render_only=true -e render_output=/out -e ansible_become=false \
	-e tcp_bind_address=203.0.113.1 -e sources_root=/repo/tests/fixtures/sources
```

- [ ] **Step 2: Verify the baseline holds every placed service**

Run: `ls "$B/render"` and `find "$B/render" -type f | wc -l`
Expected: one directory per StorageBaby service (calino … yuzukam, traefik) and a non-zero file count. renaissance is absent (its repository is not fetched; the playbook says so and skips it).

No commit: nothing in a repository changed.

---

### Task 2: Import the engine into a new Cradle repository

**Files:**

- Create: `/home/jlk/Projects/cradle/` with `ansible/`, `bootstrap.sh`, `devtools/`, `mise.toml`, `lefthook.yml`, `.ansible-lint`, `.prettierrc`, `.prettierignore`, `.env.example`, `.github/workflows/ci.yml`, `tests/`, `docs/`, `.claude/skills/adding-a-service/`, `.gitignore`, `hosts/` (temporary, moved in Task 3)

**Interfaces:**

- Produces: a git repository whose first commit is a verbatim copy of the engine at StorageBaby `HEAD`.

- [ ] **Step 1: Create the repository and copy the engine**

```bash
S=/home/jlk/Projects/StorageBaby
C=/home/jlk/Projects/cradle
mkdir -p "$C" && cd "$C" && git init -q -b master
cd "$S"
for p in ansible bootstrap.sh devtools mise.toml lefthook.yml .ansible-lint .prettierrc .prettierignore .env.example .github tests docs .gitignore; do
	cp -a "$p" "$C/"
done
mkdir -p "$C/.claude/skills" && cp -a .claude/skills/adding-a-service "$C/.claude/skills/"
mkdir -p "$C/hosts" && cp -a hosts/shared hosts/test-a hosts/test-ci "$C/hosts/"
# test-a and test-ci symlink into ../../StorageBaby/services/<name>: materialise those as real folders.
cd "$C/hosts"
for l in test-a/services/* test-ci/services/*; do
	if [ -L "$l" ]; then
		rm "$l"
		cp -a "$S/hosts/StorageBaby/services/$(basename "$l")" "$l"
	fi
done
# Nothing of JLK's data or local state comes along.
rm -rf "$C/docs/runbooks" "$C/hosts/test-a/secrets" "$C/hosts/test-ci/secrets" "$C/tests/static/__pycache__"
find "$C" -name __pycache__ -prune -exec rm -rf {} +
```

- [ ] **Step 2: Check the copy has no StorageBaby-only material**

Run: `cd /home/jlk/Projects/cradle && ls hosts && find hosts -type l | wc -l && rg -l "klees\.io" hosts | wc -l`
Expected: `shared test-a test-ci`, `0` symlinks, and `0` files with JLK's domain under `hosts/` (test hosts use `test.local`; service folders carry only labels or full names under `klees.io` in their `service.yml` — if that last count is not 0, list the files and leave them: a service's own full domain is the service's fact and the fixture keeps it).

- [ ] **Step 3: Commit the import**

```bash
cd /home/jlk/Projects/cradle && git add -A && git commit -q -m "chore: import the engine from StorageBaby at $(cat /tmp/claude-1000/-home-jlk-Projects-StorageBaby/205176ee-280c-48c7-baa9-d3b66f2e4832/scratchpad/baseline/HEAD)

Verbatim copy of ansible/, bootstrap.sh, the tests, the docs and the dev
tooling, plus the two test hosts with the service folders they place
materialised from symlinks. Nothing is renamed yet; the next commits make
this Cradle. Design: docs/superpowers/specs/2026-10-16-cradle-split-design.md.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

(Direct `git commit` here on purpose: the `commit` skill stages this thread's work in StorageBaby; Cradle has no hooks yet. From Task 4 on, Cradle commits go through `mise run install-hooks` + the skill.)

---

### Task 3: The host folder becomes the playbook's input; the fixture host repository

**Files:**

- Modify: `ansible/playbook.yml` (vars, pre_tasks, every `storagebaby_repo_root`/`inventory_hostname` use)
- Modify: `ansible/roles/service/tasks/main.yml:36,97`, `ansible/roles/service/tasks/secrets.yml:79`, `ansible/roles/service/tasks/registries.yml:42-52,80,90`, `ansible/roles/storage/tasks/mail.yml:7,25`, `ansible/roles/storage/tasks/mounts.yml:45,191`
- Delete: `ansible/inventory/hosts.yml`; Modify: `ansible/ansible.cfg` (drop `inventory =`)
- Move: `hosts/{shared,test-a,test-ci}` → `tests/fixtures/host-repo/hosts/`
- Modify: `tests/static/conftest.py:8-9,33,171-195`, `.gitignore`, `.prettierignore`
- Modify: `tests/static/test_bootstrap.py` (nothing yet), `tests/static/test_smoke.py:33` (`-i localhost,`)

**Interfaces:**

- Consumes: nothing.
- Produces: the playbook requires `-e host_dir=<abs path to hosts/<name>>`; derives `host_name = host_dir | basename`, `hosts_root = host_dir | dirname`, `cradle_root = playbook_dir/..`. `tests/static/conftest.py` exposes `HOST_REPO = REPO/"tests/fixtures/host-repo"`, `HOSTS = HOST_REPO/"hosts"`, `host_dir(host) -> Path`.

- [ ] **Step 1: Move the test hosts into the fixture host repository**

```bash
cd /home/jlk/Projects/cradle
mkdir -p tests/fixtures/host-repo
git mv hosts tests/fixtures/host-repo/hosts
# test-ci places what test-a places: symlink back into test-a so the identical-copies rule holds by construction.
cd tests/fixtures/host-repo/hosts/test-ci/services
for d in kopia paperless yuzukam; do
	rm -rf "$d"
	ln -s "../../test-a/services/$d" "$d"
done
cd /home/jlk/Projects/cradle
sed -i 's|^hosts/test-\*/secrets/$|tests/fixtures/host-repo/hosts/test-*/secrets/|' .gitignore .prettierignore
```

- [ ] **Step 2: Write the failing static test for the input contract**

Create `tests/static/test_host_dir.py`:

```python
"""The playbook reads one host folder, named explicitly: `-e host_dir=<repo>/hosts/<name>`."""

import subprocess

from conftest import REPO


def test_playbook_refuses_to_run_without_host_dir():
    proc = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", "-c", "local", "ansible/playbook.yml",
         "-e", "render_only=true", "-e", "ansible_become=false"],
        capture_output=True, text=True, cwd=str(REPO), check=False,
    )
    assert proc.returncode != 0
    assert "host_dir" in proc.stdout + proc.stderr


def test_playbook_never_reads_the_hostname():
    text = (REPO / "ansible/playbook.yml").read_text()
    for role in ["service", "host_base", "storage"]:
        for f in (REPO / "ansible/roles" / role / "tasks").glob("*.yml"):
            text += f.read_text()
    assert "inventory_hostname" not in text
    assert "storagebaby_repo_root" not in text
```

- [ ] **Step 3: Run it to see it fail**

Run: `cd /home/jlk/Projects/cradle && docker run --rm -t -v "$PWD":/repo -w /repo -e HOME=/root storagebaby-devtools pytest tests/static/test_host_dir.py -v`
Expected: both FAIL (`inventory_hostname` present; the playbook runs without `host_dir` and fails on a different message, or succeeds).

- [ ] **Step 4: Rewrite the playbook's input**

In `ansible/playbook.yml` replace the `vars:` head and the first pre_task:

```yaml
vars:
  cradle_root: '{{ playbook_dir }}/..'
  # The one input: the folder holding this host's host.yml, inside a checkout of the
  # host repository. Explicit, never derived from the machine: the deploy script reads
  # HOST_PATH from /etc/cradle/deploy.conf and passes it here.
  host_name: '{{ host_dir | basename }}'
  hosts_root: '{{ host_dir | dirname }}'
  render_only: false
  render_output: ''
  tcp_bind_address: '{{ ansible_default_ipv4.address }}'
  cert_zones: '{{ [domain] }}'
pre_tasks:
  - name: The host folder this converge reads
    ansible.builtin.assert:
      that:
        - host_dir is defined
        - (host_dir ~ '/host.yml') is file
      fail_msg: >-
        pass -e host_dir=<host repository checkout>/hosts/<name>, the folder that holds
        host.yml. Cradle reads exactly one host folder and never guesses it.
      quiet: true

  - name: Load host configuration
    ansible.builtin.include_vars:
      file: '{{ host_dir }}/host.yml'
```

Keep the existing comments above `tcp_bind_address` and `cert_zones`. Then, with sed, the remaining references:

```bash
cd /home/jlk/Projects/cradle
sed -i "s|{{ storagebaby_repo_root }}/hosts/shared/services|{{ hosts_root }}/shared/services|g; s|{{ storagebaby_repo_root }}/hosts/{{ inventory_hostname }}/services|{{ host_dir }}/services|g; s|ports claimed twice on {{ inventory_hostname }}|ports claimed twice on {{ host_name }}|" ansible/playbook.yml
sed -i "s|hostname: '{{ inventory_hostname }}'|hostname: '{{ host_name }}'|; s|storagebaby_repo_root ~ '/hosts/' ~ inventory_hostname ~ '/secrets/'|host_dir ~ '/secrets/'|" ansible/roles/service/tasks/main.yml
sed -i "s|{{ storagebaby_repo_root }}/hosts/{{ inventory_hostname }}/secrets/|{{ host_dir }}/secrets/|" ansible/roles/service/tasks/secrets.yml ansible/roles/service/tasks/registries.yml
sed -i "s|{{ inventory_hostname }} does not offer|{{ host_name }} does not offer|; s|in hosts/{{ inventory_hostname }}/host.yml|in {{ host_dir }}/host.yml|; s|hosts/{{ inventory_hostname }}/secrets/registries.sops.yaml|{{ host_dir }}/secrets/registries.sops.yaml|; s|on {{ inventory_hostname }}|on {{ host_name }}|g" ansible/roles/service/tasks/registries.yml
sed -i "s|{{ storagebaby_repo_root }}/hosts/{{ inventory_hostname }}/secrets/mail.sops.yaml|{{ host_dir }}/secrets/mail.sops.yaml|; s|(storagebaby_repo_root ~ '/hosts/' ~ inventory_hostname ~ '/secrets/mail.sops.yaml')|(host_dir ~ '/secrets/mail.sops.yaml')|" ansible/roles/storage/tasks/mail.yml
sed -i "s|hosts/{{ inventory_hostname }}/host.yml|{{ host_dir }}/host.yml|" ansible/roles/storage/tasks/mounts.yml
git rm -q ansible/inventory/hosts.yml
sed -i '/^inventory = /d' ansible/ansible.cfg
sed -i 's|"-i", "StorageBaby,"|"-i", "localhost,"|' tests/static/test_smoke.py
rg -n "inventory_hostname|storagebaby_repo_root" ansible/ # must print nothing
```

`ansible/roles/service/tasks/main.yml:97` builds a list with `~`; check the sed landed by reading lines 95-100 after it.

- [ ] **Step 5: Point the static suite at the fixture host repository**

In `tests/static/conftest.py` replace lines 8-9 and 33 and the `rendered` fixture body:

```python
REPO = Path(__file__).resolve().parents[2]
# The fixture host repository: what a real host repository looks like to Cradle, holding
# the two test hosts and the services they place. Everything the static suite checks
# about a *placement* is checked against this tree.
HOST_REPO = REPO / "tests" / "fixtures" / "host-repo"
HOSTS = HOST_REPO / "hosts"
FIXTURE_SOURCES = REPO / "tests" / "fixtures" / "sources"


def host_dir(host: str) -> Path:
    return HOSTS / host
```

and in `rendered`, the subprocess arguments:

```python
            [
                "ansible-playbook", "-i", "localhost,", "-c", "local", "ansible/playbook.yml",
                "-e", f"host_dir={host_dir(host)}",
                "-e", "render_only=true",
                "-e", f"render_output={out / host}",
                "-e", "ansible_become=false",
                "-e", f"tcp_bind_address={RENDER_TCP_BIND_ADDRESS}",
                "-e", f"sources_root={FIXTURE_SOURCES}",
            ],
```

Then every other static test that builds `REPO / "hosts"` by hand: `rg -n '"hosts"' tests/static` and switch each to `HOSTS` from conftest.

- [ ] **Step 6: Run the static suite**

Run: `cd /home/jlk/Projects/cradle && docker run --rm -t -v "$PWD":/repo -w /repo -e HOME=/root storagebaby-devtools pytest tests/static -v`
Expected: all PASS, including the two new ones. `test_contract.py`'s identical-copies test sees test-ci symlinking into test-a. `test_secrets.py` reads the recipients config at `REPO / ".sops.yaml"`, which Cradle does not have: give the fixture host repository its own, `tests/fixtures/host-repo/.sops.yaml`, and make the test read `HOST_REPO / ".sops.yaml"` with paths relative to `HOST_REPO`:

```yaml
# The fixture host repository's recipients. The service folders under hosts/test-a are
# copies of StorageBaby's and carry its encrypted secrets, so the rule names the same two
# recipients those files were encrypted to; a test VM never decrypts them -- prepare.yml
# generates a host override per service, encrypted to the VM's own key.
keys:
  - &jlk age16eygwufge3085w0xqqw2pmj0w7h93465s6u0e3sg7ueaxlpxhcnqyfmgd5
  - &storagebaby age142s7lfpzjdddrrxkee8gr057u9hqkanu220n4f6hvzmr9n66x4rs9w3zvs
creation_rules:
  - path_regex: ^hosts/(test-a|shared)/.*\.sops\.yaml$
    key_groups:
      - age: [*jlk, *storagebaby]
```

- [ ] **Step 7: Commit**

```bash
cd /home/jlk/Projects/cradle && git add -A && git commit -q -m "feat: the playbook takes one host folder as input

-e host_dir=<checkout>/hosts/<name> replaces the inventory name and the
repository root: Cradle reads exactly the folder it is told to and never
derives it from the machine. The test hosts move into a fixture host
repository, which is what the static suite renders from now on.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The Molecule harness converges the fixture host repository

**Files:**

- Modify: `tests/integration/molecule/test-ci/molecule.yml` (group_vars `host_dir`), `prepare.yml` (every `storagebaby_repo_root`/`_hosts_root`, the seed play, the preflight), `tests/integration/molecule/test-ci/tests/test_service.py:16-17`, `test_storage.py:13`, `test_deploy.py:44-46,160-163,373,950`, `test_host_base.py:10`, `test_traefik.py:101`, `test_ftp.py:50`, `tests/integration/molecule/test-ci/tests/conftest.py`

**Interfaces:**

- Consumes: Task 3's `host_dir`.
- Produces: on the VM, `/srv/src-host` (the seeded host repository working copy) and `/srv/host.git` (its bare remote); `/srv/src-cradle` and `/srv/cradle.git` likewise. Verifier helper `host_name(host) -> str` in `tests/conftest.py`, reading `HOST_PATH` from `/etc/cradle/deploy.conf` (until Task 6 renames it, `/etc/storagebaby/deploy.conf`; write it reading whichever exists).

- [ ] **Step 1: Molecule passes `host_dir`**

In `molecule.yml` under `provisioner.inventory.group_vars.all` add:

```yaml
# The one input the playbook takes: this VM's folder in the fixture host repository.
host_dir: /repo/tests/fixtures/host-repo/hosts/{{ inventory_hostname }}
```

- [ ] **Step 2: prepare.yml reads the fixture host repository and seeds two remotes**

```bash
cd /home/jlk/Projects/cradle/tests/integration/molecule/test-ci
sed -i "s|storagebaby_repo_root: /repo|cradle_root: /repo|g; s|{{ storagebaby_repo_root }}/hosts/|{{ cradle_root }}/tests/fixtures/host-repo/hosts/|g; s|_hosts_root: '{{ storagebaby_repo_root }}/hosts'|_hosts_root: '{{ cradle_root }}/tests/fixtures/host-repo/hosts'|; s|{{ storagebaby_repo_root }}/tests/fixtures/sources|{{ cradle_root }}/tests/fixtures/sources|g; s|storagebaby_repo_root ~ '/tests/fixtures/sources/'|cradle_root ~ '/tests/fixtures/sources/'|" prepare.yml
rg -n "storagebaby_repo_root|/repo/hosts" prepare.yml # must print nothing
```

Replace the play "Seed a local git remote from the working tree" with two seeds. The archive step becomes:

```yaml
- name: Archive Cradle and the fixture host repository the way git would see them
  ansible.builtin.shell: |
    set -e
    tar -C /repo --exclude-vcs-ignores \
      --exclude=.git --exclude=.env --exclude='*.secret' \
      --exclude=__pycache__ --exclude=.pytest_cache --exclude=node_modules \
      --exclude=.superpowers --exclude=.claude \
      -cf "$CRADLE_TAR" .
    gzip -f "$CRADLE_TAR"
    tar -C /repo/tests/fixtures/host-repo --exclude-vcs-ignores --exclude=__pycache__ -cf "$HOST_TAR" .
    tar -C /repo/tests/fixtures/host-repo -rf "$HOST_TAR" hosts/{{ inventory_hostname }}/secrets
    gzip -f "$HOST_TAR"
  environment:
    CRADLE_TAR: '/tmp/{{ inventory_hostname }}-cradle.tar'
    HOST_TAR: '/tmp/{{ inventory_hostname }}-host.tar'
  delegate_to: localhost
  changed_when: true
```

Then copy/extract/seed each into `/srv/src-cradle` → `/srv/cradle.git` and `/srv/src-host` → `/srv/host.git`, with the same `git init -b stable … push -qf origin stable` block the play has today, once per pair (loop over `[{src: /srv/src-cradle, bare: /srv/cradle.git, secrets: ''}, {src: /srv/src-host, bare: /srv/host.git, secrets: 'hosts/{{ inventory_hostname }}/secrets'}]`; `add -f` only when `secrets` is non-empty). Keep the existing comments about why it reseeds every run.

The bootstrap step (line 17) becomes — leave it on the _old_ argument form for now; Task 6 rewrites bootstrap and this line together. Until then this task's check is the static suite plus the converge step only.

The preflight play's command becomes:

```yaml
cmd: >-
  timeout 900 ansible-playbook -i localhost, -c local
  -e host_dir=/srv/src-host/hosts/{{ inventory_hostname }}
  --check --diff ansible/playbook.yml
chdir: /srv/src-cradle
```

- [ ] **Step 3: The verifiers read the fixture tree and the explicit host name**

Append to `tests/integration/molecule/test-ci/tests/conftest.py`:

```python
import re


def host_name(host) -> str:
    """The host folder this VM was told to converge: HOST_PATH in the deploy config.

    Explicit configuration, not `uname -n`: the harness happens to name the VM after the
    folder, but the engine never consults the hostname and neither should a verifier.
    """
    for conf in ("/etc/cradle/deploy.conf", "/etc/storagebaby/deploy.conf"):
        f = host.file(conf)
        if f.exists:
            m = re.search(r"^HOST_PATH=(.*)$", f.content_string, re.M)
            if m:
                return m.group(1).strip().rstrip("/").rsplit("/", 1)[-1]
    return host.check_output("uname -n")
```

Then:

```bash
cd /home/jlk/Projects/cradle/tests/integration/molecule/test-ci/tests
sed -i 's|HOSTS = Path("/repo/hosts")|HOSTS = Path("/repo/tests/fixtures/host-repo/hosts")|' test_service.py test_storage.py
sed -i 's|host.check_output("uname -n")|host_name(host)|g' test_host_base.py test_traefik.py test_storage.py test_ftp.py test_deploy.py
# and import it where used:
for f in test_host_base.py test_traefik.py test_storage.py test_ftp.py test_deploy.py; do
	grep -q "host_name" "$f" && sed -i '0,/^from conftest import /s//from conftest import host_name, /' "$f"
done
sed -i 's|SEEDED = "/srv/src"|SEEDED = "/srv/src-host"|; s|relative_to("/repo")|relative_to("/repo/tests/fixtures/host-repo")|; s|cd /srv/src \&\& sed|cd /srv/src-host \&\& sed|; s|hosts/\$(uname -n)/host.yml|hosts/{host_name(host)}/host.yml|' test_deploy.py
```

`test_deploy.py:950` becomes an f-string; check it. `CHECK` (line 160) becomes:

```python
CHECK = (
    f"cd {PULLED} && timeout 900 ansible-playbook -i localhost, -c local "
    f"-e host_dir={PULLED_HOST}/hosts/$(sed -n 's/^HOST_PATH=.*\\///p' /etc/storagebaby/deploy.conf) "
    "--check --diff ansible/playbook.yml"
)
```

with `PULLED = "/var/lib/storagebaby/repo"` for now and `PULLED_HOST = PULLED` — Task 5 splits the two checkouts and Task 6 renames; this task keeps the harness runnable against the current deploy script, which still fetches one repository.

- [ ] **Step 4: The deploy script passes `host_dir` (minimal, Task 5 rewrites it)**

In `ansible/roles/host_base/files/storagebaby-deploy` replace the playbook line:

```bash
/usr/bin/ansible-playbook -i localhost, -c local -e "host_dir=$REPO_DIR/hosts/$HOST" ${EXTRA_ARGS:-} ansible/playbook.yml
```

and in `bootstrap.sh` the same line inside the heredoc (the byte-identical test still holds in this task).

- [ ] **Step 5: Static suite, then converge + verify on test-ci**

Run: `cd /home/jlk/Projects/cradle && docker run --rm -t -v "$PWD":/repo -w /repo -e HOME=/root storagebaby-devtools pytest tests/static -v`
Expected: PASS.

Run: `cd /home/jlk/Projects/cradle && MOLECULE_HOST=test-ci mise run test-integration` (the task names the `storagebaby-devtools` image until Task 6; `mise trust` once)
Expected: `destroy → create → prepare → converge → idempotence → verify → destroy` green. ~30 minutes. On a failure read the Molecule output top-down; the first red task is the one to fix.

- [ ] **Step 6: Commit**

```bash
cd /home/jlk/Projects/cradle && git add -A && git commit -q -m "test: the harness converges the fixture host repository

Molecule hands the playbook host_dir, prepare seeds Cradle and the fixture
host repository as two remotes, and the verifiers read the host name off
the deploy config instead of uname.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: The deploy script fetches three kinds of checkout

**Files:**

- Modify: `ansible/roles/host_base/files/storagebaby-deploy` (whole script), `ansible/roles/host_base/files/storagebaby-deploy.service` (drop `Environment=GIT_SSH_COMMAND`, drop `ExecStartPost`)
- Modify: `bootstrap.sh` (the heredoc copy and `deploy.conf` — kept in sync by the byte-identical test until Task 6 removes the embedding)
- Modify: `tests/integration/molecule/test-ci/tests/test_deploy.py` (`PULLED`, `PULLED_HOST`, `CHECK`), `test_host_base.py:102-125`
- Test: `tests/static/test_bootstrap.py` (`test_git_ssh_command_is_one_quoted_environment_value` goes: the unit carries no `GIT_SSH_COMMAND` any more)

**Interfaces:**

- Consumes: `/etc/storagebaby/deploy.conf` with the five values (Task 6 moves the file to `/etc/cradle`).
- Produces: `sync_checkout <dir> <url> <ref> <key>`; checkouts at `$STATE_DIR/cradle`, `$STATE_DIR/host`, `$STATE_DIR/sources/<name>`; `cradle-deploy --check` fetches, runs `--check --diff`, writes no state.

- [ ] **Step 1: Rewrite the script's configuration and fetch section**

Replace from `. /etc/storagebaby/deploy.conf` through the state comparison with:

```bash
# CRADLE_REPO, CRADLE_REF, HOST_REPO, HOST_REF, HOST_PATH, optional EXTRA_ARGS: written by
# bootstrap.sh. Explicit configuration, all five of them; the hostname is never consulted.
. "$CONF_DIR/deploy.conf"
: "${CRADLE_REPO:?}" "${CRADLE_REF:?}" "${HOST_REPO:?}" "${HOST_REF:?}" "${HOST_PATH:?}"

CRADLE_DIR=$STATE_DIR/cradle
HOST_DIR=$STATE_DIR/host
SOURCES_DIR=$STATE_DIR/sources
STATE_FILE=$STATE_DIR/deploy.state

# --check: fetch everything, run the playbook with --check --diff, write no state. The
# pre-flight an operator runs before letting a converge touch the host, and the only way
# to do that through this script: a check run must never be recorded as a converge.
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

export SOPS_AGE_KEY_FILE=$CONF_DIR/age.key
export GIT_TERMINAL_PROMPT=0
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0=

# sync_checkout <dir> <url> <ref> <key>: clone or fetch over ssh with <key> (empty for a
# public https remote or a token-authenticated one), check out origin/<ref> detached,
# print HEAD. One key per repository because a GitHub deploy key belongs to exactly one
# repository: cradle_key opens Cradle, deploy_key opens the host repository.
sync_checkout() {
	local dir=$1 url=$2 ref=$3 key=$4
	local ssh="ssh -o StrictHostKeyChecking=accept-new"
	[ -n "$key" ] && ssh="ssh -i $key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
	export GIT_SSH_COMMAND=$ssh
	if [ ! -d "$dir/.git" ]; then
		git clone --quiet --no-checkout "$url" "$dir" || return 1
	fi
	git -C "$dir" remote set-url origin "$url" || return 1
	git -C "$dir" fetch --quiet --prune origin "+refs/heads/$ref:refs/remotes/origin/$ref" || return 1
	git -C "$dir" checkout --quiet --detach "origin/$ref" || return 1
	git -C "$dir" reset --quiet --hard || return 1
	git -C "$dir" clean --quiet -fdx || return 1
	git -C "$dir" rev-parse HEAD
}
```

with `CONF_DIR=/etc/storagebaby` and `STATE_DIR=/var/lib/storagebaby` declared at the top (Task 6 flips both to cradle). Then the fetches:

```bash
install -d -m 0755 "$STATE_DIR"
install -d -m 0700 "$SOURCES_DIR"

echo "cradle: $CRADLE_REPO ($CRADLE_REF)"
cradle_sha=$(sync_checkout "$CRADLE_DIR" "$CRADLE_REPO" "$CRADLE_REF" "$CONF_DIR/cradle_key") || {
	echo "cradle $CRADLE_REPO ($CRADLE_REF) failed to fetch; nothing was converged" >&2
	exit 1
}
echo "host repository: $HOST_REPO ($HOST_REF, $HOST_PATH)"
host_sha=$(sync_checkout "$HOST_DIR" "$HOST_REPO" "$HOST_REF" "$CONF_DIR/deploy_key") || {
	echo "the host repository $HOST_REPO ($HOST_REF) failed to fetch; nothing was converged" >&2
	exit 1
}
if [ ! -f "$HOST_DIR/$HOST_PATH/host.yml" ]; then
	echo "$HOST_REPO ($HOST_REF) has no $HOST_PATH/host.yml; nothing to converge" >&2
	exit 1
fi
desired="cradle $cradle_sha"$'\n'"host $host_sha"
```

`list_pointers` globs `$HOST_DIR/$HOST_PATH/services/*/source.yml` and `$HOST_DIR/$(dirname "$HOST_PATH")/shared/services/*/source.yml`; `SOURCE_TOKENS` reads `$HOST_DIR/$HOST_PATH/secrets/sources.sops.yaml`. Pointer fetches pass an empty key (https + token). The tail:

```bash
if [ "$CHECK" -eq 1 ]; then
	cd "$CRADLE_DIR"
	exec /usr/bin/ansible-playbook -i localhost, -c local -e "host_dir=$HOST_DIR/$HOST_PATH" --check --diff ansible/playbook.yml
fi
if [ -f "$STATE_FILE" ] && [ "$(cat "$STATE_FILE")" = "$desired" ]; then
	echo "nothing changed since the last converge; not running the playbook"
	exit 0
fi
cd "$CRADLE_DIR"
/usr/bin/ansible-playbook -i localhost, -c local -e "host_dir=$HOST_DIR/$HOST_PATH" ${EXTRA_ARGS:-} ansible/playbook.yml
printf '%s\n' "$desired" > "$STATE_FILE.tmp" && mv -f "$STATE_FILE.tmp" "$STATE_FILE"
```

Keep every explanatory comment the script has today where it still applies.

- [ ] **Step 2: The unit**

`storagebaby-deploy.service` `[Service]` becomes:

```ini
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/storagebaby-deploy
```

(`EnvironmentFile` goes too: the script sources the config itself and validates it.) Delete `test_git_ssh_command_is_one_quoted_environment_value` from `test_bootstrap.py`.

- [ ] **Step 3: bootstrap writes the five values (interim, old paths)**

In `bootstrap.sh` the argument parser gains `--cradle-repo`, `--cradle-ref` (default `stable`), `--host-repo`, `--host-ref` (default `stable`), `--host-path`; `--repo`/`--branch` go. `deploy.conf` is written as:

```bash
cat > /etc/storagebaby/deploy.conf << EOF
CRADLE_REPO=${CRADLE_REPO}
CRADLE_REF=${CRADLE_REF}
HOST_REPO=${HOST_REPO}
HOST_REF=${HOST_REF}
HOST_PATH=${HOST_PATH}
EXTRA_ARGS=
EOF
```

A second key: after the `deploy_key` block, `ssh-keygen -q -t ed25519 -N '' -C "cradle@$(hostname)" -f /etc/storagebaby/cradle_key` guarded on absence, and print it at the end with its purpose ("read-only deploy key on the Cradle repository"). Update the heredoc copies of the script and unit so the byte-identical test passes. prepare.yml line 17 becomes:

```yaml
ansible.builtin.command: >-
  /usr/local/sbin/storagebaby-bootstrap --local
  --cradle-repo /srv/cradle.git --cradle-ref stable
  --host-repo /srv/host.git --host-ref stable
  --host-path hosts/{{ inventory_hostname }}
```

- [ ] **Step 4: The verifiers follow**

In `test_deploy.py`: `PULLED = "/var/lib/storagebaby/cradle"`, `PULLED_HOST = "/var/lib/storagebaby/host"`, `CHECK = "timeout 900 /usr/local/sbin/storagebaby-deploy --check"`, and line 185 asserts `f"{PULLED}/ansible/playbook.yml"`. Add:

```python
@pytest.mark.order(-1)
def test_a_check_tick_runs_the_playbook_in_check_mode_and_records_nothing(host):
    state_before = host.file(STATE).content_string if host.file(STATE).exists else None
    r = host.run(CHECK)
    check_recap(r)
    assert "PLAY RECAP" in r.stdout
    state_after = host.file(STATE).content_string if host.file(STATE).exists else None
    assert state_after == state_before, "a --check tick wrote the state file"
```

In `test_host_base.py`: the unit has no `GIT_SSH_COMMAND`; `cradle_key` exists at mode 0600 beside `deploy_key`; the deploy unit has no `ExecStartPost`.

- [ ] **Step 5: Static suite, then integration**

Run: static as in Task 4 step 5 — PASS. Run: `MOLECULE_HOST=test-ci mise run test-integration` — green, including the new check-tick test and every `test_deploy.py` case that pushes to `/srv/src-host`.

- [ ] **Step 6: Commit**

```bash
cd /home/jlk/Projects/cradle && git add -A && git commit -q -m "feat: a deploy tick fetches Cradle, the host repository and every pointer

Five explicit values in deploy.conf, one ssh key per repository because a
GitHub deploy key opens one repository, a --check mode that fetches and
runs the playbook in check mode without recording a converge, and no
hostname anywhere: the unit's ExecStartPost that consulted it is gone.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: The rename, and bootstrap runs the script from the Cradle checkout

**Files:**

- Rename: `ansible/roles/host_base/files/storagebaby-{deploy,deploy.service,deploy.timer,svc}` → `cradle-*`; `ansible/roles/service/templates/storagebaby-hosts.conf.j2` → `cradle-hosts.conf.j2`; `tests/fixtures/sources/ext-echo/deploy/storagebaby` → `deploy/cradle`
- Modify: every file `rg -l storagebaby` lists (roles, playbook, bootstrap, tests, mise.toml, lefthook.yml, ci.yml, devtools/package.json, molecule vars, fixture pointers)
- Modify: `bootstrap.sh` (clone Cradle first, install script and units from the checkout, drop the heredocs, adoption step, `--no-upgrade`)
- Modify: `tests/static/test_bootstrap.py` (drop `test_bootstrap_embeds_the_same_units_and_deploy_script_host_base_installs`; add the "installs from the checkout" test)

**Interfaces:**

- Produces: on a host, `/etc/cradle/{age.key,age.pub,deploy_key,deploy_key.pub,cradle_key,cradle_key.pub,deploy.conf}`, `/var/lib/cradle/{cradle,host,sources,deploy.state}`, `/opt/cradle/maintenance`, `/usr/local/sbin/{cradle-deploy,cradle-svc}`, `cradle-deploy.{service,timer}`, `10-cradle-hosts.conf`, `70-cradle-render.rules`. Devtools image `cradle-devtools`, cache volume `cradle-molecule-cache`, libvirt images under `/var/lib/libvirt/images/cradle`.

- [ ] **Step 1: Rename files**

```bash
cd /home/jlk/Projects/cradle
for f in deploy deploy.service deploy.timer svc; do git mv ansible/roles/host_base/files/storagebaby-$f ansible/roles/host_base/files/cradle-$f; done
git mv ansible/roles/service/templates/storagebaby-hosts.conf.j2 ansible/roles/service/templates/cradle-hosts.conf.j2
git mv tests/fixtures/sources/ext-echo/deploy/storagebaby tests/fixtures/sources/ext-echo/deploy/cradle
sed -i 's|path: deploy/storagebaby|path: deploy/cradle|' tests/fixtures/host-repo/hosts/test-a/services/ext-echo/source.yml tests/fixtures/host-repo/hosts/test-ci/services/ext-echo/source.yml
```

- [ ] **Step 2: Replace the name everywhere it is Cradle's**

```bash
cd /home/jlk/Projects/cradle
rg -l storagebaby --hidden -g '!.git' -g '!docs/superpowers' -g '!tests/fixtures/host-repo' | xargs sed -i \
	-e 's|/etc/storagebaby|/etc/cradle|g' \
	-e 's|/var/lib/storagebaby|/var/lib/cradle|g' \
	-e 's|/opt/storagebaby|/opt/cradle|g' \
	-e 's|storagebaby-deploy|cradle-deploy|g' \
	-e 's|storagebaby-svc|cradle-svc|g' \
	-e 's|storagebaby-hosts|cradle-hosts|g' \
	-e 's|storagebaby-render|cradle-render|g' \
	-e 's|storagebaby-bootstrap|cradle-bootstrap|g' \
	-e 's|storagebaby-devtools|cradle-devtools|g' \
	-e 's|storagebaby-molecule-cache|cradle-molecule-cache|g' \
	-e 's|/var/lib/libvirt/images/storagebaby|/var/lib/libvirt/images/cradle|g' \
	-e 's|storagebaby-base-builder|cradle-base-builder|g' \
	-e 's|\.storagebaby-probe|.cradle-probe|g' \
	-e 's|\.storagebaby-branch-probe|.cradle-branch-probe|g' \
	-e 's|storagebaby-ftp-probe|cradle-ftp-probe|g' \
	-e 's|storagebaby-tcp|cradle-tcp|g' \
	-e 's|storagebaby@test.local|cradle@test.local|g' \
	-e 's|storagebaby test harness|cradle test harness|g' \
	-e 's|"name": "storagebaby-devtools"|"name": "cradle-devtools"|g'
rg -n "storagebaby" --hidden -g '!.git' -g '!docs/superpowers' -g '!tests/fixtures/host-repo' .
```

Expected leftovers, and only these: `docs/architecture.md`/`docs/ownership.md` prose (Task 9 edits those), `bootstrap.sh` (Step 3 rewrites it; the adoption names the old paths on purpose). Anything else is a miss — fix it by hand. Untouched on purpose: `docs/superpowers/` is history, and the fixture host repository's `host.yml` comments and copied service READMEs talk _about_ StorageBaby, whose services they are. The fixture pointer `ext-echo/source.yml` was fixed in Step 1.

- [ ] **Step 3: bootstrap clones Cradle first and installs from the checkout**

Rewrite `bootstrap.sh` from the argument parser down (keep the Chaotic-AUR block verbatim):

```bash
#!/bin/bash
# One-time host setup. Run as root on an Arch host:
#   bootstrap.sh --cradle-repo <url> --host-repo <url> --host-path hosts/<name> \
#                [--cradle-ref stable] [--host-ref stable] [--local] [--no-upgrade]
# --local: do not enable the deploy timer (tests and dry runs).
# --no-upgrade: skip the one `pacman -Syu`; for a host that already runs, whose packages
#   are the operator's business (an upgrade mid-cutover replaces the kernel and breaks
#   overlay mounts until a reboot nobody planned).
set -euo pipefail

CRADLE_REPO="" CRADLE_REF=stable HOST_REPO="" HOST_REF=stable HOST_PATH="" LOCAL=0 UPGRADE=1
while [ $# -gt 0 ]; do
	case "$1" in
		--cradle-repo)
			CRADLE_REPO=$2
			shift 2
			;;
		--cradle-ref)
			CRADLE_REF=$2
			shift 2
			;;
		--host-repo)
			HOST_REPO=$2
			shift 2
			;;
		--host-ref)
			HOST_REF=$2
			shift 2
			;;
		--host-path)
			HOST_PATH=$2
			shift 2
			;;
		--local)
			LOCAL=1
			shift
			;;
		--no-upgrade)
			UPGRADE=0
			shift
			;;
		*)
			echo "unknown argument: $1" >&2
			exit 2
			;;
	esac
done
for v in CRADLE_REPO HOST_REPO HOST_PATH; do
	[ -n "${!v}" ] || {
		echo "--$(echo "$v" | tr '[:upper:]_' '[:lower:]-') is required" >&2
		exit 2
	}
done
[ "$(id -u)" -eq 0 ] || {
	echo "run as root" >&2
	exit 2
}

# Adoption of a host the pre-split engine set up under the StorageBaby name: the state
# the deploy tick needs before any playbook runs moves to Cradle's paths, once, here.
# Per item and never the parent: /var/lib/storagebaby/fast is a *declared storage root*
# on the first such host, data, and not Cradle's to touch. Everything else the old
# engine left (units, helpers, drop-ins, /opt) is ansible/migrations/0001's job on the
# first converge.
if [ -d /etc/storagebaby ] && [ ! -d /etc/cradle ]; then
	echo "adopting /etc/storagebaby as /etc/cradle"
	mv /etc/storagebaby /etc/cradle
	rm -f /etc/cradle/deploy.conf
fi
if [ -d /var/lib/storagebaby ] && [ ! -d /var/lib/cradle ]; then
	echo "adopting the deploy state under /var/lib/storagebaby"
	install -d -m 0755 /var/lib/cradle
	[ -d /var/lib/storagebaby/repo ] && mv /var/lib/storagebaby/repo /var/lib/cradle/host
	[ -d /var/lib/storagebaby/sources ] && mv /var/lib/storagebaby/sources /var/lib/cradle/sources
	[ -f /var/lib/storagebaby/deploy.state ] && rm -f /var/lib/storagebaby/deploy.state
fi
if systemctl list-unit-files storagebaby-deploy.timer > /dev/null 2>&1; then
	systemctl disable --now storagebaby-deploy.timer 2> /dev/null || true
fi

# ... Chaotic-AUR block unchanged ...

if [ "$UPGRADE" -eq 1 ]; then
	pacman -Syu --noconfirm --needed git ansible sops age podman passt openssh
else
	pacman -S --noconfirm --needed git ansible sops age podman passt openssh
fi

install -d -m 0700 /etc/cradle
[ -f /etc/cradle/age.key ] || {
	age-keygen -o /etc/cradle/age.key 2> /dev/null
	chmod 0600 /etc/cradle/age.key
}
age-keygen -y /etc/cradle/age.key > /etc/cradle/age.pub
[ -f /etc/cradle/deploy_key ] || ssh-keygen -q -t ed25519 -N '' -C "cradle-host@$(hostname)" -f /etc/cradle/deploy_key
[ -f /etc/cradle/cradle_key ] || ssh-keygen -q -t ed25519 -N '' -C "cradle@$(hostname)" -f /etc/cradle/cradle_key

cat > /etc/cradle/deploy.conf << EOF
CRADLE_REPO=${CRADLE_REPO}
CRADLE_REF=${CRADLE_REF}
HOST_REPO=${HOST_REPO}
HOST_REF=${HOST_REF}
HOST_PATH=${HOST_PATH}
EXTRA_ARGS=
EOF
chmod 0600 /etc/cradle/deploy.conf

# Cradle itself, first: the deploy script and its units are installed from this checkout,
# so nothing is embedded here and nothing can drift. A private Cradle needs cradle_key
# registered as a read-only deploy key before this clone can succeed; the keys are
# printed below, and this script is idempotent -- register them and run it again.
install -d -m 0755 /var/lib/cradle
export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND="ssh -i /etc/cradle/cradle_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
if [ ! -d /var/lib/cradle/cradle/.git ]; then
	if ! git clone --quiet --branch "$CRADLE_REF" "$CRADLE_REPO" /var/lib/cradle/cradle; then
		echo
		echo "Cradle could not be cloned from $CRADLE_REPO. If it is private, add this as a"
		echo "read-only deploy key on it and run bootstrap again with the same arguments:"
		cat /etc/cradle/cradle_key.pub
		exit 1
	fi
fi
install -m 0755 /var/lib/cradle/cradle/ansible/roles/host_base/files/cradle-deploy /usr/local/sbin/cradle-deploy
install -m 0644 /var/lib/cradle/cradle/ansible/roles/host_base/files/cradle-deploy.service /etc/systemd/system/cradle-deploy.service
install -m 0644 /var/lib/cradle/cradle/ansible/roles/host_base/files/cradle-deploy.timer /etc/systemd/system/cradle-deploy.timer
systemctl daemon-reload
[ "$LOCAL" -eq 0 ] && systemctl enable --now cradle-deploy.timer

echo
echo "Read-only deploy key for the host repository ($HOST_REPO):"
cat /etc/cradle/deploy_key.pub
echo
echo "Read-only deploy key for Cradle ($CRADLE_REPO), if it is private:"
cat /etc/cradle/cradle_key.pub
echo
echo "age recipient for this host; add it to the host repository's .sops.yaml under this"
echo "host's rule and the hosts/shared/** rule, then run sops updatekeys on every *.sops.yaml:"
cat /etc/cradle/age.pub
```

Keep `test_bootstrap_is_valid_bash`, `test_deploy_script_is_valid_bash` (pointing at `cradle-deploy`), the three Chaotic-AUR agreement tests. Replace the embedding test with:

```python
def test_bootstrap_installs_the_deploy_script_and_units_from_the_cradle_checkout():
    script = (REPO / "bootstrap.sh").read_text()
    for name in ["cradle-deploy", "cradle-deploy.service", "cradle-deploy.timer"]:
        assert f"/var/lib/cradle/cradle/ansible/roles/host_base/files/{name}" in script, name
    assert "DEPLOY_SCRIPT" not in script, "the deploy script is installed from the checkout, never embedded"
```

- [ ] **Step 4: prepare.yml plants the old layout before bootstrap, so adoption runs on every VM**

Before the "Copy bootstrap.sh" task:

```yaml
# The old engine's footprint, planted on the fresh VM so bootstrap's adoption and
# migration 0001 run in CI on every host: a marker under /etc/storagebaby (never a fake
# age.key -- bootstrap would keep it), the deploy state, a stale helper, a stale drop-in
# and, under /var/lib/storagebaby/fast, a file that must survive because that path is
# a declared storage root on the real host. test_migrations.py reads the result.
- name: Plant the pre-split layout
  ansible.builtin.shell: |
    set -e
    install -d -m 0711 /etc/storagebaby
    touch /etc/storagebaby/adopted-marker
    install -d /var/lib/storagebaby/repo /var/lib/storagebaby/sources /var/lib/storagebaby/fast
    echo stale > /var/lib/storagebaby/deploy.state
    touch /var/lib/storagebaby/fast/keep
    install -d /opt/storagebaby/maintenance
    touch /opt/storagebaby/maintenance/stale.sh
    printf '#!/bin/sh\n' > /usr/local/sbin/storagebaby-svc && chmod 0755 /usr/local/sbin/storagebaby-svc
    install -d /etc/containers/systemd/users/9999/stale.container.d
    touch /etc/containers/systemd/users/9999/stale.container.d/10-storagebaby-hosts.conf
    touch /etc/udev/rules.d/70-storagebaby-render.rules
  changed_when: true
```

and the bootstrap call: `/usr/local/sbin/cradle-bootstrap --local --cradle-repo /srv/cradle.git --host-repo /srv/host.git --host-path hosts/{{ inventory_hostname }}`. **Ordering:** bootstrap clones Cradle, so the seed play (two bare repos) has to run _before_ the bootstrap play now. Move the "Seed …" play above "Bootstrap the test host like a real one".

- [ ] **Step 5: Static suite**

Run: `cd /home/jlk/Projects/cradle && mise run devtools && mise run test-static` (the image is now `cradle-devtools`).
Expected: PASS. `test_molecule_playbooks_parse` covers the reordered prepare.

- [ ] **Step 6: Commit** (install hooks first: `mise run install-hooks`)

```bash
cd /home/jlk/Projects/cradle && git add -A && git commit -q -m "feat!: the engine is Cradle, on a host and in its own tooling

Every path, unit, helper and image the engine owns carries its own name;
bootstrap clones Cradle before anything else and installs the deploy
script and units from that checkout, so nothing is embedded any more;
a host set up under the old name is adopted by bootstrap, per item, with
the declared fast storage root left where it is.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Migrations, and the first one

**Files:**

- Create: `ansible/migrations/0001-storagebaby-to-cradle.yml`, `ansible/migrations/README.md`
- Modify: `ansible/playbook.yml` (include migrations in `pre_tasks`, before discovery)
- Create: `tests/integration/molecule/test-ci/tests/test_migrations.py`
- Test: `tests/static/test_migrations.py`

**Interfaces:**

- Produces: `ansible/migrations/NNNN-<name>.yml`, task files included in name order on every converge, each a no-op once its old state is gone.

- [ ] **Step 1: Static test for the mechanism**

```python
"""Migrations: task files Cradle runs first on every converge, idempotent by construction."""

import re

import yaml

from conftest import REPO

MIGRATIONS = REPO / "ansible" / "migrations"


def test_migrations_are_numbered_task_files():
    files = sorted(p.name for p in MIGRATIONS.glob("*.yml"))
    assert files, "no migrations"
    for i, name in enumerate(files, start=1):
        assert re.match(rf"^{i:04d}-[a-z0-9-]+\.yml$", name), name
        body = yaml.safe_load((MIGRATIONS / name).read_text())
        assert isinstance(body, list) and body, f"{name} is not a task list"


def test_playbook_runs_migrations_before_anything_else():
    play = yaml.safe_load((REPO / "ansible/playbook.yml").read_text())[0]
    first = play["pre_tasks"][1]  # [0] is the host_dir assert
    assert "include_tasks" in str(first) and "migrations" in str(first), first
```

Run it: FAIL (no directory).

- [ ] **Step 2: The mechanism in the playbook**

After the `host_dir` assert, before "Load host configuration":

```yaml
# Migrations of Cradle's own state on the host: numbered task files, run in order on
# every converge, each a no-op once the old state it looks for is gone. No state file
# to keep, and a tick that died half way through finishes on the next one.
- name: Run Cradle's migrations
  ansible.builtin.include_tasks: '{{ item }}'
  loop: "{{ lookup('ansible.builtin.fileglob', cradle_root ~ '/ansible/migrations/*.yml') | split(',') | sort }}"
  loop_control:
    label: '{{ item | basename }}'
  when: not (render_only | bool)
```

- [ ] **Step 3: Migration 0001**

```yaml
# 0001: a host set up by the pre-split StorageBaby engine. bootstrap.sh adopted /etc and
# the deploy state; this removes what the old engine installed under its own name. Every
# removed thing is rendered again under Cradle's name by the roles of this same converge
# (the maintenance tree, cradle-svc, the drop-ins, the udev rule), so "remove old" is the
# whole migration and it is idempotent by construction.
- name: Old deploy timer and unit
  ansible.builtin.systemd:
    name: storagebaby-deploy.timer
    enabled: false
    state: stopped
  failed_when: false

- name: Old units, helpers, udev rule and maintenance tree
  ansible.builtin.file:
    path: '{{ item }}'
    state: absent
  loop:
    - /etc/systemd/system/storagebaby-deploy.timer
    - /etc/systemd/system/storagebaby-deploy.service
    - /usr/local/sbin/storagebaby-deploy
    - /usr/local/sbin/storagebaby-svc
    - /etc/udev/rules.d/70-storagebaby-render.rules
    - /opt/storagebaby/maintenance
    - /opt/storagebaby
    - /var/lib/storagebaby/deploy.state
  register: _old_files

- name: Reload systemd when an old unit went
  ansible.builtin.systemd:
    daemon_reload: true
  when: _old_files.changed

- name: Old host-gateway drop-ins
  ansible.builtin.find:
    paths: /etc/containers/systemd/users
    patterns: 10-storagebaby-hosts.conf
    recurse: true
  register: _old_dropins

- name: Remove them (the service role renders 10-cradle-hosts.conf beside them)
  ansible.builtin.file:
    path: '{{ item.path }}'
    state: absent
  loop: '{{ _old_dropins.files }}'
  loop_control:
    label: '{{ item.path }}'

# /var/lib/storagebaby is left standing when anything else is in it: on the first host it
# holds the declared fast storage root. Removed only when empty.
- name: Remove /var/lib/storagebaby when nothing but the old state was in it
  ansible.builtin.command: rmdir /var/lib/storagebaby
  args:
    removes: /var/lib/storagebaby
  failed_when: false
  changed_when: false
```

`ansible/migrations/README.md`: three paragraphs — what a migration is, the naming rule, "idempotent by construction: look for the old state, do nothing when it is gone; never a state file".

- [ ] **Step 4: The integration verifier**

```python
"""bootstrap's adoption and migration 0001 on a VM prepare planted the old layout on."""


def test_etc_was_adopted(host):
    assert host.file("/etc/cradle/adopted-marker").exists
    assert not host.file("/etc/storagebaby").exists


def test_deploy_state_was_adopted_and_the_fast_root_left_alone(host):
    assert host.file("/var/lib/cradle/host/.git").is_directory
    assert host.file("/var/lib/cradle/sources").is_directory
    assert host.file("/var/lib/storagebaby/fast/keep").exists
    assert not host.file("/var/lib/storagebaby/repo").exists
    assert not host.file("/var/lib/storagebaby/deploy.state").exists


def test_migration_0001_removed_the_old_engine(host):
    for path in [
        "/opt/storagebaby",
        "/usr/local/sbin/storagebaby-svc",
        "/etc/udev/rules.d/70-storagebaby-render.rules",
        "/etc/containers/systemd/users/9999/stale.container.d/10-storagebaby-hosts.conf",
    ]:
        assert not host.file(path).exists, path
    assert host.service("storagebaby-deploy.timer").is_enabled is False
    assert host.file("/opt/cradle/maintenance/sync.sh").exists
    assert host.file("/usr/local/sbin/cradle-svc").exists
```

- [ ] **Step 5: Static, then integration**

Run: `mise run test-static` — PASS. Run: `MOLECULE_HOST=test-ci mise run test-integration` — green, `test_migrations.py` included; `idempotence` proves the migration reports no change on the second converge.

- [ ] **Step 6: Commit**

```bash
cd /home/jlk/Projects/cradle && git add -A && git commit -q -m "feat: migrations of Cradle's own state, and the storagebaby-to-cradle one

Numbered task files the playbook includes first on every converge, each a
no-op once its old state is gone. 0001 removes what the old engine left
under its name; the roles render it again under Cradle's in the same run.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: `mise run render`, and the Cradle CI

**Files:**

- Modify: `mise.toml` (new `render` task; `changes` skip regex reference), `.github/workflows/ci.yml` (skip regex, cache paths, `MOLECULE_HOST`), `.env.example`

**Interfaces:**

- Produces: `mise run render <host-repo path> <hosts/name> <out dir>` renders that host folder with this Cradle into `<out dir>`. Task 13 uses it.

- [ ] **Step 1: The render task**

```toml
[tasks.render]
description = "Render what Cradle would put on a host, without a host: mise run render /path/to/host-repo hosts/<name> /path/to/out"
# The operator's view of a host repository against this Cradle: every unit, route, timer and
# storage file, into a directory. The same thing the static suite does with the fixture host
# repository, offered for any host repository on this machine. Not a gate: nothing in a
# host repository's CI calls it.
usage = '''
arg "<host_repo>" help="A host repository checkout"
arg "<host_path>" help="The host folder inside it, e.g. hosts/StorageBaby"
arg "<out>" help="Output directory (created)"
'''
run = '''
mkdir -p "$usage_out"
docker run --rm -t $DEVTOOLS_OPTS -v "$(realpath "$usage_host_repo")":/host:ro -v "$(realpath "$usage_out")":/out $DEVTOOLS_IMAGE \
  ansible-playbook -i localhost, -c local ansible/playbook.yml \
  -e host_dir=/host/$usage_host_path -e render_only=true -e render_output=/out \
  -e ansible_become=false -e tcp_bind_address=203.0.113.1 -e sources_root=/host/.cradle-sources
'''
```

- [ ] **Step 2: CI**

In `.github/workflows/ci.yml`: `skippable='^docs/|\.md$'`; `/var/lib/libvirt/images/storagebaby` already renamed in Task 6 — confirm with `rg storagebaby .github`. `branches: [master]`. Keep the `promote` job as is.

- [ ] **Step 3: Check**

Run: `mise run render tests/fixtures/host-repo hosts/test-ci /tmp/claude-1000/-home-jlk-Projects-StorageBaby/205176ee-280c-48c7-baa9-d3b66f2e4832/scratchpad/render-test-ci` — exits 0, output holds `yuzukam/`, `paperless/`, `traefik/`.

- [ ] **Step 4: Commit** — `feat: mise run render, and CI without the StorageBaby-specific skip list`.

---

### Task 9: Cradle's docs, skill and CLAUDE.md

**Files:**

- Create: `README.md`, `CLAUDE.md`
- Modify: `docs/ownership.md` (§ 2, § 3 row, § 7), `docs/architecture.md` (preface), `docs/external-service.md`, `docs/quickstart.md`, `ansible/roles/service/README.md`, `ansible/roles/storage/README.md`, `.claude/skills/adding-a-service/SKILL.md`
- Copy: StorageBaby `docs/superpowers/specs/*` and `plans/*` are already in the import (Task 2). Add this plan after it is committed in StorageBaby (Task 12).

- [ ] **Step 1: README.md** — new, in this order, each a short section: what Cradle is (one machine, one host repository, a template to fork); the host repository contract (§ 4 of the spec, with the tree); bootstrap (the command with its five values, the two deploy keys, the age recipient); the loop (three fetches, `stable`, `cradle-deploy --check`); operating a service (`cradle-svc`, the raw `systemctl --user -M` forms); secrets in one minute; external services (pointer); migrations; working on the repo (`mise tasks`, the devtools image, `mise run render`, the two suites, `MOLECULE_HOST`). Lift the prose from StorageBaby's README sections "Operating a service", "Working on the repo", "External services", "Secrets, in one minute", "New host" and `docs/quickstart.md`, with the names and paths of Task 6.

- [ ] **Step 2: CLAUDE.md** — StorageBaby's CLAUDE.md minus everything that is about that host (its services list, its storage layout, its disks), with: the host repository contract, `-e host_dir`, the fixture host repository as the thing the tests render, `mise run render`, migrations, and the "service folder is a plugin" paragraph unchanged.

- [ ] **Step 3: ownership.md** — § 2 "Platform": `Cradle — the repository this file lives in: ansible/, bootstrap.sh, CI, and the contract the static tests enforce`. § 3 new row: `Migrating its own state on a host | Numbered task files run first on every converge; a host set up under an older name is adopted, never left behind`. § 7: remove items 13–16 (they move to StorageBaby's README in Task 12) and renumber nothing else; fix every `ansible/roles/...` path that Task 6 renamed.

- [ ] **Step 4: architecture.md** — add a preface under the title: "Since 2026-10-16 this engine is Cradle and lives in its own repository; the layers below are unchanged except that `hosts/` is a host repository Cradle reads through `-e host_dir`, every path on a host carries Cradle's name, and the tests render `tests/fixtures/host-repo/`. Measurements and section numbers are the pre-split ones." Then `sed` the renamed paths in it.

- [ ] **Step 5: role READMEs, external-service.md, quickstart.md, the skill** — `rg -n "storagebaby|hosts/<host>|StorageBaby" docs ansible/roles/*/README.md .claude` and fix each: paths from Task 6, `hosts/<host>/` stays (it is the host repository's layout), "this repository" → "the host repository" where a service folder is meant. The skill's description line: `Use when adding, changing or reviewing a service for a Cradle host — a service folder, a Quadlet unit, a service.yml key, a pod, a volume, a secret, a route or a fixture placement.`

- [ ] **Step 6: Check and commit**

Run: `mise run fmt-check && mise run test-static` — PASS. Commit: `docs: Cradle's own README, CLAUDE.md and the ownership doc re-pointed`.

---

### Task 10: Full local integration run, then GitHub

**Files:** none new.

- [ ] **Step 1: Both suites, test-a placement**

Run: `cd /home/jlk/Projects/cradle && mise run test-static && mise run test-integration` (default `MOLECULE_HOST=test-a`, ~50 min).
Expected: green. test-a places every copied service, so this is the run that proves the copied fixtures converge under Cradle.

- [ ] **Step 2: Create the private repository and push**

```bash
cd /home/jlk/Projects/cradle
gh repo create janlucaklees/cradle --private --source . --remote origin --description "One-machine GitOps for rootless Podman services: a host repository in, running services out" --push
gh run watch --exit-status # the CI run on master
```

Expected: `static` and `integration` green on the runner, `promote` fast-forwards `stable`. `gh api repos/janlucaklees/cradle/branches/stable` returns the tested SHA. If the runner's integration job fails for a runner-only reason (KVM, timeout), read the job log, fix, push again; `stable` must exist before Task 14.

---

### Task 10b: StorageBaby's Traefik template stops naming the engine's path

Found in Task 6: `hosts/shared/services/traefik/quadlet/traefik.container.j2` hardcodes
`/etc/storagebaby/traefik/dynamic.d` and `/etc/storagebaby/traefik/certs`. After
bootstrap's adoption those paths are gone and Cradle writes routes under
`/etc/cradle/traefik`, so Traefik would come up with no routes at cutover. The role hands
every service its config directory as `config_dir` (`/etc/<engine>/<name>`), which renders
to the same text under the old engine and to the right path under Cradle — so this commit
is engine-agnostic and must be on StorageBaby's `stable` **before Task 14**.

**Files (StorageBaby, on `master`, not the split worktree):**

- Modify: `hosts/shared/services/traefik/quadlet/traefik.container.j2:52,58`, `hosts/shared/services/traefik/README.md` (the two paths, if named)

- [ ] **Step 1: The template reads `config_dir`**

```
Volume={{ config_dir }}/dynamic.d:/etc/traefik/dynamic.d:ro
Volume={{ config_dir }}/certs:/etc/traefik/certs:ro
```

with a comment above the first: the directory is the platform's, handed down as
`config_dir`; a service folder never names the engine's path (`docs/ownership.md` § 1.1).

- [ ] **Step 2: Prove the render is unchanged under the old engine**

Run from StorageBaby master: the Task 1 baseline command into a second directory
(`$B/render-10b`) and `diff -r $B/render $B/render-10b` — empty.

- [ ] **Step 3: Commit on master, push, let CI promote `stable`**

Commit via the `commit` skill: `fix: traefik reads its config directory from the role, not from a path`.
`git push origin master`, `gh run watch --exit-status`; `stable` moves (the push touches only
`hosts/shared/`, so the `changes` job skips the VM). On the NAS the next tick re-renders an
identical unit: `ssh root@192.168.178.2 'journalctl -u storagebaby-deploy.service -n 30 --no-pager'`
shows a converge with no Traefik restart.

Cradle's fixture copy of the template uses `{{ config_dir }}` the same way (Task 6 review).

---

### Task 11: StorageBaby becomes a host repository

**Files (in the `cradle-split` worktree):**

- Delete: `ansible/`, `bootstrap.sh`, `tests/`, `.ansible-lint`, `hosts/test-a`, `hosts/test-ci`, `.claude/skills/adding-a-service`, `docs/architecture.md`, `docs/external-service.md`, `docs/quickstart.md`, `docs/ownership.md`, `compress-subfolders.sh` (untracked leftover: leave it)
- Modify: `mise.toml`, `.github/workflows/ci.yml`, `devtools/Dockerfile`, `devtools/requirements*` (delete), `.gitignore`, `.prettierignore`, `lefthook.yml` (unchanged), `README.md`, `CLAUDE.md`, `hosts/StorageBaby/services/*/README.md` (`storagebaby-svc` → `cradle-svc`, `/etc/storagebaby/` → `/etc/cradle/`)
- Keep: `hosts/StorageBaby/**`, `hosts/shared/**`, `.sops.yaml`, `docs/runbooks/`, `docs/superpowers/`, `.prettierrc`

- [ ] **Step 1: Worktree**

Use `superpowers:using-git-worktrees` to create `.claude/worktrees/cradle-split` on branch `cradle-split` from `master`.

- [ ] **Step 2: Remove the engine**

```bash
cd /home/jlk/Projects/StorageBaby/.claude/worktrees/cradle-split
git rm -rq ansible bootstrap.sh tests .ansible-lint hosts/test-a hosts/test-ci .claude/skills/adding-a-service docs/architecture.md docs/external-service.md docs/quickstart.md docs/ownership.md devtools/requirements.txt devtools/requirements.yml
sed -i '/^tests\/fixtures/d; /^hosts\/test-\*/d; /^\.pytest_cache/d; /^__pycache__/d' .gitignore
sed -i '/^hosts\/test-\*\/secrets\/$/d' .prettierignore
```

- [ ] **Step 3: devtools is prettier only**

`devtools/Dockerfile`:

```dockerfile
# Formatting only: a host repository has nothing to run but prettier. Cradle carries the
# Ansible, Molecule and pytest tooling.
FROM docker.io/library/node:22-alpine
WORKDIR /opt/devtools
COPY package.json package-lock.json ./
RUN npm ci
ENV PATH="/opt/devtools/node_modules/.bin:${PATH}"
WORKDIR /repo
```

`mise.toml`: keep `[env]` `DEVTOOLS_IMAGE`, `AGE_DIR`, `DEVTOOLS_OPTS`, `DEVTOOLS_OPTS_AGE`; keep tasks `devtools`, `devtools-shell`, `format`, `fmt-check`, `sops`, `updatekeys`, `install-hooks`; delete every Molecule/VM/test task and the `SCENARIO`/`MOLECULE_*`/`LIBVIRT_*` env. Rewrite the header comment: "A host repository for Cradle (github.com/janlucaklees/cradle). Everything that converges a host lives there; here are formatting and secrets."

`.github/workflows/ci.yml`:

```yaml
name: CI
on:
  push:
    branches: [master]
  pull_request:
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: false
env:
  MISE_TRUSTED_CONFIG_PATHS: ${{ github.workspace }}
jobs:
  static:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: jdx/mise-action@v2
      - run: mise run devtools
      - run: mise run fmt-check
  promote:
    runs-on: ubuntu-latest
    needs: [static]
    if: github.ref == 'refs/heads/master' && github.event_name == 'push'
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      # The host tracks `stable`. Formatting is the whole gate here: a placement mistake is
      # refused by Cradle at converge, on the host, loudly -- not by a pipeline.
      - run: git push origin HEAD:refs/heads/stable
```

- [ ] **Step 4: README.md and CLAUDE.md**

`README.md`: keep "Layout" (rewritten: `hosts/StorageBaby/{host.yml,secrets,services}`, `hosts/shared`, and "the engine is Cradle: github.com/janlucaklees/cradle, checked out at /home/jlk/Projects/cradle"), "Services", "Storage" (with the maintenance run), "Networking", "Operator steps before and right after the first storagebaby deploy" (historic; add one line saying so), "Migrating existing service data", and add **"Known drift"** holding ownership.md § 7 items 13–16 verbatim. Delete "External services", "Secrets, in one minute", "New host", "Operating a service" and "Working on the repo", replacing them with one section "Operating and working on this repository" that says: `cradle-svc` on the host, `mise run sops`/`updatekeys`/`format` here, everything else in Cradle's README. Every `storagebaby-svc` → `cradle-svc`, `/etc/storagebaby` → `/etc/cradle`, `storagebaby-deploy` → `cradle-deploy`.

`CLAUDE.md`: a host repository's. Keep the "What this repo is" paragraph rewritten ("the host repository of StorageBaby; the engine is Cradle at …; the contract reference and the adding-a-service skill live there"), "Managing services" (placement, the `service.yml` key summary, pods, test placement replaced by "there are no test hosts here; Cradle's fixtures are"), "Storage architecture", "Storage maintenance pipeline", "Networking", "Adding a new disk", "Deployment" (rewritten: bootstrap is Cradle's, the five values, `cradle-deploy --check`), the formatting section, and the "Start here" pointing at Cradle's docs. Delete "Known drift at 60b402f" (the `make` item is resolved by the split; the paperless hook item stays, in README's Known drift).

```bash
rg -l "storagebaby-svc|/etc/storagebaby|storagebaby-deploy|/opt/storagebaby" hosts README.md CLAUDE.md docs/runbooks | xargs sed -i 's|storagebaby-svc|cradle-svc|g; s|/etc/storagebaby|/etc/cradle|g; s|storagebaby-deploy|cradle-deploy|g; s|/opt/storagebaby|/opt/cradle|g; s|/var/lib/storagebaby/repo|/var/lib/cradle/host|g; s|/var/lib/storagebaby/sources|/var/lib/cradle/sources|g'
rg -n "/var/lib/storagebaby" hosts README.md CLAUDE.md # only `fast` may remain
```

- [ ] **Step 5: Check and commit**

Run: `cd .claude/worktrees/cradle-split && mise run devtools && mise run fmt-check` — PASS. Commit through the `commit` skill: `refactor!: this repository is a host repository; the engine is Cradle`.

---

### Task 12: Plan copy and the spec's status

- [ ] **Step 1:** In the worktree, set the spec's `Status:` to `implemented 2026-10-xx (stage 1)` once Task 15 is done — do it in Task 16. Now: copy this plan and the spec into Cradle: `cp docs/superpowers/{specs/2026-10-16-cradle-split-design.md,plans/2026-10-16-cradle-split.md} /home/jlk/Projects/cradle/docs/superpowers/{specs,plans}/`, commit in Cradle (`docs: the split's spec and plan`), push.

---

### Task 13: Render parity

- [ ] **Step 1: Render StorageBaby with Cradle**

```bash
B=/tmp/claude-1000/-home-jlk-Projects-StorageBaby/205176ee-280c-48c7-baa9-d3b66f2e4832/scratchpad/baseline
cd /home/jlk/Projects/cradle
mise run render /home/jlk/Projects/StorageBaby/.claude/worktrees/cradle-split hosts/StorageBaby "$B/after"
```

- [ ] **Step 2: Diff modulo the rename**

```bash
cd "$B" && cp -a render before-sub && cd before-sub
find . -type f | xargs sed -i 's|/etc/storagebaby|/etc/cradle|g; s|/opt/storagebaby|/opt/cradle|g; s|10-storagebaby-hosts|10-cradle-hosts|g; s|storagebaby-svc|cradle-svc|g'
find . -depth -name '10-storagebaby-hosts.conf' -execdir mv {} 10-cradle-hosts.conf \;
diff -r "$B/before-sub" "$B/after" && echo PARITY
```

Expected: `PARITY`. A difference means stage 1 changed behaviour: read it, fix the cause in Cradle (never by editing the baseline), re-run Tasks 10 and 13. `/var/lib/storagebaby/fast` appears identically on both sides.

---

### Task 14: Cutover on StorageBaby (the live host)

Pre-conditions, all checked before touching the host: Cradle `stable` exists and equals the SHA that passed CI (Task 10); Task 13 printed `PARITY`; the time is not within 01:30–04:00 (maintenance at 02:00) and `ssh root@192.168.178.2 'systemctl --user -M svc-kopia@ list-timers' ` shows the hourly replica push not running; no Molecule run is in progress on the workstation. **StorageBaby's `stable` is still the pre-split tree**, and that is intended: Cradle reads `hosts/StorageBaby` out of it and ignores the rest, so the old tick keeps working until bootstrap disables it and nothing races.

- [ ] **Step 1: Register Cradle's deploy key from the host**

```bash
ssh root@192.168.178.2 'systemctl disable --now storagebaby-deploy.timer; systemctl is-active storagebaby-deploy.service' # inactive
ssh root@192.168.178.2 'curl -fsSL https://raw.githubusercontent.com/janlucaklees/cradle/stable/bootstrap.sh -o /usr/local/sbin/cradle-bootstrap && chmod 0755 /usr/local/sbin/cradle-bootstrap' \
	|| { # private repository: fetch it with gh here and copy it over
		cd /home/jlk/Projects/cradle && git show stable:bootstrap.sh > /tmp/claude-1000/-home-jlk-Projects-StorageBaby/205176ee-280c-48c7-baa9-d3b66f2e4832/scratchpad/cradle-bootstrap \
			&& scp /tmp/claude-1000/-home-jlk-Projects-StorageBaby/205176ee-280c-48c7-baa9-d3b66f2e4832/scratchpad/cradle-bootstrap root@192.168.178.2:/usr/local/sbin/cradle-bootstrap && ssh root@192.168.178.2 chmod 0755 /usr/local/sbin/cradle-bootstrap
	}
ssh root@192.168.178.2 'cradle-bootstrap --local --no-upgrade --cradle-repo git@github.com:janlucaklees/cradle.git --host-repo git@github.com:janlucaklees/StorageBaby.git --host-path hosts/StorageBaby'
```

Expected: adoption messages, then "Cradle could not be cloned … add this as a read-only deploy key", exit 1, the key printed. Then:

```bash
ssh root@192.168.178.2 cat /etc/cradle/cradle_key.pub > /tmp/claude-1000/-home-jlk-Projects-StorageBaby/205176ee-280c-48c7-baa9-d3b66f2e4832/scratchpad/cradle_key.pub
gh repo deploy-key add /tmp/claude-1000/-home-jlk-Projects-StorageBaby/205176ee-280c-48c7-baa9-d3b66f2e4832/scratchpad/cradle_key.pub -R janlucaklees/cradle -t storagebaby
ssh root@192.168.178.2 'cradle-bootstrap --local --no-upgrade --cradle-repo git@github.com:janlucaklees/cradle.git --host-repo git@github.com:janlucaklees/StorageBaby.git --host-path hosts/StorageBaby'
```

Expected: exit 0, `/var/lib/cradle/cradle` cloned, `cradle-deploy` installed, timer installed but not enabled.

- [ ] **Step 2: Verify the adoption left data alone**

```bash
ssh root@192.168.178.2 'ls -la /etc/cradle /var/lib/cradle; ls /var/lib/storagebaby; test -d /var/lib/storagebaby/fast && echo fast-ok; test ! -e /var/lib/storagebaby/repo && echo repo-moved; podman ps -a --format "{{.Names}} {{.Status}}" 2>/dev/null | head -3'
```

Expected: `fast-ok`, `repo-moved`, `/var/lib/storagebaby` holds only `fast`, every service still running (containers hold their old bind mounts open; **restart nothing by hand before the converge**).

- [ ] **Step 3: Pre-flight**

```bash
ssh root@192.168.178.2 'cradle-deploy --check 2>&1 | tail -120'
```

Expected: fetches Cradle, the host repository and renaissance; migration 0001 reports the removals it would make; the storage role reports **no remount** (the pool declaration is unchanged); every service's units show the `/etc/storagebaby` → `/etc/cradle` path change and the drop-in rename; `PLAY RECAP … failed=0`. Anything beyond that list is unexpected: stop, read, fix in Cradle, push, wait for `stable`, re-run this step.

- [ ] **Step 4: The converge**

```bash
ssh root@192.168.178.2 'cradle-deploy 2>&1 | tail -60'
```

Expected: `failed=0`; every service restarted once. Then:

```bash
ssh root@192.168.178.2 'for s in traefik kopia paperless nextcloud immich jellyfin; do cradle-svc ps $s | sed -n 1,3p; done; systemctl cat storage-maintenance.service | grep ExecStart; ls /opt/cradle/maintenance; test ! -e /opt/storagebaby && echo opt-clean; test ! -e /usr/local/sbin/storagebaby-svc && echo svc-clean; cat /var/lib/cradle/deploy.state'
curl -sS -o /dev/null -w '%{http_code}\n' https://jellyfin.home.klees.io/
curl -sS -o /dev/null -w '%{http_code}\n' https://nextcloud.klees.io/status.php
```

Expected: every unit `active`, `ExecStart=/opt/cradle/maintenance/…`, `opt-clean`, `svc-clean`, a state file with `cradle`, `host` and `source:renaissance` lines, both probes `200`.

- [ ] **Step 5: Enable the timer and watch one no-op tick**

```bash
ssh root@192.168.178.2 'systemctl enable --now cradle-deploy.timer; sleep 1; systemctl start cradle-deploy.service; journalctl -u cradle-deploy.service -n 5 --no-pager'
```

Expected: "nothing changed since the last converge".

---

### Task 15: Merge the StorageBaby split

- [ ] **Step 1:** Push `cradle-split`, open the PR with `gh pr create` (title "This repository is a host repository; the engine is Cradle", body from the spec's § 1), merge it with `gh pr merge --merge`, `gh run watch` the master run: `static` green, `stable` promoted.

- [ ] **Step 2:** On the host, the next tick fetches the new `stable`: `ssh root@192.168.178.2 'systemctl start cradle-deploy.service; journalctl -u cradle-deploy.service -n 40 --no-pager'`. Expected: a converge with `changed=0` on every service (parity), or only the README copies under `/etc/cradle/<svc>/` if a service's `config/` README changed — read the recap; nothing restarts.

- [ ] **Step 3:** Remove the worktree (`superpowers:using-git-worktrees` cleanup), pull master in `/home/jlk/Projects/StorageBaby`.

---

### Task 16: Close the stage

- [ ] **Step 1:** In StorageBaby: spec `Status: implemented <date> (stage 1); stages 2–4 open`. Write `docs/runbooks/<date>-cradle-cutover.md`: what Task 14 did on the host, in the order it happened, with the `--check` output's summary, so stage 2's cutover has a model. Commit (`docs: record the Cradle cutover`), push master.
- [ ] **Step 2:** In Cradle: copy the updated spec; commit; push.
- [ ] **Step 3:** Update the memory file `cradle-split-programme.md`: stage 1 done, `stable` SHAs, the two deploy keys registered, next is stage 2's spec.
