# External Services — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A service whose definition lives in its own repository is deployed by this platform through a pointer folder, with the host fetching that repository on every deploy tick and converging when it moved.

**Architecture:** `hosts/<host>/services/<name>/source.yml` points at a repository, a branch and a folder; the host's deploy unit runs a script that fetches this repository and every pointed one into `/var/lib/storagebaby/sources/<name>` and runs the playbook when any checkout differs from the SHAs of the last successful converge. The playbook resolves a pointer to the fetched `service.yml` and from there the `service` role treats it like any in-tree service. One new `service.yml` key, `registries`, makes the role write the service user's `auth.json` from the pointer's `source.sops.yaml` so private images pull. A fixture external repository under `tests/fixtures/sources/` is served to the test VM by a throwaway basic-auth git HTTP server and pulls its image from a throwaway htpasswd registry, so the token path and the private-image path both run for real.

**Tech Stack:** As before (Ansible, rootless Podman + Quadlet, sops + age, Molecule on libvirt VMs, pytest/testinfra). New: a bash deploy script with `GIT_ASKPASS`, python `http.server` for the dumb git protocol, `docker.io/library/registry:2` with htpasswd on the VM.

**Spec:** `docs/superpowers/specs/2026-10-02-external-services-design.md`

## Global Constraints

- **Decisions in the spec §1 are locked.** External repo owns the whole service definition including `secrets.sops.yaml`; this repo holds only `source.yml` (three required keys `repo`, `ref`, `path`, no defaults) and `source.sops.yaml` (`git_token`, `registry_user`, `registry_token`); live fetch with no gate in this repo's CI; one service per external repository; branch refs only; `registries` is accepted only on a pointer service.
- **The external folder is the existing contract verbatim.** No new template variable, no translation. Everything in `ansible/roles/service/` keeps working on an in-tree service exactly as before; a pointer service only differs in where `service_dir` is.
- Rootless only, one user per service, loopback publishing only, nothing privileged, no hostname in roles/templates/tasks. The host name reaches the deploy script as `$(uname -n)` (the Arch cloud image has no `hostname` binary).
- Secrets: `no_log: true` on every task that carries a decrypted value; a token is never written to disk in the clear (the deploy script hands it to git through `GIT_ASKPASS` and the process environment only); `*.sops.yaml` copied to the host are `0600 root`.
- Commands via mise only — `mise run test-static`, `mise run test-integration`, `MOLECULE_HOST=test-ci mise run test-integration`, `mise run molecule <cmd>`, `mise run molecule-exec '<cmd>'`, `mise run format`, `mise run fmt-check`. Never docker/virsh/molecule/pytest/pacman directly on the workstation.
- **One Molecule run at a time on this machine, coordinated with the other sessions.** Before `mise run test-integration` or `mise run molecule ...`, run `pgrep -af molecule`; if anything is running, or if JLK has not said this session may use the VM now, stop and ask. Static tests run freely.
- Commit per task on `external-services` (standing permission; never push). One concern per commit. Every commit message ends with:

  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  ```

- Static tests enumerate the filesystem (`Path.glob`, `rglob`), never `git ls-files`: this is a worktree, and git inside the devtools container cannot resolve its `.git` pointer.
- Ansible idioms as in the existing roles: `check_mode: false` on read-only probes whose result a later task reads; `| bool` on any conditional fed from `-e`; `find` with `follow: true` for service folders; the per-host `service_config` merge stays in the playbook.
- `ansible-lint --offline ansible/` stays clean (`tests/static/test_lint.py`) and `mise run fmt-check` stays green. The pre-commit hook formats staged files; a bash file in `ansible/roles/host_base/files/` is formatted by prettier-plugin-sh (tabs), so run `mise run format` before committing anything shell.
- Heredoc terminators inside the deploy script must not be `EOF`: `bootstrap.sh` embeds the whole script in a heredoc of its own.

---

### Task 1: The deploy script replaces `ansible-pull`

Spec §3. No pointer exists yet; this task only changes _how_ a host decides to converge and must leave every existing deploy test green.

**Files:**

- Create: `ansible/roles/host_base/files/storagebaby-deploy`
- Modify: `ansible/roles/host_base/files/storagebaby-deploy.service`
- Modify: `ansible/roles/host_base/tasks/main.yml` (install the script beside `storagebaby-svc`)
- Modify: `bootstrap.sh` (heredoc of the script, unit text)
- Modify: `tests/static/test_bootstrap.py`
- Modify: `tests/integration/molecule/test-ci/tests/test_host_base.py`
- Modify: `tests/integration/molecule/test-ci/tests/test_deploy.py` (docstrings that explain the mechanism; one new case)
- Modify: `README.md` lines around 415 and 477 (the two paragraphs that describe `ansible-pull --only-if-changed`); `CLAUDE.md` line 190

**Interfaces:**

- Produces: `/usr/local/sbin/storagebaby-deploy`, run by `storagebaby-deploy.service`. Reads `/etc/storagebaby/deploy.conf` (`REPO_URL`, `BRANCH`, `EXTRA_ARGS`), keeps `/var/lib/storagebaby/repo`, creates `/var/lib/storagebaby/sources/` (0700) and `/var/lib/storagebaby/deploy.state`. State file format: one line per watched checkout, `repo <sha>` first, then `source:<name> <sha>` sorted by name. Exit 0 and the log line `nothing changed since the last converge; not running the playbook` when the state matches.
- Produces: the shell function `sync_checkout <dir> <url> <ref>` (prints HEAD) and the pointer loop that Task 2's fixture will exercise; the pointer format it reads is `source.yml` with `repo`, `ref`, `path`, and the optional `source.sops.yaml` key `git_token`.

- [ ] **Step 1: Write the failing static test**

`tests/static/test_bootstrap.py`, replace `test_bootstrap_embeds_the_same_units_host_base_installs` with:

```python
def test_bootstrap_embeds_the_same_units_and_deploy_script_host_base_installs():
    """bootstrap.sh and host_base install one deploy unit, one timer and one script.

    The script is embedded and not fetched: the first deploy runs before `host_base` has
    ever converged, and bootstrap cannot clone the repository -- the operator registers the
    deploy key only after bootstrap prints it. So the heredoc is the second copy of the
    file, and this is what keeps it the same copy.
    """
    script = (REPO / "bootstrap.sh").read_text()
    for name in ["storagebaby-deploy.service", "storagebaby-deploy.timer", "storagebaby-deploy"]:
        body = (UNIT_DIR / name).read_text().strip()
        assert body in script, f"{name} in bootstrap.sh drifted from host_base/files/{name}"


def test_deploy_unit_runs_the_script_and_not_ansible_pull():
    unit = (UNIT_DIR / "storagebaby-deploy.service").read_text()
    assert "ExecStart=/usr/local/sbin/storagebaby-deploy\n" in unit, unit
    assert "ansible-pull" not in unit, "the unit must run the script; ansible-pull watches one repository only"


def test_deploy_script_is_valid_bash():
    subprocess.run(["bash", "-n", str(UNIT_DIR / "storagebaby-deploy")], check=True)
```

- [ ] **Step 2: Run it to see it fail**

Run: `mise run test-static -- -k bootstrap` (if mise does not forward arguments, run `mise run test-static` and read the bootstrap failures).
Expected: FAIL — `storagebaby-deploy` is not in `host_base/files`.

- [ ] **Step 3: Write the script**

`ansible/roles/host_base/files/storagebaby-deploy`:

```bash
#!/bin/bash
# One deploy tick. Run by storagebaby-deploy.service as root.
#
# Installed twice from one text: by the host_base role from this file, and by bootstrap.sh
# from a heredoc, because the first tick of a fresh host happens before host_base has ever
# run. tests/static/test_bootstrap.py holds the two byte-identical.
#
# What it does: fetch the platform repository, then every external repository this host
# points at (hosts/<host>/services/<name>/source.yml and hosts/shared/services/*/source.yml),
# and run the playbook only when any of those checkouts differs from what the last
# *successful* converge saw -- which is also what makes a failed converge retry on the next
# tick instead of waiting for the next commit, as `ansible-pull --only-if-changed` did.
set -euo pipefail

# REPO_URL, BRANCH, EXTRA_ARGS, written by bootstrap.sh.
. /etc/storagebaby/deploy.conf

REPO_DIR=/var/lib/storagebaby/repo
SOURCES_DIR=/var/lib/storagebaby/sources
STATE_FILE=/var/lib/storagebaby/deploy.state
HOST=$(uname -n)

export SOPS_AGE_KEY_FILE=/etc/storagebaby/age.key
# A private repository without a usable token fails here and now, instead of hanging on a
# prompt no one will answer.
export GIT_TERMINAL_PROMPT=0
# Whatever credential helper the host's git might have configured is not consulted: the
# token for an external repository comes from its pointer and nowhere else.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0=

# The token reaches git through GIT_ASKPASS and the environment, never through the remote
# URL and never through .git/config -- so it is nowhere on disk in the clear. The askpass
# helper itself is a few lines on tmpfs, removed when this run ends.
ASKPASS=$(mktemp /run/storagebaby-deploy.XXXXXX)
trap 'rm -f "$ASKPASS"' EXIT
cat > "$ASKPASS" << 'ASKPASS_SCRIPT'
#!/bin/sh
case "$1" in
	Username*) printf '%s\n' x-access-token ;;
	*) printf '%s\n' "${STORAGEBABY_GIT_TOKEN:-}" ;;
esac
ASKPASS_SCRIPT
chmod 0700 "$ASKPASS"
export GIT_ASKPASS=$ASKPASS

# sync_checkout <dir> <url> <ref>: clone or fetch, check out origin/<ref> detached, print
# the resulting HEAD. Always a hard reset and a clean: a host never has local changes in
# these checkouts, and a force-push upstream must not wedge the tick.
sync_checkout() {
	local dir=$1 url=$2 ref=$3
	if [ ! -d "$dir/.git" ]; then
		git clone --quiet --no-checkout "$url" "$dir"
	fi
	git -C "$dir" remote set-url origin "$url"
	git -C "$dir" fetch --quiet --prune origin "+refs/heads/$ref:refs/remotes/origin/$ref"
	git -C "$dir" checkout --quiet --detach "origin/$ref"
	git -C "$dir" reset --quiet --hard
	git -C "$dir" clean --quiet -fdx
	git -C "$dir" rev-parse HEAD
}

# One line per pointer of this host: "<name> <repo> <ref> <path> <pointer folder>".
list_pointers() {
	python3 - "$REPO_DIR" "$HOST" << 'PY'
import glob
import os
import sys

import yaml

repo, host = sys.argv[1], sys.argv[2]
pointers = sorted(
    glob.glob(f"{repo}/hosts/shared/services/*/source.yml")
    + glob.glob(f"{repo}/hosts/{host}/services/*/source.yml")
)
for pointer in pointers:
    with open(pointer) as fh:
        source = yaml.safe_load(fh)
    folder = os.path.dirname(pointer)
    print(os.path.basename(folder), source["repo"], source["ref"], source["path"], folder)
PY
}

# The git_token of a pointer, or nothing: no source.sops.yaml, or one without that key,
# means an anonymous clone (a public repository, or a private image on a public one).
git_token_of() {
	local sops_file=$1/source.sops.yaml
	[ -f "$sops_file" ] || return 0
	sops --decrypt --output-type json "$sops_file" \
		| python3 -c 'import json, sys; print(json.load(sys.stdin).get("git_token", ""))'
}

install -d -m 0755 /var/lib/storagebaby
install -d -m 0700 "$SOURCES_DIR"

echo "platform repository: $REPO_URL ($BRANCH)"
repo_sha=$(sync_checkout "$REPO_DIR" "$REPO_URL" "$BRANCH")
if [ ! -d "$REPO_DIR/hosts/$HOST" ]; then
	echo "the repository has no hosts/$HOST folder; nothing to converge" >&2
	exit 1
fi
desired="repo $repo_sha"

names=()
# Captured first and not read through a process substitution: under `set -e` a python
# failure inside `<(...)` would end the loop quietly with half the pointers, and the
# state file would then say "converged" about repositories that were never fetched.
pointer_lines=$(list_pointers)
while read -r name url ref path folder; do
	[ -n "$name" ] || continue
	names+=("$name")
	echo "external repository for $name: $url ($ref, $path)"
	STORAGEBABY_GIT_TOKEN=$(git_token_of "$folder")
	export STORAGEBABY_GIT_TOKEN
	sha=$(sync_checkout "$SOURCES_DIR/$name" "$url" "$ref")
	unset STORAGEBABY_GIT_TOKEN
	if [ ! -f "$SOURCES_DIR/$name/$path/service.yml" ]; then
		echo "$name: $url ($ref) has no $path/service.yml" >&2
		exit 1
	fi
	desired+=$'\n'"source:$name $sha"
done <<< "$pointer_lines"

# A checkout nothing points at any more goes, so the sources directory mirrors the placement.
shopt -s nullglob
for existing in "$SOURCES_DIR"/*/; do
	existing=$(basename "$existing")
	keep=0
	for name in "${names[@]}"; do
		[ "$name" = "$existing" ] && keep=1
	done
	if [ "$keep" -eq 0 ] && [[ "$existing" =~ ^[a-z0-9-]+$ ]]; then
		echo "removing the fetched repository of $existing: no pointer names it any more"
		rm -rf "${SOURCES_DIR:?}/$existing"
	fi
done

if [ -f "$STATE_FILE" ] && [ "$(cat "$STATE_FILE")" = "$desired" ]; then
	echo "nothing changed since the last converge; not running the playbook"
	exit 0
fi

cd "$REPO_DIR"
# shellcheck disable=SC2086
/usr/bin/ansible-playbook --inventory ansible/inventory/hosts.yml --limit "$HOST" $EXTRA_ARGS ansible/playbook.yml
printf '%s\n' "$desired" > "$STATE_FILE"
```

`ansible/roles/host_base/files/storagebaby-deploy.service`:

```ini
[Unit]
Description=StorageBaby deploy (fetch the stable branch and every pointed repository, converge on change)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/storagebaby/deploy.conf
Environment="GIT_SSH_COMMAND=ssh -i /etc/storagebaby/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
ExecStart=/usr/local/sbin/storagebaby-deploy
# Without this, a host whose hostname has no hosts/<name> folder converges nothing
# at all -- --limit matches no host and the playbook still exits 0. Fail visibly.
ExecStartPost=/usr/bin/test -d /var/lib/storagebaby/repo/hosts/%H
```

`ansible/roles/host_base/tasks/main.yml`: after the "Install the service control helper" task add

```yaml
# The deploy tick itself. One text, installed from here on every converge and from a
# heredoc in bootstrap.sh for the first tick of a fresh host; `test_bootstrap.py` keeps the
# two identical. It is a program and not the unit's ExecStart because it watches more than
# one repository: the platform's, and every external repository a pointer under
# hosts/<host>/services/ names.
- name: Install the deploy script
  ansible.builtin.copy:
    src: storagebaby-deploy
    dest: /usr/local/sbin/storagebaby-deploy
    owner: root
    group: root
    mode: '0755'
```

`bootstrap.sh`: before the `cat > /etc/systemd/system/storagebaby-deploy.service` block insert

```bash
# The deploy tick, identical to ansible/roles/host_base/files/storagebaby-deploy (a static
# test holds them so). Embedded because the first tick runs before host_base ever has.
cat > /usr/local/sbin/storagebaby-deploy << 'DEPLOY_SCRIPT'
DEPLOY_SCRIPT
chmod 0755 /usr/local/sbin/storagebaby-deploy
```

Between those two `DEPLOY_SCRIPT` lines goes the complete text of `ansible/roles/host_base/files/storagebaby-deploy` from this step, byte for byte, from its `#!/bin/bash` line to its last `printf` line. No line of the script may be `DEPLOY_SCRIPT`, and the script's own heredocs use `ASKPASS_SCRIPT` and `PY` for that reason.

and replace the unit heredoc with the new unit text above. Keep the timer heredoc.

- [ ] **Step 4: Run the static tests**

Run: `mise run format && mise run test-static`
Expected: PASS (bootstrap tests, lint, fmt).

- [ ] **Step 5: Integration assertions on the installed script**

`tests/integration/molecule/test-ci/tests/test_host_base.py`, after `test_deploy_units_installed_but_timer_off_for_test_host`:

```python
DEPLOY_SCRIPT = "/usr/local/sbin/storagebaby-deploy"


def test_deploy_script_installed_and_named_by_the_unit(host):
    """The tick is a program the role installs, and the unit runs that program.

    `ansible-pull` could watch exactly one repository; the script also fetches every
    external repository a pointer under hosts/<host>/services/ names, and converges when
    any of them moved.
    """
    f = host.file(DEPLOY_SCRIPT)
    assert f.exists and f.mode == 0o755 and f.user == "root"
    unit = host.file("/etc/systemd/system/storagebaby-deploy.service").content_string
    assert f"ExecStart={DEPLOY_SCRIPT}\n" in unit, unit
```

`tests/integration/molecule/test-ci/tests/test_deploy.py`: update the module docstring (first paragraph) to

```python
"""The production deploy path: `storagebaby-deploy.service` fetching the stable branch.

The unit is a oneshot that runs `/usr/local/sbin/storagebaby-deploy` against the bare repo
seeded in prepare: it fetches the checkout, compares it (and every pointed external
repository) with the SHAs of the last successful converge in /var/lib/storagebaby/deploy.state,
and runs the playbook when anything differs. `systemctl start` blocks until the whole
convergence is done -- a minute or two on the first run, which clones the repo first.
`timeout` keeps a wedged pull from hanging the verifier.
```

Rename `test_deploy_service_is_a_noop_when_nothing_changed` to `test_first_deploy_converges_the_checkout_and_changes_nothing` and set its docstring to: _"The first tick finds no state file, clones the checkout and runs the playbook; the checkout renders byte for byte what the converge already put on the host, so nothing is reported changed and no unit is restarted."_ Then add directly after it:

```python
STATE = "/var/lib/storagebaby/deploy.state"
NOOP_LINE = "nothing changed since the last converge; not running the playbook"


@pytest.mark.order(-1)
def test_a_tick_with_nothing_changed_does_not_run_the_playbook(host):
    """The second tick: the state file matches every watched checkout, so no playbook runs.

    Checked on the journal line the script writes, not on timing: a playbook that runs and
    changes nothing looks the same from the units' side, and this is the claim that the
    tick is cheap when the world did not move.
    """
    state = host.file(STATE)
    assert state.exists, "the first deploy wrote no state file"
    repo_sha = host.check_output("git -C /var/lib/storagebaby/repo rev-parse HEAD")
    assert f"repo {repo_sha}" in state.content_string.splitlines(), state.content_string
    marker = host.check_output("date +%s")
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout
    journal = host.run(f"journalctl -u storagebaby-deploy.service --since @{marker} --no-pager").stdout
    assert NOOP_LINE in journal, journal
    assert "PLAY RECAP" not in journal, f"the playbook ran on an unchanged state:\n{journal}"
```

In the two docstrings of `test_deploy_restores_a_drifted_host_gateway_dropin` and `test_deploy_refuses_to_converge_with_a_declared_device_missing` replace the sentence about `ansible-pull --only-if-changed` with: _"The deploy script runs the playbook only when a watched checkout's SHA differs from the state file, so the deploy needs a new sha, and an empty commit leaves the tree identical."_

- [ ] **Step 6: Docs that describe the mechanism**

`README.md`, the paragraph beginning "The checkout the deploy unit converges from does not exist yet — `ansible-pull` clones it": replace `ansible-pull` with `the deploy script`. The paragraph beginning "**If the converge aborts, re-run that same command.**" becomes:

> **If the converge aborts, re-run that same command — or start the deploy unit.** `storagebaby-deploy` writes `/var/lib/storagebaby/deploy.state` only after a converge that ended `failed=0`, so a tick after a failed run finds the state stale and runs the playbook again. (`ansible-pull --only-if-changed`, which the unit used to run, compared the checkout before and after its own fetch and would have sat idle until the next push.)

`CLAUDE.md` line 190 becomes:

> From then on the host deploys itself: the timer runs `/usr/local/sbin/storagebaby-deploy` as root every 5 minutes (2 min after boot). It fetches the `stable` branch into `/var/lib/storagebaby/repo` and every external repository this host points at into `/var/lib/storagebaby/sources/<name>`, and runs `ansible/playbook.yml --limit <hostname>` when any of those checkouts differs from `/var/lib/storagebaby/deploy.state`, the SHAs of the last converge that succeeded. A failed converge is therefore retried on the next tick.

- [ ] **Step 7: Integration run (coordinate first)**

Run: `pgrep -af molecule` — only when nothing runs and JLK has cleared the VM for this session: `MOLECULE_HOST=test-ci mise run test-integration`.
Expected: every existing deploy case green, the two new ones green. If the VM is not available, say so in the task report and leave the run for the whole-branch verification.

- [ ] **Step 8: Commit**

```bash
git add ansible/roles/host_base bootstrap.sh tests/static/test_bootstrap.py tests/integration/molecule/test-ci/tests/test_host_base.py tests/integration/molecule/test-ci/tests/test_deploy.py README.md CLAUDE.md
git commit -m "Replace ansible-pull with a deploy script that watches more than one repository

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Pointer services — playbook resolution, static contract, the fixture and its git server

Spec §2, §4 (all but `registries`), §6 fixture, prepare's git server. At the end of this task `ext-echo` is deployed on both test hosts from a repository the VM fetches with a token.

**Files:**

- Modify: `ansible/playbook.yml` (pre_tasks: `sources_root`, pointer discovery and resolution, missing-source fail/skip, name assert, loopback-port assert; `pointer_dir` passed to the role)
- Modify: `tests/static/conftest.py` (`Placement.pointer`, pointer resolution through the fixture tree, `pointers()`, `FIXTURE_SOURCES`, `rendered` passes `sources_root`)
- Create: `tests/static/test_sources.py`
- Modify: `tests/static/test_contract.py` (`name == p.name`; `registries` key accepted, pointer-only), `tests/static/test_secrets.py` (exclusions and the fixture skip)
- Create: `tests/fixtures/sources/ext-echo/README.md`, `tests/fixtures/sources/ext-echo/deploy/storagebaby/{service.yml,README.md}`, `.../quadlet/ext-echo.container.j2`, `.../config/www/index.html`
- Create: `hosts/test-a/services/ext-echo/source.yml`, `hosts/test-ci/services/ext-echo/source.yml`
- Modify: `.gitignore`
- Create: `tests/integration/molecule/test-ci/files/test-git-http.py`
- Modify: `tests/integration/molecule/test-ci/prepare.yml` (fixture secrets, pointer credentials, git server, external seed, archive of the pointer's generated file)
- Modify: `tests/integration/molecule/test-ci/molecule.yml` (`sources_root` group var)
- Modify: `tests/integration/molecule/test-ci/tests/test_service.py` (`service_name()`, `_specs()` resolves pointers) and the seven other `parent.name` sites listed in Step 7
- Create: `tests/integration/molecule/test-ci/tests/test_external.py`

**Interfaces:**

- Consumes: Task 1's script (`/var/lib/storagebaby/sources/<name>` layout, `source.sops.yaml` key `git_token`).
- Produces: playbook var `sources_root` (default `/var/lib/storagebaby/sources`; Molecule `/repo/tests/fixtures/sources`; static `<REPO>/tests/fixtures/sources`), include_role var `pointer_dir` (`''` for an in-tree service, the pointer folder's path otherwise — Task 3 reads `source.sops.yaml` from it), static helpers `conftest.FIXTURE_SOURCES`, `conftest.pointers() -> list[tuple[host, name, folder: Path, source: dict]]`, `Placement.pointer: Path | None`; integration helper `test_service.service_name(spec_path) -> str`; on the VM: git server `http://127.0.0.1:8418/<name>.git` with tokens in `/etc/test-git-http/credentials`, external working copy `/srv/ext-src/<name>`, bare remote `/srv/git/<name>.git`.

- [ ] **Step 1: Static tests first — the pointer contract**

`tests/static/conftest.py`: replace `Placement` and `_services_in`, add helpers:

```python
# The external repositories the static suite can see: one folder per pointer name holding
# that repository's working tree, exactly as the host fetches it. On a host the same tree
# is /var/lib/storagebaby/sources/<name>; for Molecule it is this folder under /repo.
FIXTURE_SOURCES = REPO / "tests" / "fixtures" / "sources"


@dataclass(frozen=True)
class Placement:
    host: str
    name: str
    dir: Path
    spec: dict
    # The pointer folder under hosts/ for an external service, None for an in-tree one.
    pointer: Path | None = None


def external_source_dir(name: str, source: dict) -> Path | None:
    """Where a pointer's contract folder is in the fixture tree, or None when absent.

    A pointer to a private repository on a real host has no local tree and yields no
    placement: everything about such a service is checked on the host at converge time.
    """
    folder = FIXTURE_SOURCES / name / source["path"]
    return folder if (folder / "service.yml").exists() else None


def _services_in(dir_: Path) -> list[tuple[str, Path, dict, Path | None]]:
    out = []
    for folder in sorted(p for p in dir_.iterdir() if p.is_dir()):
        if (folder / "service.yml").exists():
            out.append((folder.name, folder.resolve(), load_yaml(folder / "service.yml"), None))
        elif (folder / "source.yml").exists():
            resolved = external_source_dir(folder.name, load_yaml(folder / "source.yml"))
            if resolved is not None:
                out.append((folder.name, resolved, load_yaml(resolved / "service.yml"), folder))
    return out


def placements() -> list[Placement]:
    shared = _services_in(HOSTS / "shared" / "services")
    result = []
    for host in host_names():
        for name, dir_, spec, pointer in shared + _services_in(HOSTS / host / "services"):
            result.append(Placement(host, name, dir_, spec, pointer))
    return result


def pointers() -> list[tuple[str, str, Path, dict]]:
    """(owner, name, pointer folder, source.yml) for every pointer; owner is a host or 'shared'."""
    out = []
    for owner_dir in [HOSTS / "shared"] + [HOSTS / h for h in host_names()]:
        for pointer in sorted((owner_dir / "services").glob("*/source.yml")):
            out.append((owner_dir.name, pointer.parent.name, pointer.parent, load_yaml(pointer)))
    return out
```

In the `rendered` fixture add two arguments to the `ansible-playbook` call, after the `tcp_bind_address` pair:

```python
                "-e",
                f"sources_root={FIXTURE_SOURCES}",
```

`tests/static/test_sources.py`, new:

```python
"""Pointer folders: a service whose definition lives in its own repository.

`hosts/<host>/services/<name>/source.yml` names a repository, a branch and a folder in
it; the host fetches that repository on every deploy tick and the folder is the service
contract. Nothing of the service is in this repository, and nothing about the *contract*
can be checked here for a private repository -- the fixture under tests/fixtures/sources
is what runs the external path through every other static test, and the host asserts the
rest at converge time.
"""

import re

import pytest

from conftest import FIXTURE_SOURCES, HOSTS, load_yaml, placements, pointers

SOURCE_KEYS = {"repo", "ref", "path"}
# Plaintext key names in a sops file, like `test_secrets.py` relies on.
SOURCE_SECRET_KEYS = {"git_token", "registry_user", "registry_token"}
# What a pointer folder may hold. The generated test credential is gitignored and may or
# may not be present; nothing else belongs beside the pointer -- a `quadlet/` here would be
# a service half in this repository and half in its own.
POINTER_FILES = {"source.yml", "source.sops.yaml", "README.md"}
NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
# https anywhere; plain http only to the loopback, which is the throwaway git server the
# test VM runs. A token over plain http to anything else would be a token on the wire.
REPO_RE = re.compile(r"^(https://\S+|http://127\.0\.0\.1(:\d+)?/\S+)$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
PATH_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")

POINTERS = pointers()
ids = [f"{owner}/{name}" for owner, name, _, _ in POINTERS]


@pytest.mark.parametrize("owner,name,folder,source", POINTERS, ids=ids)
def test_pointer_contract(owner, name, folder, source):
    assert set(source) == SOURCE_KEYS, f"{name}: source.yml must have exactly {sorted(SOURCE_KEYS)}, has {sorted(source)}"
    assert NAME_RE.match(name), f"{name}: not a valid service name"
    assert REPO_RE.match(source["repo"]), f"{name}: repo must be https://, or http://127.0.0.1 for a test host"
    assert REF_RE.match(source["ref"]), f"{name}: ref {source['ref']!r} is not a branch name"
    assert PATH_RE.match(source["path"]), f"{name}: path {source['path']!r} must be a relative folder"
    assert not (folder / "service.yml").exists(), f"{name}: a folder is a pointer or a service, never both"
    extra = {p.name for p in folder.iterdir()} - POINTER_FILES
    assert not extra, f"{name}: a pointer folder holds only {sorted(POINTER_FILES)}, found {sorted(extra)}"
    secret = folder / "source.sops.yaml"
    if secret.exists():
        keys = set(load_yaml(secret)) - {"sops"}
        assert keys <= SOURCE_SECRET_KEYS, f"{name}: unknown keys in source.sops.yaml: {keys - SOURCE_SECRET_KEYS}"


def test_a_pointer_on_a_test_host_has_its_repository_in_the_fixture_tree():
    """What a test host points at exists under tests/fixtures/sources, so the static suite
    renders it and Molecule converges it; a pointer with no tree would be a placement no
    test ever sees."""
    for owner, name, _, source in POINTERS:
        if owner.startswith("test-"):
            folder = FIXTURE_SOURCES / name / source["path"]
            assert (folder / "service.yml").exists(), f"{owner}/{name}: no fixture at {folder}"


def test_test_host_copies_of_a_pointer_are_identical():
    """Like the other fixtures: a real folder per test host, byte-identical `source.yml`."""
    by_name: dict[str, set[bytes]] = {}
    for owner, name, folder, _ in POINTERS:
        if owner.startswith("test-"):
            by_name.setdefault(name, set()).add((folder / "source.yml").read_bytes())
    for name, texts in by_name.items():
        assert len(texts) == 1, f"{name}: the test hosts' source.yml files differ"


def test_registries_is_declared_only_by_an_external_service():
    """The credential for a registry login comes from the pointer's source.sops.yaml; an
    in-tree service has no such file, and nothing in tree pulls a private image."""
    for p in placements():
        if p.spec.get("registries"):
            assert p.pointer is not None, f"{p.host}/{p.name}: registries without a pointer"
```

`tests/static/test_contract.py`: add `"registries"` to `SPEC_KEYS`; change `assert spec["name"] == p.dir.name, "name must equal the folder name"` to `assert spec["name"] == p.name, "name must equal the folder name"`; before the `for secret, ref in spec.get("host_secrets"...` loop add

```python
    registries = spec.get("registries", [])
    assert isinstance(registries, list) and registries == sorted(set(registries)), f"{p.name}: registries is a sorted list"
    for registry in registries:
        assert REGISTRY_RE.match(registry), f"{p.name}: {registry!r} is not a registry host"
```

with, at the top, `REGISTRY_RE = re.compile(r"^(localhost|[a-z0-9-]+(\.[a-z0-9-]+)+)(:\d+)?$")`.

`tests/static/test_secrets.py`: in `sops_files()` add a second exclusion after the first:

```python
        if re.match(r"^hosts/test-[^/]+/services/[^/]+/source\.sops\.yaml$", rel):
            continue  # gitignored, test-key-encrypted pointer credentials written by the integration scenario
```

and at the top of `test_declared_secrets_exist_in_file`:

```python
    if p.pointer is not None and not (p.dir / "secrets.sops.yaml").exists():
        pytest.skip("external fixture: prepare generates its secrets file, encrypted to the VM")
```

`.gitignore`, append:

```
hosts/test-*/services/*/source.sops.yaml
tests/fixtures/sources/*/**/secrets.sops.yaml
```

- [ ] **Step 2: The fixture and its pointers**

`tests/fixtures/sources/ext-echo/README.md`:

```markdown
# ext-echo: the model external repository

This folder is a whole repository as the platform expects to find one: the application's
own code would sit beside `deploy/`, and `deploy/storagebaby/` is the service contract the
host fetches. The integration scenario pushes this tree to a throwaway git server on the
test VM (basic auth, so the pointer's `git_token` is really used) and the pointers under
`hosts/test-*/services/ext-echo/` fetch it from there. The static suite reads it in place
(`tests/static/conftest.py`, `FIXTURE_SOURCES`). `secrets.sops.yaml` is generated by
`prepare.yml`, encrypted to the VM's key, and gitignored.
```

`tests/fixtures/sources/ext-echo/deploy/storagebaby/service.yml`:

```yaml
# A test-only fixture: a service that lives in its *own* repository and is placed by a
# pointer (`hosts/test-*/services/ext-echo/source.yml`). The contract is the ordinary one;
# what the fixture measures is the path that brings it here -- the deploy script's fetch
# with a token, the playbook's resolution of a pointer, the secrets file read from the
# external tree rather than from this repository.
name: ext-echo
port: 8118
domain: ext-echo
volumes: {}
secrets: [greeting]
backup: none
```

`tests/fixtures/sources/ext-echo/deploy/storagebaby/quadlet/ext-echo.container.j2`:

```
[Unit]
Description=External-repository fixture, deployed through a pointer

[Container]
# Pinned, like every fixture: a floating tag would let the thing a test measures change
# under the test. Task 3 points this at the test VM's private registry.
Image=docker.io/library/busybox:1.37.0
ContainerName=ext-echo
# BusyBox's httpd in the foreground, serving the service's own config tree: the page it
# answers with came from the external repository's config/, through /etc/storagebaby/ext-echo/.
Exec=httpd -f -p 8118 -h /www
PublishPort=127.0.0.1:{{ service.port }}:8118
Volume={{ config_dir }}/www:/www:ro
Secret=greeting
Environment=TZ={{ tz }}
HealthCmd=wget -q --spider http://127.0.0.1:8118/ || exit 1
HealthOnFailure=kill

[Service]
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

`tests/fixtures/sources/ext-echo/deploy/storagebaby/config/www/index.html`:

```html
<!doctype html>
<title>ext-echo</title>
<p>ext-echo: served from the fixture's own repository.</p>
```

`tests/fixtures/sources/ext-echo/deploy/storagebaby/README.md`: two lines — what the service is (busybox httpd on 8118 behind `ext-echo.<domain>`) and that `greeting` is a secret it mounts only to prove the external secrets path.

`hosts/test-a/services/ext-echo/source.yml` and the identical `hosts/test-ci/services/ext-echo/source.yml`:

```yaml
# A test-only pointer, placed on the test hosts and nowhere else. The repository is the
# throwaway git HTTP server `prepare.yml` runs on the VM, and the token the host presents to
# it is in the generated, gitignored source.sops.yaml beside this file. The tree it serves
# is tests/fixtures/sources/ext-echo. A real pointer names a GitHub repository over https.
repo: http://127.0.0.1:8418/ext-echo.git
ref: main
path: deploy/storagebaby
```

- [ ] **Step 3: Run the static suite to see the new failures**

Run: `mise run test-static`
Expected: `test_sources.py` passes; `test_render.py` and `test_contract.py` for `test-a/ext-echo` and `test-ci/ext-echo` FAIL because the playbook does not resolve a pointer yet (render output missing).

- [ ] **Step 4: The playbook resolves pointers**

`ansible/playbook.yml`, in `pre_tasks`, directly after "Load host configuration":

```yaml
# Where fetched external repositories are, on the controller: discovery, every
# `lookup('template')` and every `copy src:` read from the controller, so this is a
# controller path -- /var/lib/storagebaby/sources on a host whose deploy script runs the
# playbook, /repo/tests/fixtures/sources for Molecule (molecule.yml group var), the
# fixture tree for the static render (-e). Resolved through `default` and not declared
# as a play var, because a play var would outrank the inventory.
- name: Where fetched external repositories live
  ansible.builtin.set_fact:
    sources_root: "{{ sources_root | default('/var/lib/storagebaby/sources') }}"
```

After "Discover this host's service specs":

```yaml
# External services: a folder holding `source.yml` instead of `service.yml` points at a
# repository the deploy script fetched into sources_root/<name>, and the contract is at
# `path` inside it. Resolved here, before anything reads a spec, so every loop below --
# route names, TCP ports, the role include -- sees a pointer service exactly like an
# in-tree one.
- name: Discover shared service pointers
  ansible.builtin.find:
    paths: '{{ storagebaby_repo_root }}/hosts/shared/services'
    patterns: source.yml
    recurse: true
    depth: 2
    follow: true
  delegate_to: localhost
  become: false
  register: _shared_pointers

- name: Discover this host's service pointers
  ansible.builtin.find:
    paths: '{{ storagebaby_repo_root }}/hosts/{{ inventory_hostname }}/services'
    patterns: source.yml
    recurse: true
    depth: 2
    follow: true
  delegate_to: localhost
  become: false
  register: _host_pointers

- name: Resolve every pointer to the spec it fetches
  ansible.builtin.set_fact:
    _pointers: >-
      {{ (_pointers | default([])) + [{
           'name': pointer | dirname | basename,
           'pointer_dir': pointer | dirname,
           'spec': sources_root ~ '/' ~ (pointer | dirname | basename) ~ '/' ~ _source.path ~ '/service.yml'
         }] }}
  vars:
    _source: "{{ lookup('file', pointer) | from_yaml }}"
  loop: "{{ (_shared_pointers.files | map(attribute='path') | sort) + (_host_pointers.files | map(attribute='path') | sort) }}"
  loop_control:
    loop_var: pointer
    label: '{{ pointer | dirname | basename }}'

# A pointer whose repository is not fetched is a hard stop on a real converge -- the
# deploy script fetches before it runs the playbook, so this means the playbook was run
# by hand or the fetch failed. Under --check and in a render it is reported and skipped
# instead, the way a service user that does not exist yet is: the operator's pre-flight
# of a host that has never deployed has to reach the end of the play, and a static
# render has no host to fetch on.
- name: A pointer whose repository has not been fetched yet
  ansible.builtin.fail:
    msg: >-
      {{ item.name }} points at an external repository and {{ item.spec | dirname }} does
      not exist here. storagebaby-deploy.service fetches it before it runs this playbook;
      start that unit instead, or read its journal for the fetch that failed.
  loop: '{{ _pointers | default([]) }}'
  loop_control:
    label: '{{ item.name }}'
  when:
    - not (item.spec is file)
    - not ansible_check_mode
    - not (render_only | bool)

- name: What cannot be shown for a pointer that is not fetched yet
  ansible.builtin.debug:
    msg: >-
      {{ item.name }}: its repository is not fetched here ({{ item.spec | dirname }}), so
      nothing of this service can be rendered or described. The deploy unit fetches it.
  loop: '{{ _pointers | default([]) }}'
  loop_control:
    label: '{{ item.name }}'
  when: not (item.spec is file)

- name: The pointers whose repository is here
  ansible.builtin.set_fact:
    _resolved_pointers: "{{ _pointers | default([]) | selectattr('spec', 'file') | list }}"

# The same rule `test_contract.py` holds for an in-tree folder, asserted here because a
# private repository's contract is never seen by the static suite.
- name: A fetched service.yml names the service its pointer folder names
  ansible.builtin.assert:
    that: (lookup('file', item.spec) | from_yaml).name == item.name
    fail_msg: >-
      {{ item.spec }} says name: {{ (lookup('file', item.spec) | from_yaml).name }}, but
      the pointer folder is {{ item.name }}. The folder is the service's name on this host.
    quiet: true
  loop: '{{ _resolved_pointers }}'
  loop_control:
    label: '{{ item.name }}'

- name: Which pointer folder a spec came from (empty for an in-tree service)
  ansible.builtin.set_fact:
    _pointer_dir_of: "{{ dict(_resolved_pointers | map(attribute='spec') | zip(_resolved_pointers | map(attribute='pointer_dir'))) }}"
```

Change "Collect placed services (shared first, then this host's)" to

```yaml
- name: Collect placed services (shared first, then this host's; pointers after in-tree specs)
  ansible.builtin.set_fact:
    placed_services: >-
      {{ (_shared_specs.files | map(attribute='path') | sort)
         + (_resolved_pointers | selectattr('pointer_dir', 'search', '/hosts/shared/') | map(attribute='spec') | sort)
         + (_host_specs.files | map(attribute='path') | sort)
         + (_resolved_pointers | rejectattr('pointer_dir', 'search', '/hosts/shared/') | map(attribute='spec') | sort) }}
```

After "The host's TCP entrypoint ports, in a stable order" add the loopback-port assert:

```yaml
# Every loopback number the host's placement claims: route ports, TCP entrypoints and the
# targets that differ from their entrypoint, in one list -- the namespace
# tests/static/test_ports.py checks for every spec it can see. It cannot see a pointer to
# a private repository, so the host checks before it writes a single route file: two
# services on one loopback port is one of them silently unreachable.
# A `target` counts only when it is declared: paperless writes `target: 2121` for the
# port it forwards elsewhere and no `target` for the passive range, which is exactly
# the "target differs from port" distinction test_ports.py makes, without jmespath.
- name: Collect every port the placed services claim
  ansible.builtin.set_fact:
    _claimed_ports: >-
      {{ (_claimed_ports | default([]))
         + (_spec.routes | default([]) | map(attribute='port') | list)
         + ([_spec.port] if (_spec.port is defined and _spec.routes is not defined) else [])
         + (_spec.tcp_ports | default([]) | selectattr('target', 'defined') | map(attribute='target') | list) }}
  vars:
    _spec: "{{ lookup('file', placed_service) | from_yaml }}"
  loop: '{{ placed_services }}'
  loop_control:
    loop_var: placed_service
    label: '{{ placed_service | dirname | basename }}'

- name: No two placed services claim the same port
  ansible.builtin.assert:
    that: _duplicates | length == 0
    fail_msg: >-
      ports claimed twice on {{ inventory_hostname }}: {{ _duplicates | join(', ') }}.
      Route ports, TCP entrypoints and TCP targets are one namespace per host.
    quiet: true
  vars:
    _all: '{{ (_claimed_ports | default([])) + (_tcp_expanded | default([])) }}'
    _duplicates: "{{ _all | community.general.counter | dict2items | selectattr('value', 'gt', 1) | map(attribute='key') | list }}"
```

In the role include at the end of the playbook add `pointer_dir` to its `vars`:

```yaml
pointer_dir: "{{ _pointer_dir_of[placed_service] | default('') }}"
```

- [ ] **Step 5: Static suite green**

Run: `mise run test-static`
Expected: PASS. `test-a/ext-echo` and `test-ci/ext-echo` appear in `test_contract`, `test_render`, `test_quadlet_conventions`, `test_ports`, `test_secrets` (the secrets-file case skipped with the fixture reason).

- [ ] **Step 6: The git HTTP server and prepare**

`tests/integration/molecule/test-ci/files/test-git-http.py`:

```python
#!/usr/bin/env python3
"""A throwaway git server for the test VM: /srv/git over HTTP with basic auth, on 127.0.0.1:8418.

It speaks git's *dumb* HTTP protocol, which is static files -- a bare repository whose
`info/refs` and `objects/info/packs` are kept current by `git update-server-info` (the
repository's `post-update` hook runs it on every push). That is enough for a clone and a
fetch, and it is the standard library only: a smart server would mean `git http-backend`
behind a CGI runner, on a host that is supposed to look like storagebaby.

The deploy script's `GIT_ASKPASS` path is what this exists for: every request without a
valid token is answered 401 with a Basic challenge, which is what makes git ask for
credentials and present the pointer's `git_token`. Tokens are read from the credentials
file on *every* request, one per line, so a test can revoke one by editing the file and
put it back afterwards, without restarting anything. The username is not checked -- GitHub
ignores it for a token too, and the script sends `x-access-token`.
"""

import base64
import http.server

ROOT = "/srv/git"
CREDENTIALS = "/etc/test-git-http/credentials"
LISTEN = ("127.0.0.1", 8418)


def allowed(header: str | None) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        _, _, password = base64.b64decode(header[6:]).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    with open(CREDENTIALS) as fh:
        tokens = {line.strip() for line in fh if line.strip()}
    return password in tokens


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def challenge(self) -> bool:
        if allowed(self.headers.get("Authorization")):
            return False
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="test-git-http"')
        self.end_headers()
        return True

    def do_GET(self) -> None:
        if not self.challenge():
            super().do_GET()

    def do_HEAD(self) -> None:
        if not self.challenge():
            super().do_HEAD()


class Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    Server(LISTEN, Handler).serve_forever()
```

`tests/integration/molecule/test-ci/prepare.yml`:

(a) In the first play, after "The sink runs", install the git server the same way as the sink:

```yaml
# Stands in for GitHub, for the external repositories a pointer names: a basic-auth
# git server over HTTP on the loopback, serving /srv/git. The credentials file is
# filled below, once the pointer's token exists.
- name: Install the throwaway git HTTP server
  ansible.builtin.copy:
    src: files/test-git-http.py
    dest: /usr/local/sbin/test-git-http
    owner: root
    group: root
    mode: '0755'

- name: Its credentials directory and served root
  ansible.builtin.file:
    path: '{{ item }}'
    state: directory
    owner: root
    group: root
    mode: '0755'
  loop: [/etc/test-git-http, /srv/git]

- name: The git server's unit
  ansible.builtin.copy:
    dest: /etc/systemd/system/test-git-http.service
    owner: root
    group: root
    mode: '0644'
    content: |
      [Unit]
      Description=Throwaway basic-auth git server for the integration tests

      [Service]
      ExecStart=/usr/local/sbin/test-git-http
      Restart=always

      [Install]
      WantedBy=multi-user.target

- name: The git server runs
  ansible.builtin.systemd:
    name: test-git-http.service
    daemon_reload: true
    enabled: true
    state: started
```

(b) In the "Generate throwaway secrets" play, after "Write and encrypt the mail secret", add the pointer half:

```yaml
# External services. A pointer names a repository; the host fetches it and reads the
# service's secrets from *that* tree, so the fixture's secrets file is generated into the
# fixture tree (gitignored there) and never as a host override -- the override path is
# exactly what this fixture must not take. The pointer's own credential file gets the
# token the git server below will accept.
- name: Discover this host's pointers (shared and own)
  ansible.builtin.find:
    paths:
      - '{{ _hosts_root }}/shared/services'
      - '{{ _hosts_root }}/{{ inventory_hostname }}/services'
    patterns: source.yml
    recurse: true
    depth: 2
    follow: true
  register: _pointer_files
  delegate_to: localhost
  become: false

- name: The pointers, resolved against the fixture tree
  ansible.builtin.set_fact:
    pointers: >-
      {{ pointers | default([]) + [{
           'name': item.path | dirname | basename,
           'folder': item.path | dirname,
           'source_dir': storagebaby_repo_root ~ '/tests/fixtures/sources/' ~ (item.path | dirname | basename) ~ '/' ~ (lookup('file', item.path) | from_yaml).path
         }] }}
  loop: '{{ _pointer_files.files }}'
  loop_control:
    label: '{{ item.path | dirname | basename }}'

- name: Write and encrypt the fixture's own secrets file into its tree
  ansible.builtin.shell: |
    set -euo pipefail
    if [ -f "$OUT" ] && grep -q '{{ storagebaby_age_pub }}' "$OUT"; then
      echo keeping
      exit 0
    fi
    rm -f "$TMP"
    {% for key in (lookup('file', item.source_dir ~ '/service.yml') | from_yaml).secrets %}
    printf '%s: "%s"\n' '{{ key }}' "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" >> "$TMP"
    {% endfor %}
    sops --encrypt --age {{ storagebaby_age_pub }} --input-type yaml --output-type yaml "$TMP" > "$OUT"
    rm -f "$TMP"
  args:
    executable: /bin/bash
    chdir: /tmp
  environment:
    TMP: '/tmp/{{ inventory_hostname }}-ext-{{ item.name }}-secrets.yaml'
    OUT: '{{ item.source_dir }}/secrets.sops.yaml'
  loop: '{{ pointers | default([]) }}'
  loop_control:
    label: '{{ item.name }}'
  when: (lookup('file', item.source_dir ~ '/service.yml') | from_yaml).secrets | length > 0
  delegate_to: localhost
  register: _ext_secret
  changed_when: _ext_secret.stdout != 'keeping'

# git_token only here; Task 3 adds registry_user and registry_token to the same file.
- name: Write and encrypt the pointer's credential file
  ansible.builtin.shell: |
    set -euo pipefail
    if [ -f "$OUT" ] && grep -q '{{ storagebaby_age_pub }}' "$OUT"; then
      echo keeping
      exit 0
    fi
    printf 'git_token: "%s"\n' "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" > "$TMP"
    sops --encrypt --age {{ storagebaby_age_pub }} --input-type yaml --output-type yaml "$TMP" > "$OUT"
    rm -f "$TMP"
  args:
    executable: /bin/bash
    chdir: /tmp
  environment:
    TMP: '/tmp/{{ inventory_hostname }}-pointer-{{ item.name }}.yaml'
    OUT: '{{ item.folder }}/source.sops.yaml'
  loop: '{{ pointers | default([]) }}'
  loop_control:
    label: '{{ item.name }}'
  delegate_to: localhost
  register: _pointer_secret
  changed_when: _pointer_secret.stdout != 'keeping'

# The token the host will present is read back from this file *on the VM*, where the key
# is: prepare on a surviving VM keeps the file ("keeping" above) and the controller cannot
# decrypt it. The git server play and Task 3's registry play both read the copy.
- name: Copy each pointer's credential file to the VM
  ansible.builtin.copy:
    src: '{{ item.folder }}/source.sops.yaml'
    dest: /tmp/pointer-{{ item.name }}.sops.yaml
    mode: '0600'
  loop: '{{ pointers | default([]) }}'
  loop_control:
    label: '{{ item.name }}'
```

The `pointers` fact also needs the branch the seed pushes: add `'ref': (lookup('file', item.path) | from_yaml).ref` to the dict in "The pointers, resolved against the fixture tree".

(c) In the "Seed a local git remote" play, in the archive step add the pointer credentials after the line that appends the host's secrets (the generated `source.sops.yaml` is gitignored, so `--exclude-vcs-ignores` dropped it, like the secrets):

```
        tar -C /repo -rf "$TAR" hosts/{{ inventory_hostname }}/secrets
        (cd /repo && find hosts/{{ inventory_hostname }}/services hosts/shared/services -name source.sops.yaml -print0 \
          | xargs -0 -r tar -C /repo -rf "$TAR")
```

In "Seed the repo and the bare remote from the extracted tree" force-add them after the secrets:

```
        for f in $(find hosts/{{ inventory_hostname }}/services hosts/shared/services -name source.sops.yaml 2> /dev/null); do
          git -c user.name=t -c user.email=t@t add -f "$f"
        done
```

(d) A new play after it, "Seed the external repositories and let the git server accept their tokens":

```yaml
- name: Seed every pointed external repository on the VM's git server
  hosts: all
  gather_facts: false
  tasks:
    # One line per token in the server's credentials file, decrypted on the VM from the copy
    # the secrets play made.
    - name: The git server accepts exactly those tokens
      ansible.builtin.shell: |
        set -euo pipefail
        : > /etc/test-git-http/credentials
        {% for p in pointers | default([]) %}
        SOPS_AGE_KEY_FILE=/etc/storagebaby/age.key sops --decrypt --extract '["git_token"]' /tmp/pointer-{{ p.name }}.sops.yaml >> /etc/test-git-http/credentials
        echo >> /etc/test-git-http/credentials
        {% endfor %}
        chmod 0600 /etc/test-git-http/credentials
      args:
        executable: /bin/bash
      changed_when: true
      no_log: true

    - name: Archive each fixture repository on the controller
      ansible.builtin.shell: |
        set -e
        tar -C "$SRC" --exclude=__pycache__ -cf "$TAR" .
        gzip -f "$TAR"
      environment:
        SRC: '{{ storagebaby_repo_root }}/tests/fixtures/sources/{{ item.name }}'
        TAR: '/tmp/{{ inventory_hostname }}-ext-{{ item.name }}.tar'
      loop: '{{ pointers | default([]) }}'
      loop_control:
        label: '{{ item.name }}'
      delegate_to: localhost
      changed_when: true

    - name: A working copy directory per external repository
      ansible.builtin.file:
        path: '/srv/ext-src/{{ item.name }}'
        state: directory
        owner: root
        group: root
        mode: '0755'
      loop: '{{ pointers | default([]) }}'
      loop_control:
        label: '{{ item.name }}'

    # `--no-same-owner`, like the platform seed: the archive carries the operator's uid.
    - name: Copy and extract each one under /srv/ext-src
      ansible.builtin.unarchive:
        src: '/tmp/{{ inventory_hostname }}-ext-{{ item.name }}.tar.gz'
        dest: '/srv/ext-src/{{ item.name }}'
        extra_opts: [--no-same-owner]
      loop: '{{ pointers | default([]) }}'
      loop_control:
        label: '{{ item.name }}'

    # Same shape as the platform seed: a working copy, a bare remote, a force-push of the
    # branch the pointer names, every run -- so a test's pushes are undone by the next
    # prepare. `post-update` is what keeps the dumb protocol's index files current.
    - name: Seed each bare remote from its working copy
      ansible.builtin.shell: |
        set -e
        cd /srv/ext-src/{{ item.name }}
        [ -d .git ] || git init -q -b {{ item.ref }}
        git -c user.name=t -c user.email=t@t add -A
        git -c user.name=t -c user.email=t@t add -f $(find . -name secrets.sops.yaml)
        git -c user.name=t -c user.email=t@t commit -q --allow-empty -m 'seed'
        if [ ! -d /srv/git/{{ item.name }}.git ]; then
          git clone -q --bare . /srv/git/{{ item.name }}.git
          cp /srv/git/{{ item.name }}.git/hooks/post-update.sample /srv/git/{{ item.name }}.git/hooks/post-update
        fi
        git remote remove origin 2> /dev/null || true
        git remote add origin /srv/git/{{ item.name }}.git
        git push -qf origin {{ item.ref }}
        git -C /srv/git/{{ item.name }}.git update-server-info
      loop: '{{ pointers | default([]) }}'
      loop_control:
        label: '{{ item.name }}'
      changed_when: true
```

The `pointers` fact is set in the secrets play and read by this one: both plays run on `all`, and a `set_fact` persists across plays for the same host, so no re-discovery is needed.

`tests/integration/molecule/test-ci/molecule.yml`, under `provisioner.inventory.group_vars.all` add:

```yaml
# Where the controller finds the external repositories a test host points at:
# the fixture tree, which prepare also pushes to the VM's git server.
sources_root: /repo/tests/fixtures/sources
```

- [ ] **Step 7: Integration helpers see pointer services**

`tests/integration/molecule/test-ci/tests/test_service.py`: replace `_specs()` and add `service_name`:

```python
FIXTURE_SOURCES = Path("/repo/tests/fixtures/sources")


def service_name(spec_path: Path) -> str:
    """The service's name: the `name` key, which the contract holds equal to its folder.

    Not `spec_path.parent.name`: an external service's `service.yml` sits at
    `tests/fixtures/sources/<name>/<path>/service.yml`, and its parent is the last
    component of `path`, not the service.
    """
    return load_spec(spec_path)["name"]


def _resolve(folder: Path) -> Path | None:
    """A service folder's spec: its own service.yml, or the one its pointer fetches."""
    if (folder / "service.yml").exists():
        return folder / "service.yml"
    if (folder / "source.yml").exists():
        source = load_spec(folder / "source.yml")
        spec = FIXTURE_SOURCES / folder.name / source["path"] / "service.yml"
        return spec if spec.exists() else None
    return None


def _specs() -> list[tuple[str, Path]]:
    """(owner, spec path) for every service spec; owner is 'shared' or a host name."""
    found = []
    for owner_dir in [HOSTS / "shared"] + sorted(p for p in HOSTS.iterdir() if p.is_dir() and p.name != "shared"):
        for folder in sorted(p for p in (owner_dir / "services").iterdir() if p.is_dir()):
            spec = _resolve(folder)
            if spec is not None:
                found.append((owner_dir.name, spec))
    return found
```

Then replace every `spec_path.parent.name` / `p.parent.name` with `service_name(...)` at these sites (run `grep -n 'parent\.name' tests/integration/molecule/test-ci/tests/*.py` to confirm the list is still these eight): `test_service.py:34` (ids) and `:119`; `test_binds.py:25`; `test_kopia.py:35,37`; `test_ftp.py:52`; `test_deploy.py:109,255`. Import `service_name` from `test_service` where it is not yet imported.

`tests/integration/molecule/test-ci/tests/test_external.py`, new:

```python
"""The external-service path, on the VM: a service placed by a pointer and fetched by the host.

The generic claims (user, secret, route, unit active, healthy, answers over HTTPS) land on
`ext-echo` through test_service.py like on any service. What is specific to a pointer
service is *where its definition came from*, and that is what this file asserts. Everything
here runs after the first deploy, because the fetched tree only exists once a tick ran.
"""

from pathlib import Path

import pytest
from test_service import HOSTS, load_spec, placed

SOURCES = "/var/lib/storagebaby/sources"
FIXTURE = Path("/repo/tests/fixtures/sources")


def pointer_cases() -> list[tuple[str, str, dict]]:
    """(owner, name, source.yml) for every pointer in the repo."""
    out = []
    for owner_dir in [HOSTS / "shared"] + sorted(p for p in HOSTS.iterdir() if p.is_dir() and p.name != "shared"):
        for pointer in sorted((owner_dir / "services").glob("*/source.yml")):
            out.append((owner_dir.name, pointer.parent.name, load_spec(pointer)))
    return out


CASES = pointer_cases()
pointer_case = pytest.mark.parametrize("owner,name,source", CASES, ids=[f"{o}/{n}" for o, n, _ in CASES])


@pytest.mark.order(-1)
@pointer_case
def test_the_host_fetched_the_pointed_repository(host, owner, name, source):
    placed(host, owner)
    checkout = host.file(f"{SOURCES}/{name}")
    # The parent is what keeps the fetched trees root-only (0700); a clone itself is 0755.
    assert checkout.is_directory and checkout.user == "root", f"{SOURCES}/{name}"
    head = host.check_output(f"git -C {SOURCES}/{name} rev-parse HEAD")
    remote = host.check_output(f"git -C /srv/git/{name}.git rev-parse {source['ref']}")
    assert head == remote, f"{name}: fetched {head}, the remote's {source['ref']} is {remote}"
    assert host.file(f"{SOURCES}/{name}/{source['path']}/service.yml").exists
    assert host.file("/var/lib/storagebaby/sources").mode == 0o700


@pytest.mark.order(-1)
@pointer_case
def test_the_secrets_came_from_the_external_tree_and_not_from_an_override(host, owner, name, source):
    """The file the host decrypted is the external repository's, byte for byte.

    A host override under hosts/<host>/secrets/ would also make the generic secret test
    pass; the point of this case is that none exists for this service and the copy on the
    host is the fixture tree's own. The two can be byte-equal on either route -- the
    Molecule converge copying from the controller's fixture tree, the deploy tick copying
    from the fetched checkout -- because prepare generated the file *into* the fixture tree
    and seeded the external remote from that same tree.
    """
    hostname = host.check_output("uname -n")
    placed(host, owner)
    assert not (HOSTS / hostname / "secrets" / f"{name}.sops.yaml").exists(), "an override would hide the external path"
    external = (FIXTURE / name / source["path"] / "secrets.sops.yaml").read_bytes()
    on_host = host.file(f"/etc/storagebaby/{name}.secrets.sops.yaml")
    assert on_host.exists and on_host.mode == 0o600
    assert on_host.content == external


@pytest.mark.order(-1)
@pointer_case
def test_the_page_served_is_the_external_repositorys_config(host, owner, name, source):
    """config/ reached /etc/storagebaby/<name>/ from the fetched tree: the page says so."""
    placed(host, owner)
    hostname = host.check_output("uname -n")
    domain = load_spec(HOSTS / hostname / "host.yml")["domain"]
    spec = load_spec(FIXTURE / name / source["path"] / "service.yml")
    r = host.run(f"curl -sk -H 'Host: {spec['domain']}.{domain}' https://127.0.0.1/")
    assert r.rc == 0, r.stderr
    assert "served from the fixture's own repository" in r.stdout, r.stdout
```

- [ ] **Step 8: Integration run (coordinate first)**

Run: `pgrep -af molecule`; when clear: `MOLECULE_HOST=test-ci mise run test-integration`.
Expected: prepare's pre-flight (`--check` on a never-deployed host) passes with the "not fetched yet" debug line for `ext-echo`; converge places `ext-echo` from the fixture tree; idempotence zero changes; verify green including the three `test_external.py` cases and every generic case for `test-ci/ext-echo`.

- [ ] **Step 9: Commit**

```bash
git add ansible/playbook.yml tests/static tests/fixtures hosts/test-a/services/ext-echo hosts/test-ci/services/ext-echo .gitignore tests/integration
git commit -m "Place a service from its own repository through a pointer folder

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `registries` — a private image for a pointer service

Spec §2 (`registries`), §4 (`auth.json`), §5 images, §6 registry fixture.

**Files:**

- Create: `ansible/roles/service/tasks/registries.yml`
- Modify: `ansible/roles/service/tasks/host.yml` (`svc_home`, include between the helpers and `Images`, the ordering comment)
- Modify: `tests/fixtures/sources/ext-echo/deploy/storagebaby/service.yml` and `quadlet/ext-echo.container.j2`
- Modify: `tests/integration/molecule/test-ci/prepare.yml` (`registry_user`/`registry_token` in the pointer credential; the registry user, its Quadlet, htpasswd, insecure-registry drop-in, the pushed image)
- Modify: `tests/integration/molecule/test-ci/tests/test_external.py`
- Modify: `ansible/roles/service/README.md` (a `## Registries` section — the reference is part of the feature, the rest of the docs are Task 5)

**Interfaces:**

- Consumes: `pointer_dir` from Task 2; `source.sops.yaml` keys `registry_user`, `registry_token`.
- Produces: `/home/svc-<name>/.config/containers/auth.json` (0600, svc-owned) for a service with `registries`, removed for one without; `/etc/storagebaby/<name>.source.sops.yaml` (0600 root); role fact `svc_home`. On the VM: registry `127.0.0.1:5000` run by user `test-registry`, htpasswd user `test`, image `127.0.0.1:5000/ext-echo:latest`.

- [ ] **Step 1: Failing verifier first**

`tests/integration/molecule/test-ci/tests/test_external.py`, append:

```python
AUTH_JSON = ".config/containers/auth.json"
# `run_as` joins the imports at the top of the file: `from conftest import run_as`.


@pytest.mark.order(-1)
@pointer_case
def test_a_service_with_registries_has_a_login_its_user_owns(host, owner, name, source):
    """`registries` becomes the service user's auth.json, 0600, written before the pull.

    The path is the one podman falls back to for a user whose runtime dir holds no
    auth.json -- and that it *is* read is what the pull of this very image proved: the role
    fetched `127.0.0.1:5000/ext-echo:latest` as the service user, and the registry answers
    nothing without the login.
    """
    placed(host, owner)
    spec = load_spec(FIXTURE / name / source["path"] / "service.yml")
    if not spec.get("registries"):
        pytest.skip("no registries declared")
    user = f"svc-{name}"
    home = host.user(user).home
    auth = host.file(f"{home}/{AUTH_JSON}")
    assert auth.exists and auth.mode == 0o600 and auth.user == user and auth.group == user, auth.path
    for registry in spec["registries"]:
        assert f'"{registry}"' in auth.content_string, f"{registry} is not in {auth.path}"
    credential = host.file(f"/etc/storagebaby/{name}.source.sops.yaml")
    assert credential.exists and credential.mode == 0o600 and credential.user == "root"


@pytest.mark.order(-1)
@pointer_case
def test_auto_update_authenticates_with_the_same_login(host, owner, name, source):
    """The service user's own `podman auto-update` reaches the private registry.

    A dry run contacts the registry for every `AutoUpdate=registry` container and reports
    `pending` or `false`; a login it could not use reports `failed`. This is the half the
    role's pull cannot stand in for -- the timer runs under the user manager, with the
    environment that manager gives it.
    """
    placed(host, owner)
    spec = load_spec(FIXTURE / name / source["path"] / "service.yml")
    if not spec.get("registries"):
        pytest.skip("no registries declared")
    r = run_as(host, f"svc-{name}", "podman auto-update --dry-run --format '{{.ContainerName}} {{.Updated}}'")
    assert r.rc == 0, r.stderr
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith(f"{name} ")), None)
    assert line, f"auto-update lists no {name}:\n{r.stdout}"
    assert line.split()[1] in {"false", "pending"}, f"auto-update could not check the private image: {line}\n{r.stderr}"


def test_an_in_tree_service_has_no_registry_login(host):
    home = host.user("svc-traefik").home
    assert not host.file(f"{home}/{AUTH_JSON}").exists
```

- [ ] **Step 2: The role**

`ansible/roles/service/tasks/host.yml`: in "Record uid and unit dir" add `svc_home: '{{ getent_passwd[svc_user][4] }}'`. After "Install the service-user helpers" and before the `# Images, before anything...` comment, insert:

```yaml
# A registry login is a *pull prerequisite*, like the helpers above it: it is the one other
# thing written before the pull, because the pull is what needs it. Nothing of the service
# proper -- no volume, no config, no secret, no unit -- is touched until images.yml is
# through. Only a pointer service declares `registries`; the credential is the pointer's.
- name: Registry login
  ansible.builtin.include_tasks: registries.yml
```

and change the first line of the `# Images, before anything of this service is written to the host:` comment to `# Images, before anything of this service proper is written to the host (the registry login above is the pull's own prerequisite):`.

`ansible/roles/service/tasks/registries.yml`:

```yaml
# `registries`: the registry hosts this service pulls private images from. The service user
# gets an auth.json at the path rootless podman reads when its runtime directory holds
# none -- ~/.config/containers/auth.json -- from the `registry_user`/`registry_token` of the
# pointer's source.sops.yaml. Written before the pull (host.yml orders it so), and read by
# the user's own podman-auto-update.timer too; the integration suite's private-registry
# fixture is what proves both.
#
# A service that declares none gets the file removed, so a `registries` line that goes away
# takes the login with it.
- name: Where the service user's registry login lives
  ansible.builtin.set_fact:
    registry_auth_file: '{{ svc_home }}/.config/containers/auth.json'
    service_registries: '{{ service.registries | default([]) }}'
    # Reset per service: the role is included once per placed service in one play.
    _auths: {}

- name: No login for a service that declares no registries
  ansible.builtin.file:
    path: '{{ registry_auth_file }}'
    state: absent
  when: service_registries | length == 0

- name: Registry login for a service that declares registries
  when: service_registries | length > 0
  block:
    - name: Only an external service can declare registries
      ansible.builtin.assert:
        that:
          - pointer_dir != ''
          - (pointer_dir ~ '/source.sops.yaml') is file
        fail_msg: >-
          {{ service.name }} declares registries, but the credential for a registry login is
          the pointer's source.sops.yaml -- and {{ 'this is an in-tree service' if pointer_dir == '' else pointer_dir ~ ' has no source.sops.yaml' }}.
        quiet: true

    - name: Copy the pointer's credential file to the host
      ansible.builtin.copy:
        src: '{{ pointer_dir }}/source.sops.yaml'
        dest: '/etc/storagebaby/{{ service.name }}.source.sops.yaml'
        owner: root
        group: root
        mode: '0600'

    # A pre-flight of a service that is gaining its first `registries` line finds no copy on
    # the host yet (the copy above is skipped under --check); the decrypt is skipped with it
    # and the login is only described. On a converged service the file is there.
    - name: Look for the credential file on the host
      ansible.builtin.stat:
        path: '/etc/storagebaby/{{ service.name }}.source.sops.yaml'
      register: _source_file

    - name: Decrypt the pointer's credential on the host
      ansible.builtin.command: sops --decrypt --output-type json /etc/storagebaby/{{ service.name }}.source.sops.yaml
      environment:
        SOPS_AGE_KEY_FILE: /etc/storagebaby/age.key
      register: _source_decrypted
      check_mode: false
      changed_when: false
      failed_when: false
      no_log: true
      when: _source_file.stat.exists

    - name: The host could decrypt it
      ansible.builtin.assert:
        that: _source_decrypted.rc == 0
        fail_msg: 'sops failed to decrypt the registry credential of {{ service.name }}: {{ _source_decrypted.stderr }}'
        quiet: true
      when: _source_file.stat.exists

    - name: The credential carries a user and a token
      ansible.builtin.assert:
        that: "['registry_user', 'registry_token'] | difference(_source_decrypted.stdout | from_json) | length == 0"
        fail_msg: '{{ service.name }} declares registries but source.sops.yaml has no registry_user and registry_token'
        quiet: true
      when: _source_file.stat.exists

    - name: The service user's config directories
      ansible.builtin.file:
        path: '{{ svc_home }}/{{ item }}'
        state: directory
        owner: '{{ svc_user }}'
        group: '{{ svc_user }}'
        mode: '0700'
      loop: [.config, .config/containers]

    # The file podman reads: {"auths": {"<registry>": {"auth": "<base64 user:token>"}}}. Built
    # with a loop, one entry per declared registry, all from the one credential pair.
    - name: One login entry per declared registry
      ansible.builtin.set_fact:
        _auths: "{{ _auths | combine({item: {'auth': _auth}}) }}"
      vars:
        _creds: '{{ _source_decrypted.stdout | from_json }}'
        _auth: "{{ (_creds.registry_user ~ ':' ~ _creds.registry_token) | b64encode }}"
      loop: '{{ service_registries }}'
      no_log: true
      when: _source_file.stat.exists

    - name: Write the registry login
      ansible.builtin.copy:
        content: "{{ {'auths': _auths} | to_nice_json }}\n"
        dest: '{{ registry_auth_file }}'
        owner: '{{ svc_user }}'
        group: '{{ svc_user }}'
        mode: '0600'
      no_log: true
      when: _source_file.stat.exists

    - name: What a pre-flight cannot write yet
      ansible.builtin.debug:
        msg: '{{ service.name }}: the registry login will be written on the converge, once its credential file is on the host.'
      when: not _source_file.stat.exists
```

**If the first integration run fails at the pull with an unauthorized error**, do not debug podman's lookup: the fallback is to name the file explicitly. Add `Environment=REGISTRY_AUTH_FILE=%h/.config/containers/auth.json` through a Quadlet drop-in the role renders for a service with `service_registries` (a `10-storagebaby-registry.conf` under `<unit>.d/`, beside the host-gateway drop-in and listed in `test_render.py` the same way), and export `REGISTRY_AUTH_FILE=/home/<user>/.config/containers/auth.json` in `podman-as` when that file exists. The expectation is that neither is needed: `runuser -u` resets `HOME` to the target user's, and containers/image falls back to `$HOME/.config/containers/auth.json` when the runtime directory holds none. Record which it was in the spec's Amendments.

- [ ] **Step 3: The fixture pulls privately**

`tests/fixtures/sources/ext-echo/deploy/storagebaby/service.yml`: add

```yaml
# The image is private: it lives in the throwaway registry prepare runs on the VM, behind
# an htpasswd login. The host logs the service user in from the pointer's source.sops.yaml.
registries: ['127.0.0.1:5000']
```

`quadlet/ext-echo.container.j2`: replace the `Image=` comment and line with

```
# The test VM's private registry. `latest` and `AutoUpdate=registry` on a fixture, against
# the rule the other fixtures follow, because this one measures the auto-update timer's
# login: the image is pushed once by prepare and moves for nobody.
Image=127.0.0.1:5000/ext-echo:latest
AutoUpdate=registry
```

- [ ] **Step 4: The registry on the VM**

`prepare.yml`, "Write and encrypt the pointer's credential file": the generated file gets three keys —

```
        {
          printf 'git_token: "%s"\n' "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
          printf 'registry_user: "test"\n'
          printf 'registry_token: "%s"\n' "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
        } > "$TMP"
```

In the "Partition" play's harness packages add `python-bcrypt` (`registry:2` accepts bcrypt htpasswd entries only). Then a new play before "Seed every pointed external repository":

```yaml
# A private registry on the loopback, for the one fixture that pulls privately. Rootless
# and under its own throwaway user, because a root container on the VM would fail
# `test_no_root_containers` -- and because that is how everything else here runs. The
# login is the pointer's `registry_user`/`registry_token`, read back on the VM like the git
# token.
- name: Run a throwaway private registry
  hosts: all
  gather_facts: false
  vars:
    _reg_user: test-registry
  tasks:
    - name: The registry's user
      ansible.builtin.user:
        name: '{{ _reg_user }}'
        system: true
        shell: /usr/bin/nologin
        create_home: true

    - name: Its uid
      ansible.builtin.getent:
        database: passwd
        key: '{{ _reg_user }}'

    - name: Subordinate ids and linger
      ansible.builtin.shell: |
        set -e
        grep -q '^{{ _reg_user }}:' /etc/subuid || usermod --add-subids {{ _reg_user }}
        loginctl enable-linger {{ _reg_user }}
      changed_when: true

    - name: Wait for its user manager
      ansible.builtin.command: systemctl --user -M {{ _reg_user }}@ is-system-running --wait
      register: _reg_mgr
      retries: 15
      delay: 2
      until: _reg_mgr.rc == 0 or 'degraded' in _reg_mgr.stdout
      failed_when: _reg_mgr.rc not in [0, 1]
      changed_when: false

    - name: Registry directories
      ansible.builtin.file:
        path: '{{ item }}'
        state: directory
        owner: '{{ _reg_user }}'
        group: '{{ _reg_user }}'
        mode: '0755'
      loop:
        [/srv/test-registry, /srv/test-registry/auth, /srv/test-registry/data]

    - name: The htpasswd entry, from the pointer's registry token
      ansible.builtin.shell: |
        set -euo pipefail
        token=$(SOPS_AGE_KEY_FILE=/etc/storagebaby/age.key sops --decrypt --extract '["registry_token"]' /tmp/pointer-ext-echo.sops.yaml)
        user=$(SOPS_AGE_KEY_FILE=/etc/storagebaby/age.key sops --decrypt --extract '["registry_user"]' /tmp/pointer-ext-echo.sops.yaml)
        python3 - "$user" "$token" > /srv/test-registry/auth/htpasswd << 'PY'
        import sys
        import bcrypt
        user, token = sys.argv[1], sys.argv[2]
        print(user + ":" + bcrypt.hashpw(token.encode(), bcrypt.gensalt()).decode())
        PY
        chown {{ _reg_user }}:{{ _reg_user }} /srv/test-registry/auth/htpasswd
        chmod 0644 /srv/test-registry/auth/htpasswd
        printf '%s %s\n' "$user" "$token" > /root/.test-registry-login
        chmod 0600 /root/.test-registry-login
      args:
        executable: /bin/bash
      changed_when: true
      no_log: true

    # Plain HTTP on the loopback: podman refuses that for a registry it does not know to be
    # insecure. Harness configuration, written by nothing in a role.
    - name: Podman treats the loopback registry as insecure
      ansible.builtin.copy:
        dest: /etc/containers/registries.conf.d/50-test-registry.conf
        content: |
          [[registry]]
          location = "127.0.0.1:5000"
          insecure = true
        owner: root
        group: root
        mode: '0644'

    - name: The registry's Quadlet, in its user's unit directory
      ansible.builtin.copy:
        dest: '/etc/containers/systemd/users/{{ getent_passwd[_reg_user][1] }}/test-registry.container'
        content: |
          [Unit]
          Description=Throwaway private registry for the integration tests

          [Container]
          Image=docker.io/library/registry:2.8.3
          ContainerName=test-registry
          PublishPort=127.0.0.1:5000:5000
          Volume=/srv/test-registry/auth:/auth:ro
          Volume=/srv/test-registry/data:/var/lib/registry
          Environment=REGISTRY_AUTH=htpasswd
          Environment=REGISTRY_AUTH_HTPASSWD_REALM=test-registry
          Environment=REGISTRY_AUTH_HTPASSWD_PATH=/auth/htpasswd

          [Service]
          Restart=always

          [Install]
          WantedBy=default.target
        owner: root
        group: root
        mode: '0644'

    - name: The registry runs
      ansible.builtin.shell: |
        set -e
        systemctl --user -M {{ _reg_user }}@ daemon-reload
        systemctl --user -M {{ _reg_user }}@ start test-registry.service
        for _ in $(seq 60); do
          code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5000/v2/ || true)
          [ "$code" = 401 ] && exit 0
          sleep 2
        done
        echo "the registry did not come up (last status: $code)" >&2
        exit 1
      changed_when: true

    # The fixture's image: busybox, pushed under the registry's name as the registry's own
    # user. Pinned base, floating target tag -- the tag moves for nobody after this.
    - name: Push the fixture image into it
      ansible.builtin.shell: |
        set -euo pipefail
        read -r user token < /root/.test-registry-login
        uid=$(id -u {{ _reg_user }})
        as_reg() { cd /tmp && runuser -u {{ _reg_user }} -- env XDG_RUNTIME_DIR=/run/user/$uid DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus "$@"; }
        as_reg podman pull docker.io/library/busybox:1.37.0
        as_reg podman tag docker.io/library/busybox:1.37.0 127.0.0.1:5000/ext-echo:latest
        printf '%s' "$token" | as_reg podman login --tls-verify=false -u "$user" --password-stdin 127.0.0.1:5000
        as_reg podman push --tls-verify=false 127.0.0.1:5000/ext-echo:latest
      args:
        executable: /bin/bash
      changed_when: true
      no_log: true
```

This play reads `/tmp/pointer-ext-echo.sops.yaml`, which the secrets play copied to the VM in Task 2, so it goes after that play and before the external seed play. The name `ext-echo` is hard-coded in the htpasswd task on purpose: one private-pulling fixture exists; a second one would get a second user line. The harness Quadlet carries no `HealthCmd`: it is not a service of the platform and no convention test reads it; "The registry runs" polls `/v2/` for the 401 that says it is up.

- [ ] **Step 5: README of the role**

`ansible/roles/service/README.md`, a new section before "## What every `.container` must declare":

```markdown
## Registries

`registries: [ghcr.io]` in `service.yml` logs the service user into those registries before
any image is pulled: the role writes `~svc-<name>/.config/containers/auth.json` (0600) from
the `registry_user` and `registry_token` of the pointer's `source.sops.yaml`. Only an
external service can declare it -- the credential is the _pointer's_, and an in-tree service
has no pointer. The file is removed again when the key goes. Rootless podman reads that path
when its runtime directory holds no `auth.json`, for `podman pull` and for the user's own
`podman-auto-update.timer` alike; the integration suite's `ext-echo` fixture pulls from an
htpasswd registry through it and runs `podman auto-update --dry-run` against it.
```

- [ ] **Step 6: Static, then integration**

Run: `mise run test-static` — PASS (lint on the new task file, the fixture's new `Image=` passes `test_rendered_images_are_pullable_references`, `registries` passes the contract).
Run (coordinated): `MOLECULE_HOST=test-ci mise run test-integration` — the three new `test_external.py` cases pass, `test_every_image_a_unit_names_is_in_the_local_store` passes for `ext-echo`, idempotence stays at zero changes (auth.json content is deterministic).

- [ ] **Step 7: Commit**

```bash
git add ansible/roles/service tests/fixtures tests/integration
git commit -m "Log a pointer service's user into the registries it declares, from the pointer's credential

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The deploy path of an external repository, measured

Spec §6 verify cases: a push to the external repository, a push that renders nothing new, a revoked token, a removed pointer.

**Files:**

- Modify: `tests/integration/molecule/test-ci/tests/test_deploy.py` (four cases, placed after `test_deploy_runs_after_change_hooks`; the removal case last in the file)

**Interfaces:**

- Consumes: `/srv/ext-src/<name>` (working copy), `/srv/git/<name>.git` (remote), `/etc/test-git-http/credentials`, `STATE`, `NOOP_LINE`, `DEPLOY`, `JOURNAL`, `unit_marks`, `assert_the_role_did_not_restart`, `other_placed_service` from Task 1/2.

- [ ] **Step 1: The cases**

Append after `test_deploy_runs_after_change_hooks` (import `pointer_cases` from `test_external`, or copy the three-line helper, and add at module level `EXT_SRC = "/srv/ext-src"`):

```python
def first_pointer(host) -> tuple[str, str, dict] | None:
    """(owner, name, source.yml) of the first pointer placed on this VM, or None.

    The owner is 'shared' or the host folder, which is where the pointer folder lives --
    the removal case below deletes it from the seeded checkout and needs that path.
    """
    hostname = host.check_output("uname -n")
    for owner, name, source in pointer_cases():
        if owner in ("shared", hostname):
            return owner, name, source
    return None


def state_lines(host) -> dict[str, str]:
    return dict(ln.split(" ", 1) for ln in host.file(STATE).content_string.splitlines() if " " in ln)


@pytest.mark.order(-1)
def test_a_push_to_the_external_repository_restarts_only_its_service(host):
    """A commit in the pointed repository is a deploy: that service restarts, nothing else.

    The change is one `Environment=` line in the fixture's template, which reaches exactly
    one rendered file. The state file records the external HEAD afterwards, so the next
    tick is a no-op again.
    """
    found = first_pointer(host)
    if not found:
        pytest.skip("no pointer placed on this host")
    _, name, source = found
    user, unit = f"svc-{name}", f"{name}.service"
    other = other_placed_service(host)
    before = unit_marks(host, user, unit)
    assert before.active_since != "0", f"{unit} has no ActiveEnterTimestamp"
    traefik_before = unit_marks(host, "svc-traefik", "traefik.service")
    other_before = unit_marks(host, f"svc-{other[0]}", other[1]) if other and other[0] != name else None

    template = f"{EXT_SRC}/{name}/{source['path']}/quadlet/{name}.container.j2"
    r = host.run(
        f"cd {EXT_SRC}/{name} && sed -i 's|^Environment=TZ=.*|&\\nEnvironment=EXT_DEPLOY_MARK=2|' {template} "
        f"&& git -c user.name=t -c user.email=t@t commit -qam 'an environment line' && git push -q origin {source['ref']}"
    )
    assert r.rc == 0, r.stderr
    pushed = host.check_output(f"git -C /srv/git/{name}.git rev-parse {source['ref']}")

    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout
    rendered = host.file(f"/etc/containers/systemd/users/{host.user(user).uid}/{name}.container").content_string
    assert "Environment=EXT_DEPLOY_MARK=2" in rendered, rendered
    assert unit_marks(host, user, unit).active_since != before.active_since, f"{unit} was not restarted"
    assert_the_role_did_not_restart(host, "svc-traefik", "traefik.service", traefik_before, "traefik restarted too")
    if other_before:
        assert_the_role_did_not_restart(host, f"svc-{other[0]}", other[1], other_before, f"{other[0]} restarted too")
    assert state_lines(host)[f"source:{name}"] == pushed
    assert host.run(f"systemctl --user -M {user}@ is-active {unit}").stdout.strip() == "active"


@pytest.mark.order(-1)
def test_a_push_that_renders_nothing_new_restarts_nothing(host):
    """A README commit in the pointed repository runs the playbook and changes no unit."""
    found = first_pointer(host)
    if not found:
        pytest.skip("no pointer placed on this host")
    _, name, source = found
    user, unit = f"svc-{name}", f"{name}.service"
    before = unit_marks(host, user, unit)
    r = host.run(
        f"cd {EXT_SRC}/{name} && printf '\\nA line nothing renders.\\n' >> README.md "
        f"&& git -c user.name=t -c user.email=t@t commit -qam 'readme' && git push -q origin {source['ref']}"
    )
    assert r.rc == 0, r.stderr
    pushed = host.check_output(f"git -C /srv/git/{name}.git rev-parse {source['ref']}")
    marker = host.check_output("date +%s")
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout
    journal = host.run(f"journalctl -u storagebaby-deploy.service --since @{marker} --no-pager").stdout
    assert "PLAY RECAP" in journal, f"the playbook did not run for a moved checkout:\n{journal}"
    assert_the_role_did_not_restart(host, user, unit, before, "a README commit restarted the service")
    assert state_lines(host)[f"source:{name}"] == pushed


@pytest.mark.order(-1)
def test_a_revoked_token_fails_the_tick_and_leaves_the_service_up(host):
    """The fetch is refused, the unit fails, the journal names the repository, nothing moves.

    Revoked on the server side -- the git server re-reads its credentials file on every
    request -- so the pointer's encrypted file is untouched and put back is one `mv`.
    """
    found = first_pointer(host)
    if not found:
        pytest.skip("no pointer placed on this host")
    _, name, _ = found
    user, unit = f"svc-{name}", f"{name}.service"
    before = unit_marks(host, user, unit)
    state_before = host.file(STATE).content_string
    assert host.run("cp -a /etc/test-git-http/credentials /etc/test-git-http/credentials.harness-backup").rc == 0
    try:
        assert host.run("printf 'revoked\\n' > /etc/test-git-http/credentials").rc == 0
        # No commit is needed to provoke the fetch: the script fetches every pointer on
        # every tick, and the refusal happens there, before the state is even compared.
        marker = host.check_output("date +%s")
        r = host.run(DEPLOY)
        journal = host.run(f"journalctl -u storagebaby-deploy.service --since @{marker} --no-pager").stdout
        assert r.rc != 0, f"the tick survived a revoked token\n{journal}"
        assert name in journal and ("401" in journal or "Authentication failed" in journal), journal
        assert "PLAY RECAP" not in journal, f"the playbook ran after a failed fetch\n{journal}"
        assert host.file(STATE).content_string == state_before, "a failed tick changed the state file"
        assert_the_role_did_not_restart(host, user, unit, before, "a failed fetch restarted the service")
        assert host.run(f"systemctl --user -M {user}@ is-active {unit}").stdout.strip() == "active"
    finally:
        assert host.run("mv -f /etc/test-git-http/credentials.harness-backup /etc/test-git-http/credentials").rc == 0
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout
```

And at the very end of the file:

```python
@pytest.mark.order(-1)
def test_removing_the_pointer_removes_the_fetched_repository(host):
    """Unplacing an external service deletes its checkout under sources/ and its state line.

    Last in the file on purpose: after it the service is unplaced on this VM, and `prepare`
    is what puts the pointer back. The service's units are left as every unplaced service's
    are; the route file goes with the converge like any stale route.
    """
    found = first_pointer(host)
    if not found:
        pytest.skip("no pointer placed on this host")
    owner, name, _ = found
    folder = f"hosts/{owner}/services/{name}"
    r = host.run(
        f"cd {SEEDED} && git rm -rq {folder} "
        "&& git -c user.name=t -c user.email=t@t commit -qm 'unplace the external service' && git push -q origin stable"
    )
    assert r.rc == 0, r.stderr
    r = host.run(DEPLOY)
    assert r.rc == 0, host.run(JOURNAL).stdout
    assert not host.file(f"/var/lib/storagebaby/sources/{name}").exists
    assert f"source:{name}" not in state_lines(host)
    # The documented half of unplacing: the units stay, like every unplaced service's. A
    # cleanup that stopped them would be a contract change and has to fail here first.
    assert host.run(f"systemctl --user -M svc-{name}@ is-active {name}.service").stdout.strip() == "active"
```

- [ ] **Step 2: Integration run (coordinated)**

Run: `MOLECULE_HOST=test-ci mise run test-integration`.
Expected: all four cases green; `test_the_whole_playbook_runs_clean_in_check_mode` still green (the check now also passes over a fetched pointer service).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/molecule/test-ci/tests/test_deploy.py
git commit -m "Measure the deploy path of an external repository: a push, a no-op push, a revoked token, an unplaced pointer

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Documentation

Spec §7 and §10.

**Files:**

- Modify: `README.md` (Layout, Services fixture paragraph, a new "External services" section before "Secrets, in one minute", the deploy sentences)
- Create: `docs/external-service.md`
- Modify: `CLAUDE.md` ("Managing services": the pointer folder; the key list: `registries`; the fixture list; Deployment paragraph already changed in Task 1)
- Modify: `docs/quickstart.md` (a step "Place a repository of your own")
- Modify: `docs/superpowers/specs/2026-10-02-external-services-design.md` (Status line → implemented, plus an "Amendments" section listing whatever changed during Tasks 1–4)

- [ ] **Step 1: README**

Layout list, add after the `hosts/shared/services/<name>/` bullet:

```markdown
- `hosts/<host>/services/<name>/source.yml` — a service whose definition lives in its own
  repository: the pointer names the repository, the branch and the folder, and the host
  fetches it on every deploy tick ("External services" below).
```

Services section, the fixture paragraph: add `ext-echo` — _"and `ext-echo`, a service that lives in its own repository (`tests/fixtures/sources/ext-echo`) and is placed by a pointer, fetched by the VM from a throwaway basic-auth git server and pulling its image from a throwaway private registry, so the external path and the private-image path both run for real."_

New section `## External services` before `## Secrets, in one minute`:

```markdown
## External services

A repository of your own can carry its whole service definition and be deployed by this
platform. The repository holds the contract in a folder — `service.yml`, `quadlet/`,
`config/`, `secrets.sops.yaml`, a README — exactly as a folder under `hosts/` would, and
this repository holds only the pointer:

    hosts/storagebaby/services/<name>/source.yml
      repo: https://github.com/<you>/<repository>
      ref: main
      path: deploy/storagebaby
    hosts/storagebaby/services/<name>/source.sops.yaml     # git_token; registry_user + registry_token for a private image

Placement is still the folder. The deploy tick (`/usr/local/sbin/storagebaby-deploy`)
fetches every pointed repository into `/var/lib/storagebaby/sources/<name>` beside the
platform checkout and converges when any of them moved; the playbook treats the fetched
folder like a service folder. Nothing external passes through this repository's CI — the
external repository tests itself, and the host asserts two things at converge time: the
fetched `service.yml` names the service its folder names, and no two services on the host
claim one loopback port.

The external repository's `secrets.sops.yaml` is encrypted to your operator key **and the
host's age key** (the `&storagebaby` recipient in `.sops.yaml` here), so the external
repository needs a `.sops.yaml` of its own listing both. `docs/external-service.md` is the
page to read from that repository's side.

Access: one fine-grained GitHub token per repository (Contents: read-only, only that
repository), pasted into `source.sops.yaml` as `git_token` with `mise run sops`. The host
presents it over HTTPS through `GIT_ASKPASS`; it is never written to disk in the clear. A
public repository needs no `source.sops.yaml`. A private image needs `registries: [ghcr.io]`
in the external `service.yml` and `registry_user` + `registry_token` in `source.sops.yaml`;
the role writes the service user's `auth.json` from it before the pull. Check when you
create it whether a fine-grained token can read packages on GitHub as it is then; if not,
`registry_token` is a classic token with `read:packages`, which is account-wide — the only
place where the per-repository isolation does not hold.

Unplacing is deleting the pointer folder: the next tick removes the fetched checkout. The
service's units stay, as for any unplaced service.
```

- [ ] **Step 2: `docs/external-service.md`**

```markdown
# Deploying a repository of your own on a StorageBaby host

What the repository needs, and nothing else.

## 1. The folder

Create `deploy/storagebaby/` (any path works; it is named in the pointer) with:

    service.yml          name, port + domain, volumes, secrets, backup, ...  (the ordinary contract)
    quadlet/<name>.container.j2
    config/              optional, copied to /etc/storagebaby/<name>/
    secrets.sops.yaml    if service.yml declares secrets
    README.md

`name` must equal the pointer folder's name on the host. `ansible/roles/service/README.md`
in the StorageBaby repository is the template reference (`volumes.<name>`, `config_dir`,
`port`, `fqdn`, `tz`, ...); `tests/fixtures/sources/ext-echo/` there is a complete example.

## 2. Secrets

Add a `.sops.yaml` at the repository root with the operator key and the host key:

    creation_rules:
      - path_regex: deploy/storagebaby/secrets\.sops\.yaml$
        key_groups:
          - age:
              - <operator public key>      # &jlk in StorageBaby's .sops.yaml
              - <host public key>          # &storagebaby there

then `sops deploy/storagebaby/secrets.sops.yaml` to write the values. The host decrypts
with its own key; the operator edits with theirs.

## 3. The image

Public image: nothing to do. Private image on ghcr.io: add `registries: [ghcr.io]` to
`service.yml`; the token that reads it goes into the pointer's `source.sops.yaml` on the
host side (`registry_user`, `registry_token`). A floating tag with `AutoUpdate=registry`
is picked up by the service user's `podman-auto-update.timer`; a pinned tag changes when
you push a commit that changes it.

## 4. The pointer (StorageBaby repository)

    hosts/storagebaby/services/<name>/source.yml
      repo: https://github.com/<you>/<repository>
      ref: main
      path: deploy/storagebaby

For a private repository: a fine-grained personal access token (resource owner you, only
this repository, Contents: read-only), stored with
`mise run sops hosts/storagebaby/services/<name>/source.sops.yaml` as `git_token`. Commit,
let CI promote `stable`; the next tick fetches and converges.

## 5. Looking at it

On the host: `journalctl -u storagebaby-deploy.service -n 50` (the fetch and the converge),
`storagebaby-svc ps <name>`, `storagebaby-svc logs <name>`. A tick that could not fetch
fails the unit and names the repository; the service keeps running on what it has.
```

- [ ] **Step 3: CLAUDE.md and quickstart**

`CLAUDE.md`, "Managing services" list: after the `hosts/shared/services/<name>/` bullet add

```markdown
- `hosts/<host>/services/<name>/source.yml` — a pointer: the service's definition lives in its own repository (`repo`, `ref`, `path`, all required). The deploy script fetches it into `/var/lib/storagebaby/sources/<name>` and the playbook resolves the pointer to `<path>/service.yml` there; `source.sops.yaml` beside the pointer carries `git_token` (and `registry_user`/`registry_token` for a private image). This repository holds nothing else of such a service. `docs/external-service.md` is the other side; `tests/fixtures/sources/ext-echo` the model repository.
```

In the `service.yml` key list add

```markdown
- **`registries`** — `[ghcr.io]`: registries the service user is logged into before any pull, from the pointer's `source.sops.yaml`. Pointer services only; `test_sources.py` refuses it elsewhere.
```

In the fixture sentence ("The exceptions are the **test-only fixtures** — `tcp-echo`, ..., and `build-echo`, ...") add `ext-echo`, a pointer under both test hosts whose repository is `tests/fixtures/sources/ext-echo`, served to the VM by a basic-auth git server and pulling from a private registry, both run by `prepare.yml`.

`docs/quickstart.md`: add a step `## 8. A repository of your own` with the four commands/paths from `docs/external-service.md` §4 and a link to that page.

- [ ] **Step 4: Spec status and amendments**

Set the spec's `Status:` line to `implemented 2026-10-0X` and add `## 11. Amendments` listing every deviation Tasks 1–4 made (the `json_query` fallback if taken, the `dict_kv` shape chosen, anything the VM taught). If nothing deviated, write "None."

- [ ] **Step 5: Format check and commit**

Run: `mise run fmt-check && mise run test-static`
Expected: PASS.

```bash
git add README.md CLAUDE.md docs
git commit -m "Document external services: the pointer, the repository side, the deploy script

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Whole-branch verification

After Task 5, one coordinated full run on the workstation host too: `mise run test-integration` (default `test-a`, every service plus the fixture), then `MOLECULE_HOST=test-ci mise run test-integration` if the per-task runs did not already cover it. Both green before the branch is offered for merge. Record anything the runs taught in the spec's Amendments.
