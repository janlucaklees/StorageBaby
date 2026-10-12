# Renaissance as the first external service: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The host `StorageBaby` runs Renaissance at `https://renaissance.klees.io` from a deploy contract in the Renaissance repository, through the external-services mechanism, with both CI pipelines able to promote config-only pushes without their slow suite and the integration suite no longer hanging in prepare's reboot.

**Architecture:** Three independent deliverables in two repositories. In StorageBaby: a pre-upgraded base image for the Molecule VMs (an imported task file in the create step, cached in CI), a path-aware CI gate, and the pointer plus host credentials. In Renaissance: `deploy/storagebaby/` (one pod: nginx split, Flask, SvelteKit, Postgres), a root `.sops.yaml`, and the same path-aware CI gate. Then the sequence that moves `stable` in both repositories and watches the host.

**Tech Stack:** Ansible (Molecule, community.libvirt), libvirt/qemu-img, cloud-init, GitHub Actions, sops/age, Podman Quadlet, nginx, Flask/gunicorn, SvelteKit adapter-node, Postgres 13.

**Spec:** `docs/superpowers/specs/2026-10-12-renaissance-first-external-service-design.md`

## Global Constraints

- Never decrypt a sops file in a command (`-d`, `--decrypt`, `exec-*`); write with `--encrypt` or `set`, verify by key names only. Never print a secret or token.
- Only one Molecule run on the workstation VM at a time; coordinate with session `storagebaby-b0` over SendMessage before `mise run test-integration`.
- StorageBaby static suite after every change under `hosts/` or `tests/`: `mise run test-static`. Commit only after it is green.
- No apostrophes inside a `shell:` block of a Molecule playbook, comments included.
- Renaissance: the pre-commit hook runs `mise run check:static`; commit from a fresh worktree, never from JLK's checkout at `/home/jlk/Projects/Renaissance` (uncommitted work there).
- Commit message format: `docs/sop/git-commit-message-format.md` in Renaissance (title, summary, past tense); StorageBaby uses the same voice (`fix:`/`feat:`/`docs:` prefixes as in `git log`).
- Every file written here carries the project's commenting voice: say why, measured where possible.
- Pod port for Renaissance: `8300`. Service name: `renaissance`. Hostname: `renaissance.klees.io`.
- Work in the StorageBaby worktree `/home/jlk/Projects/StorageBaby/.claude/worktrees/external-services` (branch `external-services`) until Task 8 merges it.

---

### Task 1: The base-image stage, as a task file with a standalone playbook

**Files:**

- Create: `tests/integration/molecule/test-ci/vars/images.yml`
- Create: `tests/integration/molecule/test-ci/tasks/base-image-stage.yml`
- Create: `tests/integration/molecule/test-ci/base-image.yml`
- Create: `tests/integration/molecule/templates/base-image-user-data.j2`
- Modify: `tests/integration/molecule/test-ci/create.yml` (vars block, the cached-image task, the overlay task)
- Modify: `tests/static/test_lint.py:6` (add `base-image.yml` to the parsed playbooks)
- Modify: `mise.toml` (task `test-base-image`)

**Interfaces:**

- Produces: `prepared_image` (`/var/lib/libvirt/images/storagebaby/arch-cloudimg-prepared.qcow2`), the backing file every platform overlay uses from now on; `mise run test-base-image` to force a rebuild.
- Consumes: `domain.xml.j2` (unchanged; rendered with `storage_disk_count: 0` for the builder).

- [ ] **Step 1: The shared variables file**

`tests/integration/molecule/test-ci/vars/images.yml`:

```yaml
# The images the test VMs boot from, shared by create.yml and base-image.yml so the two
# cannot disagree on a path. destroy.yml keeps its own `images_dir` on purpose: it
# removes platform artifacts only and must never learn the names of the two cached
# images below.
libvirt_uri: qemu:///system
images_dir: /var/lib/libvirt/images/storagebaby
base_image_url: https://geo.mirror.pkgbuild.com/images/latest/Arch-Linux-x86_64-cloudimg.qcow2
# The stock cloud image, downloaded once and never revalidated: `get_url` with
# `force: false` still asks the server whether the file moved, and one run died on that
# request with the 578 MB file already present (DNS blip inside the devtools container).
base_image: '{{ images_dir }}/arch-cloudimg.qcow2'
# The stock image after one full `pacman -Syu`, flattened. Every platform overlays this
# one. It exists so bootstrap's own `-Syu` in prepare finds the kernel already current
# and the compare-and-reboot task after it has nothing to do -- that reboot is the step
# that hung a CI job for 86 minutes on 2026-10-07 and left `stable` behind `master`.
prepared_image: '{{ images_dir }}/arch-cloudimg-prepared.qcow2'
# Rebuilt when older than this. A week keeps the kernel within the window in which the
# repositories rarely move it; a kernel that lands mid-week still triggers the reboot,
# which is the one case the reboot is right for.
prepared_image_max_age_days: 7
# The stock image is only re-downloaded as part of a rebuild, and only when it is this
# old, so the weekly upgrade starts from something recent rather than from the first
# image this workstation ever cached.
base_image_max_age_days: 30
builder_name: storagebaby-base-builder
```

- [ ] **Step 2: The builder's cloud-init**

`tests/integration/molecule/templates/base-image-user-data.j2`:

```yaml
#cloud-config
# The throwaway VM that turns the stock Arch cloud image into the prepared one. Nothing
# logs in: it upgrades, cleans up after itself and powers off, and create.yml flattens
# what is left. Only the upgrade is done here -- bootstrap still installs its packages in
# prepare and the roles still install theirs, so the image is the stock image a week
# newer, not a pre-provisioned host.
growpart:
  mode: auto
  devices: ['/']
runcmd:
  # The keyring first and on its own: a cloud image that lags the repositories by weeks
  # can hold a keyring too old to verify what the full upgrade is about to install.
  - pacman -Sy --noconfirm archlinux-keyring
  - pacman -Syu --noconfirm
  - pacman -Scc --noconfirm
  # The next boot is a first boot: new instance-id, the platform's own hostname and SSH
  # key from its own seed, fresh host keys. Without this the builder's instance state
  # would make cloud-init treat the test VM as an already-configured instance.
  - cloud-init clean --logs
  # Each overlay gets its own machine-id on first boot. Shared, every test VM would
  # present the same DHCP client identifier (systemd-networkd derives it from this file)
  # and two of them would be handed one lease.
  - truncate -s 0 /etc/machine-id
power_state:
  mode: poweroff
  timeout: 60
  condition: true
```

- [ ] **Step 3: The task file**

`tests/integration/molecule/test-ci/tasks/base-image-stage.yml`:

```yaml
# Make sure the prepared base image exists and is fresh. Imported by create.yml before
# the platform overlays are made, and runnable on its own through base-image.yml. Uses
# the variables of vars/images.yml and the play's `ssh_key` is not needed: nothing logs
# into the builder.
- name: Stat the stock cloud image
  ansible.builtin.stat:
    path: '{{ base_image }}'
  register: _base_stat

- name: Stat the prepared image
  ansible.builtin.stat:
    path: '{{ prepared_image }}'
  register: _prepared_stat

# gather_facts is off in create.yml, so the clock comes from the controller directly.
- name: Decide whether the prepared image is rebuilt
  ansible.builtin.set_fact:
    _now: "{{ lookup('pipe', 'date +%s') | int }}"
    _rebuild: >-
      {{ (prepared_image_force | default(false) | bool)
         or (not _prepared_stat.stat.exists)
         or ((lookup('pipe', 'date +%s') | int) - (_prepared_stat.stat.mtime | int)
             > (prepared_image_max_age_days | int) * 86400) }}

- name: Drop a stock image that is too old to upgrade from, when rebuilding
  ansible.builtin.file:
    path: '{{ base_image }}'
    state: absent
  when:
    - _rebuild | bool
    - _base_stat.stat.exists
    - (_now | int) - (_base_stat.stat.mtime | int) > (base_image_max_age_days | int) * 86400

- name: Stat the stock cloud image again
  ansible.builtin.stat:
    path: '{{ base_image }}'
  register: _base_stat

- name: Download the stock cloud image when it is absent
  ansible.builtin.get_url:
    url: '{{ base_image_url }}'
    dest: '{{ base_image }}'
    mode: '0644'
  when: not _base_stat.stat.exists

- name: Build the prepared image
  when: _rebuild | bool
  vars:
    _builder:
      name: '{{ builder_name }}'
      hostname: '{{ builder_name }}'
      memory_mib: 2048
      vcpus: 2
    _seed_dir: '{{ images_dir }}/{{ builder_name }}-seed'
  block:
    - name: Remove a builder a previous run left behind
      community.libvirt.virt:
        uri: '{{ libvirt_uri }}'
        name: '{{ builder_name }}'
        state: destroyed
      failed_when: false

    - name: Undefine it
      community.libvirt.virt:
        uri: '{{ libvirt_uri }}'
        name: '{{ builder_name }}'
        command: undefine
      failed_when: false

    - name: Fresh builder overlay on the stock image
      ansible.builtin.command: >-
        qemu-img create -f qcow2 -b {{ base_image }} -F qcow2
        {{ images_dir }}/{{ builder_name }}.qcow2 20G
      changed_when: true

    - name: Seed directory
      ansible.builtin.file:
        path: '{{ _seed_dir }}'
        state: directory
        mode: '0755'

    - name: Seed meta-data
      ansible.builtin.template:
        src: ../templates/meta-data.j2
        dest: '{{ _seed_dir }}/meta-data'
        mode: '0644'
      vars:
        item: '{{ _builder }}'

    - name: Seed user-data
      ansible.builtin.template:
        src: ../templates/base-image-user-data.j2
        dest: '{{ _seed_dir }}/user-data'
        mode: '0644'

    - name: Seed ISO
      ansible.builtin.command: >-
        mkisofs -output {{ images_dir }}/{{ builder_name }}-seed.iso -volid cidata -joliet -rock
        {{ _seed_dir }}/user-data {{ _seed_dir }}/meta-data
      changed_when: true

    # The platform template, with no storage disks: the builder has nothing to partition.
    - name: Define the builder
      community.libvirt.virt:
        uri: '{{ libvirt_uri }}'
        command: define
        xml: "{{ lookup('template', '../templates/domain.xml.j2') }}"
      vars:
        item: '{{ _builder }}'
        storage_disk_count: 0

    - name: Start the builder
      community.libvirt.virt:
        uri: '{{ libvirt_uri }}'
        name: '{{ builder_name }}'
        state: running

    # The upgrade downloads a few hundred megabytes and installs them; measured locally
    # at 3 to 5 minutes. Twenty minutes is the budget before this is called a failure,
    # and a builder still running then is left running for `virsh console`.
    - name: Wait for the builder to power itself off
      ansible.builtin.command: virsh -c {{ libvirt_uri }} domstate {{ builder_name }}
      register: _state
      retries: 120
      delay: 10
      until: _state.stdout is search('shut off')
      changed_when: false

    # No backing file: the stock image can be replaced underneath without breaking the
    # chain, and a flat file is what the CI cache stores.
    - name: Flatten the overlay into the prepared image
      ansible.builtin.command: >-
        qemu-img convert -O qcow2
        {{ images_dir }}/{{ builder_name }}.qcow2 {{ prepared_image }}.tmp
      changed_when: true

    - name: Move it into place
      ansible.builtin.command: mv -f {{ prepared_image }}.tmp {{ prepared_image }}
      changed_when: true

    - name: Undefine the builder
      community.libvirt.virt:
        uri: '{{ libvirt_uri }}'
        name: '{{ builder_name }}'
        command: undefine

    - name: Remove the builder overlay, seed ISO and seed directory
      ansible.builtin.file:
        path: '{{ item }}'
        state: absent
      loop:
        - '{{ images_dir }}/{{ builder_name }}.qcow2'
        - '{{ images_dir }}/{{ builder_name }}-seed.iso'
        - '{{ _seed_dir }}'
```

- [ ] **Step 4: The standalone playbook**

`tests/integration/molecule/test-ci/base-image.yml`:

```yaml
# Rebuild the prepared base image on its own: `mise run test-base-image`. create.yml
# runs the same tasks before every scenario and rebuilds once a week; this is for the
# day a kernel lands mid-week, or to try the stage without a whole Molecule run.
- name: Prepare the base image the test VMs boot from
  hosts: localhost
  connection: local
  gather_facts: false
  vars_files:
    - vars/images.yml
  vars:
    prepared_image_force: true
  tasks:
    - ansible.builtin.import_tasks: tasks/base-image-stage.yml
```

- [ ] **Step 5: Wire it into create.yml**

In `tests/integration/molecule/test-ci/create.yml`:

Replace the `vars:` block's first five entries (`libvirt_uri`, `images_dir`, `base_image_url`, `base_image`, `ssh_key`) with a `vars_files` entry and keep what the play needs itself:

```yaml
- name: Create libvirt VMs
  hosts: localhost
  connection: local
  gather_facts: false
  vars_files:
    - vars/images.yml
  vars:
    ssh_key: '{{ molecule_ephemeral_directory }}/id_ed25519'
    # The storage role's disks. ...(existing comment unchanged)
    storage_disk_count: 4
    storage_disk_size: 4G
```

Replace the task `Cached Arch cloud image (downloaded once)` with:

```yaml
# The stock image, and the pre-upgraded one every platform overlays. tasks/base-image-stage.yml
# has the reasons; vars/images.yml the paths.
- name: The base images
  ansible.builtin.import_tasks: tasks/base-image-stage.yml
```

Change the overlay task's backing file from `{{ base_image }}` to `{{ prepared_image }}`:

```yaml
- name: Overlay disks
  ansible.builtin.command: qemu-img create -f qcow2 -b {{ prepared_image }} -F qcow2 {{ images_dir }}/{{ item.name }}.qcow2 {{ item.disk }}
```

- [ ] **Step 6: The lint test parses the new playbook**

`tests/static/test_lint.py:6`:

```python
MOLECULE_PLAYBOOKS = {"create.yml", "prepare.yml", "converge.yml", "destroy.yml", "verify.yml", "base-image.yml"}
```

- [ ] **Step 7: The mise task**

In `mise.toml`, after `[tasks.test-clean]`:

```toml
[tasks.test-base-image]
description = "Rebuild the pre-upgraded base image the test VMs boot from (create does this on its own once a week)"
run = 'docker run --rm -t $DEVTOOLS_OPTS_VM $DEVTOOLS_IMAGE ansible-playbook tests/integration/molecule/test-ci/base-image.yml'
```

- [ ] **Step 8: Static suite**

Run: `cd /home/jlk/Projects/StorageBaby/.claude/worktrees/external-services && mise run test-static`
Expected: all passed; in particular `test_molecule_playbooks_parse` covers `base-image.yml` and `create.yml` with the import.

- [ ] **Step 9: Build the image standalone (the VM may be busy; the builder is its own domain)**

Run: `mise run test-base-image`
Expected: the builder boots, powers off within a few minutes, `arch-cloudimg-prepared.qcow2` appears beside the stock image, no builder domain or files left:

```
ls -la /var/lib/libvirt/images/storagebaby/
virsh -c qemu:///system list --all
```

If the wait times out: `virsh -c qemu:///system console storagebaby-base-builder` shows cloud-init's output.

- [ ] **Step 10: Commit**

```bash
git add tests/integration/molecule/test-ci/vars/images.yml tests/integration/molecule/test-ci/tasks/base-image-stage.yml tests/integration/molecule/test-ci/base-image.yml tests/integration/molecule/templates/base-image-user-data.j2 tests/integration/molecule/test-ci/create.yml tests/static/test_lint.py mise.toml
git commit -m "Boot the test VMs from a pre-upgraded base image so prepare never has to reboot"
```

Body: the 86-minute hang in the reboot task on 2026-10-07, the revalidation flake, the weekly rebuild, what is deliberately not baked in.

---

### Task 2: Cache the two images in CI

**Files:**

- Modify: `.github/workflows/ci.yml` (the `integration` job)

**Interfaces:**

- Consumes: the paths from Task 1 (`/var/lib/libvirt/images/storagebaby/arch-cloudimg.qcow2`, `arch-cloudimg-prepared.qcow2`).

- [ ] **Step 1: Restore before, save after**

In the `integration` job, before `- run: mise run devtools`:

```yaml
# The stock cloud image and the pre-upgraded one the VMs overlay (tasks/base-image-stage.yml).
# Restore and save as two explicit steps rather than `actions/cache`, whose single
# step saves only when the job succeeds -- and the job that times out is the one
# that most needs its image kept for the next run. Keyed on the ISO week so the
# weekly rebuild happens once per week per key; the prefix fallback hands a new
# week last week's images, which the seven-day rule then rebuilds from the cached
# stock image instead of from the network.
- name: Images directory, writable for the cache
  run: sudo install -d -m 0755 -o "$USER" -g "$USER" /var/lib/libvirt/images/storagebaby
- id: week
  run: echo "week=$(date +%G-W%V)" >> "$GITHUB_OUTPUT"
- uses: actions/cache/restore@v4
  id: images
  with:
    path: /var/lib/libvirt/images/storagebaby/*.qcow2
    key: base-images-${{ steps.week.outputs.week }}-${{ hashFiles('tests/integration/molecule/test-ci/tasks/base-image-stage.yml', 'tests/integration/molecule/templates/base-image-user-data.j2') }}
    restore-keys: base-images-
```

After `- run: mise run test-integration`:

```yaml
- uses: actions/cache/save@v4
  if: always() && steps.images.outputs.cache-hit != 'true'
  with:
    path: /var/lib/libvirt/images/storagebaby/*.qcow2
    key: base-images-${{ steps.week.outputs.week }}-${{ hashFiles('tests/integration/molecule/test-ci/tasks/base-image-stage.yml', 'tests/integration/molecule/templates/base-image-user-data.j2') }}
```

Note: the `path` glob saves only the two cached images, never a platform overlay (`test-ci.qcow2`) that a timed-out run leaves behind -- those are named after platforms, and `destroy` runs first anyway.

- [ ] **Step 2: Static suite and commit**

Run: `mise run test-static` (there is no workflow lint; `fmt-check` covers YAML formatting: `mise run fmt-check`).

```bash
git add .github/workflows/ci.yml
git commit -m "Cache the test base images across CI runs"
```

---

### Task 3: StorageBaby CI skips integration for host-config-only pushes

**Files:**

- Modify: `.github/workflows/ci.yml` (new `changes` job, `integration.if`, `promote.if` and its comment)

- [ ] **Step 1: The decision job**

Before `static:`:

```yaml
# Which suite a push needs. A pointer folder, the host's registry list, its secret
# files and the docs are checked by `static` and resolved on the real host; nothing in
# the Molecule scenario reads them (the test hosts symlink service *folders*, never a
# pointer). So a push that touches only those skips `integration`. The decision is a
# plain diff, not a workflow path filter: a second workflow with the complementary
# `paths` would also run on a mixed push and promote `stable` past an untested commit.
# Anything that is not a clean diff -- the first push of a branch, a pull request, a
# `before` the checkout does not hold -- counts as "everything changed".
changes:
  runs-on: ubuntu-latest
  outputs:
    integration: ${{ steps.detect.outputs.integration }}
  steps:
    - uses: actions/checkout@v4
      with:
        fetch-depth: 0
    - id: detect
      run: |
        set -euo pipefail
        integration=true
        before='${{ github.event.before }}'
        if [ "${{ github.event_name }}" = push ] && [ -n "$before" ] \
           && [ "$before" != 0000000000000000000000000000000000000000 ] \
           && git cat-file -e "$before^{commit}" 2>/dev/null; then
          changed=$(git diff --name-only "$before" "${{ github.sha }}")
          printf 'changed:\n%s\n' "$changed"
          skippable='^(docs/|hosts/StorageBaby/host\.yml$|hosts/StorageBaby/secrets/|hosts/StorageBaby/services/[^/]+/source\.yml$)|\.md$'
          if [ -n "$changed" ] && ! printf '%s\n' "$changed" | grep -Ev "$skippable" | grep -q .; then
            integration=false
          fi
        fi
        echo "integration=$integration" | tee -a "$GITHUB_OUTPUT"
```

- [ ] **Step 2: Gate the integration job and spell out promote**

`integration`:

```yaml
integration:
  runs-on: ubuntu-latest
  needs: [changes, static]
  if: needs.changes.outputs.integration == 'true'
```

`promote` -- replace `needs` and `if`, and the comment above them:

```yaml
promote:
  runs-on: ubuntu-latest
  # Spelled out in full, because a job whose `needs` was skipped is itself skipped
  # unless its `if` says otherwise -- and the one thing this job must never do is move
  # `stable` past a red or cancelled run. Two ways in: both suites green, or the
  # `changes` job said integration was not needed and it really was skipped while
  # static passed. A failed `static` skips `integration`, and skipped-with-static-failed
  # matches neither branch.
  needs: [changes, static, integration]
  if: >-
    !cancelled()
    && github.ref == 'refs/heads/master' && github.event_name == 'push'
    && (
      (needs.static.result == 'success' && needs.integration.result == 'success')
      || (needs.changes.outputs.integration == 'false'
          && needs.static.result == 'success'
          && needs.integration.result == 'skipped')
    )
```

- [ ] **Step 3: Check and commit**

Run: `mise run fmt-check && mise run test-static`

```bash
git add .github/workflows/ci.yml
git commit -m "Skip the integration job for a push that changes only host config or docs"
```

---

### Task 4: Spec, plan, handover, docs committed

**Files:**

- Commit: `docs/superpowers/specs/2026-10-12-renaissance-first-external-service-design.md`, `docs/superpowers/plans/2026-10-12-renaissance-first-external-service.md`
- Modify: `tests/README.md` (the two images, the weekly rebuild, `test-base-image`); `README.md` § Tests if it names the cloud image.
- `HANDOVER.md` and `readme-full.md` are untracked files in the worktree root: leave them untracked (JLK's).

- [ ] **Step 1: tests/README.md**

Add under the section that describes the scenario's VM (find with `grep -n 'cloud' tests/README.md`):

```markdown
### The two base images

`create` keeps two files under `/var/lib/libvirt/images/storagebaby/`: the stock Arch
cloud image (`arch-cloudimg.qcow2`, downloaded once, never revalidated) and the same
image after a full `pacman -Syu` (`arch-cloudimg-prepared.qcow2`). Every test VM is an
overlay of the prepared one. It is rebuilt when older than a week, by a throwaway VM
that upgrades, cleans cloud-init and the machine-id, and powers off
(`tests/integration/molecule/test-ci/tasks/base-image-stage.yml`). `mise run test-base-image`
rebuilds it now. The point is that bootstrap's own upgrade in `prepare` then finds the
kernel current and the compare-and-reboot task after it skips -- the reboot was what
hung a CI job for 86 minutes on 2026-10-07. CI caches both files per ISO week.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-10-12-renaissance-first-external-service-design.md docs/superpowers/plans/2026-10-12-renaissance-first-external-service.md tests/README.md README.md
git commit -m "Spec and plan for Renaissance as the first external service"
```

---

### Task 5: Local test-a on the branch

- [ ] **Step 1: Coordinate the VM**

SendMessage to `storagebaby-b0`: taking the VM now, ~90 minutes. Wait for its "done" first if its run is still going (`pgrep -af molecule` on the workstation shows a running `molecule test`).

- [ ] **Step 2: Run**

Run: `cd /home/jlk/Projects/StorageBaby/.claude/worktrees/external-services && mise run test-integration 2>&1 | tee /tmp/claude-1000/-home-jlk-Projects-Renaissance/06226db6-529d-46d0-85e0-975f026a90a2/scratchpad/test-a.log` in the background, polled with `tail`.
Expected: `Reboot when bootstrap's upgrade stranded the running kernel` reports `skipping`; converge, idempotence (`changed=0`), verify all green.

- [ ] **Step 3: Release the VM**

SendMessage to `storagebaby-b0`: done, VM free.

If red: fix, re-run only what is needed (`mise run molecule converge` / `verify`), and record what it was for the report. The verifier on this branch is ~1 hour; a single failing case is re-run with `mise run molecule verify` after a fix, not the whole sequence.

---

### Task 6: Renaissance deploy contract (own worktree)

**Files (all under the Renaissance worktree):**

- Create: `deploy/storagebaby/service.yml`
- Create: `deploy/storagebaby/quadlet/renaissance.pod.j2`
- Create: `deploy/storagebaby/quadlet/renaissance-proxy.container.j2`
- Create: `deploy/storagebaby/quadlet/renaissance-app.container.j2`
- Create: `deploy/storagebaby/quadlet/renaissance-frontend.container.j2`
- Create: `deploy/storagebaby/quadlet/renaissance-database.container.j2`
- Create: `deploy/storagebaby/config/nginx.conf`
- Create: `deploy/storagebaby/secrets.sops.yaml` (encrypted)
- Create: `deploy/storagebaby/README.md`
- Create: `.sops.yaml` (repository root)
- Modify: `deploy/README.md` (one paragraph pointing at the new folder)

- [ ] **Step 1: Worktree**

```bash
cd /home/jlk/Projects/Renaissance
git worktree add .claude/worktrees/storagebaby-deploy -b storagebaby-deploy master
cd .claude/worktrees/storagebaby-deploy
mise trust && mise run worktree:init
```

- [ ] **Step 2: service.yml**

```yaml
# The StorageBaby contract for this repository: one pod behind renaissance.klees.io.
# Read by the host's playbook from the fetched checkout; nothing in this repository's
# own CI checks it (the host does, at converge). Reference:
# StorageBaby/ansible/roles/service/README.md, model: StorageBaby/hosts/StorageBaby/services/paperless.
name: renaissance
# Free on the host's loopback when this was written; the host refuses a converge in
# which two services claim one port.
port: 8300
# A full hostname, not a label: the host's default zone is home.klees.io, and klees.io
# is in its cert_zones, so the wildcard certificate already covers this name.
domain: renaissance.klees.io
volumes:
  # 70 is `postgres` in the alpine image -- the uid the process has inside the container,
  # which is what the role maps to a host uid.
  database: { class: fast, owner: 70 }
config:
  database_name: renaissance
  database_user: renaissance
  # The account `renaissance database init` creates on an empty database. Its password
  # is the `admin_password` secret.
  admin_email: email@janlucaklees.de
  admin_abbreviation: ADM
secrets: [database_password, secret_key, admin_password]
# A prototype for testing, with nothing in it worth a nightly dump and a kopia client.
backup: none
# Both images are private packages of this repository.
registries: [ghcr.io]
```

- [ ] **Step 3: The pod**

`quadlet/renaissance.pod.j2`:

```
[Pod]
PodName=renaissance
{# Literal, as every pod on the platform writes it: the host's static check reads the
   published ports out of the raw template. One port, nginx's, which splits the one
   hostname between the API and the frontend -- the platform routes a hostname to a
   port and knows no path rules. #}
PublishPort=127.0.0.1:8300:80
```

- [ ] **Step 4: The proxy**

`quadlet/renaissance-proxy.container.j2`:

```
[Unit]
Description=Renaissance proxy (splits /api from the frontend)
After=renaissance-app.service renaissance-frontend.service
Wants=renaissance-app.service renaissance-frontend.service

[Container]
Image=docker.io/library/nginx:stable-alpine
ContainerName=renaissance-proxy
Pod=renaissance.pod
AutoUpdate=registry
Environment=TZ={{ tz }}
{# config/nginx.conf of this folder, deployed by the host to its config_dir. #}
Volume={{ config_dir }}/nginx.conf:/etc/nginx/nginx.conf:ro
{# The frontend answers /login with 200 without a session; busybox wget is in the image. #}
HealthCmd=wget -q --spider http://127.0.0.1:80/login || exit 1
HealthOnFailure=kill

[Service]
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

- [ ] **Step 5: The application**

`quadlet/renaissance-app.container.j2`:

```
[Unit]
Description=Renaissance backend (Flask under gunicorn)
After=renaissance-database.service
Wants=renaissance-database.service

[Container]
Image=ghcr.io/janlucaklees/renaissance:latest
ContainerName=renaissance-app
Pod=renaissance.pod
{# Floating tag on purpose: :latest is published from the stable branch only, which holds
   master commits that passed the full suite. The start command below migrates on every
   start, so an image the auto-update timer swaps in still migrates. #}
AutoUpdate=registry
Environment=TZ={{ tz }}
Environment=FLASK_APP=renaissance
Environment=FLASK_ENV=production
Environment=DEBUG=0
Environment=TESTING=0
Environment=DB_HOST=127.0.0.1
Environment=DB_PORT=5432
Environment=DB_NAME={{ service.config.database_name }}
Environment=DB_USERNAME={{ service.config.database_user }}
{# config.py reads <NAME>_FILE for these two. #}
Environment=DB_PASSWORD_FILE=/run/secrets/database_password
Environment=SECRET_KEY_FILE=/run/secrets/secret_key
Environment=ADMIN_EMAIL={{ service.config.admin_email }}
Environment=ADMIN_ABBREVIATION={{ service.config.admin_abbreviation }}
Secret=database_password
Secret=secret_key
{# type=env: `renaissance database init` reads ADMIN_PASSWORD from the environment and
   would prompt on a terminal nobody is at without it. #}
Secret=admin_password,type=env,target=ADMIN_PASSWORD
{# `database init` creates the schema, stamps alembic head and the admin user on an empty
   database, and prints "already initialized" and exits 0 on every later start. `alembic
   upgrade head` is the migration step after an image update. `set -e` makes a start
   before the database answers fail, and Restart= tries again five seconds later. #}
Exec=sh -c "set -e; renaissance database init; alembic upgrade head; exec gunicorn -b 0.0.0.0:5000 'renaissance:create_app()'"
{# The slim Python image has neither curl nor wget. /login is 200 without a session. #}
HealthCmd=python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/login', timeout=3)"
HealthOnFailure=kill
HealthStartPeriod=120s

[Service]
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

- [ ] **Step 6: The frontend**

`quadlet/renaissance-frontend.container.j2`:

```
[Unit]
Description=Renaissance frontend (SvelteKit)
After=renaissance-app.service
Wants=renaissance-app.service

[Container]
Image=ghcr.io/janlucaklees/renaissance-frontend:latest
ContainerName=renaissance-frontend
Pod=renaissance.pod
AutoUpdate=registry
Environment=TZ={{ tz }}
Environment=PORT=3000
{# adapter-node checks form posts against this origin. #}
Environment=ORIGIN=https://{{ fqdn }}
{# The server-side API client (hooks.server.ts); the browser calls /api on its own origin
   and nginx forwards that. #}
Environment=BACKEND_URL=http://127.0.0.1:5000
HealthCmd=wget -q --spider http://127.0.0.1:3000/login || exit 1
HealthOnFailure=kill

[Service]
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

- [ ] **Step 7: The database**

`quadlet/renaissance-database.container.j2`:

```
[Unit]
Description=Renaissance database

[Container]
{# 13, as the compose files pin it; the alpine image is what owner: 70 in service.yml is read off. #}
Image=docker.io/library/postgres:13-alpine
ContainerName=renaissance-database
Pod=renaissance.pod
AutoUpdate=registry
Environment=TZ={{ tz }}
Environment=POSTGRES_DB={{ service.config.database_name }}
Environment=POSTGRES_USER={{ service.config.database_user }}
Environment=POSTGRES_PASSWORD_FILE=/run/secrets/database_password
Secret=database_password
Volume={{ volumes.database }}:/var/lib/postgresql/data
HealthCmd=pg_isready -U {{ service.config.database_user }} -d {{ service.config.database_name }}
HealthOnFailure=kill

[Service]
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

- [ ] **Step 8: nginx.conf**

`config/nginx.conf` (from `docker/services/frontend-proxy/nginx.conf`, upstreams moved to the pod's loopback, the forwarded scheme passed through):

```nginx
# The one listener of the pod. The platform routes renaissance.klees.io to this port and
# knows no path rules, so the split the old compose deployment did in Traefik labels
# happens here: /api/ to the Flask backend, everything else to SvelteKit. Both share the
# pod's network namespace, hence loopback.
events {}

http {
    map $http_upgrade $connection_upgrade {
        default upgrade;
        ''      close;
    }

    upstream renaissance_backend {
        server 127.0.0.1:5000;
    }

    upstream renaissance_frontend {
        server 127.0.0.1:3000;
    }

    server {
        listen 80;
        server_name _;

        location /api/ {
            proxy_pass http://renaissance_backend;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            proxy_set_header Host $host;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            # Traefik's, not nginx's own $scheme: inside the pod that is always http.
            proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
        }

        location / {
            proxy_pass http://renaissance_frontend;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            proxy_set_header Host $host;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
        }
    }
}
```

- [ ] **Step 9: .sops.yaml at the repository root**

```yaml
# Who can open deploy/storagebaby/secrets.sops.yaml: the operator, and the StorageBaby
# host that deploys this repository (both keys from StorageBaby's own .sops.yaml).
keys:
  - &jlk age16eygwufge3085w0xqqw2pmj0w7h93465s6u0e3sg7ueaxlpxhcnqyfmgd5
  - &storagebaby age142s7lfpzjdddrrxkee8gr057u9hqkanu220n4f6hvzmr9n66x4rs9w3zvs
creation_rules:
  - path_regex: deploy/storagebaby/secrets\.sops\.yaml$
    key_groups:
      - age: [*jlk, *storagebaby]
```

- [ ] **Step 10: The secrets file, without printing a value**

```bash
S=/tmp/claude-1000/-home-jlk-Projects-Renaissance/06226db6-529d-46d0-85e0-975f026a90a2/scratchpad
umask 077
printf 'database_password: %s\nsecret_key: %s\nadmin_password: %s\n' \
	"$(openssl rand -hex 24)" "$(openssl rand -hex 64)" "$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)" > "$S/renaissance.plain.yaml"
sops --encrypt --input-type yaml --output-type yaml --filename-override deploy/storagebaby/secrets.sops.yaml "$S/renaissance.plain.yaml" > deploy/storagebaby/secrets.sops.yaml
shred -u "$S/renaissance.plain.yaml"
grep -E '^[a-z_]+:' deploy/storagebaby/secrets.sops.yaml       # the three key names, values ENC[...]
grep -c 'recipient: age1' deploy/storagebaby/secrets.sops.yaml # 2
```

- [ ] **Step 11: README.md**

`deploy/storagebaby/README.md`:

```markdown
# Renaissance on StorageBaby

One pod behind `renaissance.klees.io`: nginx on the pod's port 80 forwards `/api/` to the
Flask backend (gunicorn, 5000) and everything else to the SvelteKit frontend (node, 3000);
Postgres 13 beside them on the pod's loopback. Deployed by the StorageBaby platform from
this folder through the pointer `hosts/StorageBaby/services/renaissance/` there; the
contract is `service.yml`, the reference is StorageBaby's `ansible/roles/service/README.md`.

Images are the floating `:latest` of this repository's two packages, which CI publishes
from `stable` only, and the pod's auto-update timer picks them up. The backend container
runs `renaissance database init` and `alembic upgrade head` on every start, so an image
swapped in by the timer migrates the database without a converge.

The admin account is `email@janlucaklees.de` / `ADM`; its password is `admin_password` in
`secrets.sops.yaml` (`sops deploy/storagebaby/secrets.sops.yaml` to read it).

Deliberately not here: mail (no `MAIL_*`, so password-reset mails fail), the legacy
Flask-rendered UI (unreachable behind the split), and a backup block -- this is a prototype
for testing.
```

- [ ] **Step 12: deploy/README.md pointer**

Append:

```markdown
## StorageBaby

`deploy/storagebaby/` is the same application as a StorageBaby service: a pod definition,
not a compose file. See its README.
```

- [ ] **Step 13: Render check of the templates locally**

The StorageBaby static suite does not see external repositories, so render the five
templates once with the role's variables to catch a Jinja error before the host does:

```bash
cd /home/jlk/Projects/StorageBaby/.claude/worktrees/external-services
docker run --rm -t -v "$PWD:/repo" -v /home/jlk/Projects/Renaissance/.claude/worktrees/storagebaby-deploy/deploy/storagebaby:/ext:ro -w /repo storagebaby-devtools python - << 'PY'
import yaml, jinja2, pathlib
spec = yaml.safe_load(open('/ext/service.yml'))
env = jinja2.Environment(loader=jinja2.FileSystemLoader('/ext/quadlet'), keep_trailing_newline=True)
ctx = dict(service=spec, tz='Europe/Berlin', fqdn='renaissance.klees.io', config_dir='/etc/storagebaby/renaissance', volumes={'database': '/var/lib/storagebaby/fast/renaissance/database'})
for p in sorted(pathlib.Path('/ext/quadlet').glob('*.j2')):
    out = env.get_template(p.name).render(**ctx)
    assert 'ContainerName=' in out or p.name.endswith('.pod.j2'), p.name
    print('rendered', p.name, len(out.splitlines()), 'lines')
PY
```

Expected: five lines `rendered ...`, no exception.

- [ ] **Step 14: Commit (hook runs check:static; `mise run deps` first on a fresh worktree)**

```bash
cd /home/jlk/Projects/Renaissance/.claude/worktrees/storagebaby-deploy
mise run deps && mise run check:build
git add .sops.yaml deploy/storagebaby deploy/README.md
git commit -m "deploy: added the StorageBaby service contract"
```

---

### Task 7: Renaissance CI skips static and e2e for config-only pushes

**Files (Renaissance worktree):**

- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/docker-image.yml`

- [ ] **Step 1: The decision job and the gates** -- same shape as Task 3 with `code` as the output and `deploy/`, `docs/`, `*.md` as the skip list:

```yaml
# Which jobs a push needs. deploy/ is read by the StorageBaby host, docs/ and *.md by
# people; nothing the static checks or the e2e suite run reads them. So a push that
# touches only those skips both and still promotes `stable` (the host fetches the
# deploy folder from `stable`). The decision is a plain diff, not a workflow path
# filter: a second workflow with the complementary `paths` would also run on a mixed
# push and promote `stable` past an untested commit. Anything that is not a clean diff
# -- the first push of a branch, a `before` the checkout does not hold -- counts as
# "everything changed".
changes:
  runs-on: ubuntu-latest
  outputs:
    code: ${{ steps.detect.outputs.code }}
  steps:
    - uses: actions/checkout@v7
      with:
        fetch-depth: 0
    - id: detect
      run: |
        set -euo pipefail
        code=true
        before='${{ github.event.before }}'
        if [ -n "$before" ] && [ "$before" != 0000000000000000000000000000000000000000 ] \
           && git cat-file -e "$before^{commit}" 2>/dev/null; then
          changed=$(git diff --name-only "$before" "${{ github.sha }}")
          printf 'changed:\n%s\n' "$changed"
          skippable='^(deploy/|docs/)|\.md$'
          if [ -n "$changed" ] && ! printf '%s\n' "$changed" | grep -Ev "$skippable" | grep -q .; then
            code=false
          fi
        fi
        echo "code=$code" | tee -a "$GITHUB_OUTPUT"
```

`static`: `needs: changes`, `if: needs.changes.outputs.code == 'true'`.
`e2e`: `needs: [changes, static]`, `if: needs.changes.outputs.code == 'true'`.
`promote`:

```yaml
# Spelled out in full, because a job whose `needs` was skipped is itself skipped
# unless its `if` says otherwise -- and the one thing this job must never do is move
# `stable` onto a red or cancelled commit. Two ways in: both suites green, or the
# `changes` job said no code changed and both really were skipped. A failed `static`
# skips `e2e`, and that pair matches neither branch.
needs: [changes, static, e2e]
if: >-
  !cancelled()
  && github.ref == 'refs/heads/master' && github.event_name == 'push'
  && (
    (needs.static.result == 'success' && needs.e2e.result == 'success')
    || (needs.changes.outputs.code == 'false'
        && needs.static.result == 'skipped'
        && needs.e2e.result == 'skipped')
  )
```

- [ ] **Step 2: docker-image.yml**

```yaml
on:
  push:
    branches: ['stable']
    # A promotion that carried only deployment config or docs rebuilds nothing: the
    # images are built from what the suite ran on, and none of these paths is in them.
    paths-ignore: ['deploy/**', 'docs/**', '**.md']
  workflow_dispatch:
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml .github/workflows/docker-image.yml
git commit -m "ci: skipped the suites for a push that changes only deploy config or docs"
```

---

### Task 8: Merge StorageBaby, watch CI, watch the host swap its deploy script

- [ ] **Step 1: Merge and push**

```bash
cd /home/jlk/Projects/StorageBaby
git status --short # only untracked files of JLK's (CODE_QUALITY.md, SECURITY_REPORT.md, TODOS.md)
git merge --no-ff external-services -m "Merge branch 'external-services': services deployed from their own repositories"
git push origin master
```

- [ ] **Step 2: Watch CI**

`gh run watch` on the new run; `integration` must be green (changes decides `true`: tests and ansible changed). On a 90-minute timeout: `gh run rerun <id> --failed` once, and note it.

- [ ] **Step 3: Watch the host**

After `promote` moved `stable`:

```bash
ssh root@storagebaby.home.klees.io 'journalctl -u storagebaby-deploy.service -f -n 5'
```

Expect, over two ticks (the timer fires every five minutes): the old ansible-pull tick converges the new `master` (host_base replaces the unit and installs the script); the next tick logs `platform repository: git@github.com:... (stable)`, finds no state file, runs the playbook with `changed=0` for every service, writes `/var/lib/storagebaby/deploy.state`. Then `nothing changed since the last converge` on the tick after.

Check: `cat /var/lib/storagebaby/deploy.state` (`repo <sha>`), `systemctl cat storagebaby-deploy.service | grep ExecStart` (the script), every service still `storagebaby-svc ps <name>` healthy.

---

### Task 9: Renaissance merge, CI, images

- [ ] **Step 1: Merge and push**

```bash
cd /home/jlk/Projects/Renaissance/.claude/worktrees/storagebaby-deploy
git checkout master && git merge --no-ff storagebaby-deploy -m "Merge branch 'storagebaby-deploy'" && git push origin master
```

(Or fast-forward: both are fine; `--no-ff` keeps the two commits grouped.) The main checkout's `master` moves with it (shared repository); JLK's working tree is untouched.

- [ ] **Step 2: Watch CI**

`changes` must say `code=true` (workflows changed). `static`, `e2e`, `promote`, then `docker-image` on `stable` publishes both `:latest` images. A red `e2e`: download the `playwright-report` artifact, fix the spec in the worktree, commit, push, repeat; record for the report.

---

### Task 10: The pointer

**Files (StorageBaby, on `master` now, in the main checkout or a fresh worktree from master):**

- Create: `hosts/StorageBaby/services/renaissance/source.yml`
- Modify: `hosts/StorageBaby/host.yml` (`registries: [ghcr.io]`)
- Commit: `hosts/StorageBaby/secrets/sources.sops.yaml`, `hosts/StorageBaby/secrets/registries.sops.yaml` (already encrypted in the external-services worktree; move them)

- [ ] **Step 1: Files**

`hosts/StorageBaby/services/renaissance/source.yml`:

```yaml
# Renaissance lives in its own repository; this folder only says where. The contract is
# deploy/storagebaby/ there, and `stable` is the branch its CI promotes once the full
# suite passed, so the host never fetches a contract that was not tested with its code.
repo: https://github.com/janlucaklees/renaissance
ref: stable
path: deploy/storagebaby
```

`host.yml`, after `cert_zones`:

```yaml
# The registries this host holds a login for (secrets/registries.sops.yaml). A service
# names the ones it needs; one it names that is not here is refused at converge.
registries: [ghcr.io]
```

- [ ] **Step 2: Static suite, commit, push**

```bash
mise run test-static
git add hosts/StorageBaby/services/renaissance/source.yml hosts/StorageBaby/host.yml hosts/StorageBaby/secrets/sources.sops.yaml hosts/StorageBaby/secrets/registries.sops.yaml
git commit -m "Place Renaissance on StorageBaby from its own repository"
git push origin master
```

CI: `changes` says `integration=false`, `static` green, `integration` skipped, `promote` moves `stable`.

- [ ] **Step 3: Watch the tick**

```
journalctl -u storagebaby-deploy.service -f
```

Expect `external repository for renaissance: https://github.com/janlucaklees/renaissance (stable, deploy/storagebaby)`, then the converge creating `svc-renaissance`, pulling four images, writing the units, the route file `renaissance-renaissance.klees.io.yml`, starting the pod.

- [ ] **Step 4: Verify**

```bash
ssh root@storagebaby.home.klees.io 'storagebaby-svc ps renaissance; cat /var/lib/storagebaby/deploy.state'
curl -sI https://renaissance.klees.io/login | head -3                                        # HTTP/2 200
curl -s https://renaissance.klees.io/api/v1/home/navigation -o /dev/null -w '%{http_code}\n' # 401 or 200, not 502
```

A browser login is JLK's in the morning; the admin password is in the Renaissance secrets file.

---

### Task 11: Amendments and the handover

- [ ] **Step 1:** Append to `docs/superpowers/specs/2026-10-02-external-services-design.md` § 11 an amendment 9 with what the real run showed: whether the fine-grained token read ghcr (it did not; a classic `read:packages` token did -- fill in what actually happened), the first-tick behaviour on the live host, anything that deviated.
- [ ] **Step 2:** Replace `HANDOVER.md`'s § 2 table rows that are now done, or delete the file if JLK's untracked copy is the only one (it is untracked: leave it, note in the report).
- [ ] **Step 3:** Commit the amendment on `master`, push (docs-only: integration skipped).
- [ ] **Step 4:** The morning report: what landed (commits, both repositories), what was fixed on the way, what is open (3.5 of the spec, mail, legacy UI, backup), and how to log in.
