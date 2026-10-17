# Cradle: the split

Status: stage 1 implemented 2026-10-17; stages 2–4 open
Date: 2026-10-16
Builds on: `docs/ownership.md` (the authority for every decision here), `docs/architecture.md` (the platform as it is), `2026-10-02-external-services-design.md` (the pointer shape this design generalises)

## 1. Goal

The engine that turns a folder into a running service leaves this repository and becomes
**Cradle**, a repository of its own at `janlucaklees/cradle`, usable by anyone who forks
it. This repository becomes a **host repository**: it says what the machine is and what
runs on it, and holds the service folders that run here. Cradle ships no service of its
own, only the fixtures its tests need.

What this buys: the two things change at different speeds and are tested differently.
Cradle's tests prove that what Cradle says it supports works, once, in a VM. A host
repository's CI is formatting, lint and the promotion of `stable`; everything else is
refused at converge, loudly, on the host. Nothing is proven twice.

This is a programme of four stages, each its own spec and rollout. This document is the
programme in one page and **stage 1 in full**.

| Stage | What                                                                                                                                                                                                                                                    | Behaviour change on the host |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| 1     | **The split.** Move the engine, its tests and its docs to Cradle; this repository becomes a host repository; Cradle gets a migration mechanism and uses it once to rename its own paths on the host                                                     | None beyond the rename       |
| 2     | **Built-ins.** Traefik and the kopia server become folders Cradle places on every host; the host declares backups (repository, replica, or off); client passwords are generated; Traefik's credentials move to the host's secrets; `hosts/shared/` goes | Kopia and Traefik move       |
| 3     | **Ports.** Cradle assigns every loopback port from service and route name; the declaration leaves the service; plain-TCP ports stay declared and collide loudly                                                                                         | Every service restarts once  |
| 4     | **Fixtures.** The real services copied into Cradle's test tree in stage 1 are replaced by one small fixture per mechanism                                                                                                                               | None                         |

Decisions already made (JLK, 2026-10-16), which the stages build on:

- **Cradle is one-machine software.** One instance, one host configuration, never more.
  Nothing on a host carries a name but Cradle's own.
- **Explicit configuration over convention.** A Cradle instance knows the repository,
  branch and path of its host configuration. A host configuration knows the repository,
  branch and path of every service it places. The machine's hostname is never consulted.
- **Cradle tracks its own repository and upgrades itself.** It is a template, not a
  product: fork it, change it, and your host tracks your fork's `stable`.
- **The host repository decides placement**, by holding the folder or a pointer to it.
  A service still ships every fact about itself (`docs/ownership.md` § 1.1).
- **Storage is declared by the host and executed by Cradle**; its role and tests are
  Cradle's.
- **Cradle owns the ingress and the backup mechanism**; the host configuration says how
  the mechanism is used. (Stage 2.)
- **Cradle manages loopback ports.** (Stage 3.)
- **No checker for host repositories.** A placement mistake is an error at converge, not
  a pipeline. "Good software does not break its back to prevent every error; it
  communicates."
- **`stable` branches everywhere**, each moved by its own CI.

Non-goals, held for later: named storages and a default storage (`docs/ownership.md`
§ 4.1), the local-development host profile beyond what stage 2's "backups off" gives it,
the provider model for backups (§ 6.1), a pointer to a folder of several services.

## 2. Shape after stage 1

```
janlucaklees/cradle                              janlucaklees/StorageBaby (this repository)
  ansible/
    playbook.yml                                   hosts/
    roles/{service,host_base,storage}/               StorageBaby/
    migrations/0001-storagebaby-to-cradle.yml          host.yml
  bootstrap.sh                                         secrets/*.sops.yaml
  devtools/                                            services/<name>/        (folder: in-tree service)
  mise.toml                                            services/<name>/source.yml  (pointer: external service)
  tests/                                             shared/
    static/                                            services/traefik/        (until stage 2)
    integration/                                   .sops.yaml
    fixtures/                                      mise.toml           (format, fmt-check, sops, updatekeys, install-hooks)
      host-repo/hosts/{test-a,test-ci}/…           .github/workflows/ci.yml   (fmt-check, promote stable)
      sources/ext-echo/                            docs/runbooks/
  docs/{ownership,architecture,external-service}.md  docs/superpowers/{specs,plans}/   (history stays)
  docs/superpowers/{specs,plans}/   (copies)       README.md, CLAUDE.md  (the host and its services)
  .claude/skills/adding-a-service/
```

Cradle's history starts at the split. This repository keeps its history; the engine's
past is readable here and the specs that record it are copied to Cradle so Cradle's
decision record is complete on its own.

## 3. The loop

A tick fetches three kinds of checkout and converges when any of them moved:

1. **Cradle itself**, at the ref the deploy config names.
2. **The host repository**, at its ref; the host folder is the path inside it.
3. **Every pointed service repository**, at the ref its `source.yml` names, as today.

```
/etc/cradle/deploy.conf                 written by bootstrap, read by every tick
  CRADLE_REPO=https://github.com/janlucaklees/cradle
  CRADLE_REF=stable
  HOST_REPO=git@github.com:janlucaklees/StorageBaby.git   # the deploy key's remote, as today
  HOST_REF=stable
  HOST_PATH=hosts/StorageBaby
```

The deploy script lives in Cradle (`ansible/roles/host_base/files/cradle-deploy`),
is installed to `/usr/local/sbin/cradle-deploy` by `host_base`, and is run by
`cradle-deploy.timer` every five minutes. It updates itself one tick late, the way it does
today: the tick runs the installed script, the converge installs the one the fetch brought.

The playbook is Cradle's and takes the host folder as input:

```
ansible-playbook -i localhost, -e host_dir=/var/lib/cradle/host/hosts/StorageBaby ansible/playbook.yml
```

`host_dir` replaces `storagebaby_repo_root ~ '/hosts/' ~ inventory_hostname` everywhere.
The host's **name**, which kopia client identities (`paperless@StorageBaby`) and the
maintenance mail carry, is the basename of `host_dir`. It is derived from explicit
configuration, not from the machine, and stage 1 keeps it derived so the render stays
identical; a `name:` key in `host.yml` is a later sharpening if ever wanted. The
inventory shrinks to one local entry; `--limit` goes.

State on the host moves under Cradle's name:

```
/etc/cradle/{age.key,age.pub,deploy_key,deploy_key.pub,deploy.conf}
/etc/cradle/<service>/                    per-service config dirs, traefik/dynamic.d, traefik/certs
/var/lib/cradle/{cradle,host,sources/<name>,deploy.state}
/opt/cradle/maintenance/
/usr/local/sbin/{cradle-deploy,cradle-svc,podman-as,podman-secret-sync}
cradle-deploy.{service,timer}             storage-maintenance.* keep their names: they are not Cradle's name
10-cradle-hosts.conf                      the host-gateway drop-in
```

Not Cradle's to rename: anything a host declares as a data path. StorageBaby's
`storage_roots.fast` is `/var/lib/storagebaby/fast` and stays exactly that; it is the
administrator's path, not the engine's.

## 4. The host repository contract

Cradle defines it the way it defines the service folder, and both are plugins to Cradle
in the sense of `docs/ownership.md` § 1.1.

- `hosts/<name>/host.yml` — the administrator's instrument, unchanged in shape.
- `hosts/<name>/secrets/*.sops.yaml` — the host's secrets, unchanged.
- `hosts/<name>/services/<name>/` — placement: a folder with `service.yml` (in-tree), a
  symlink to one (one folder, several hosts), or a folder holding only `source.yml` (a
  pointer: `repo`, `ref`, `path`). An in-tree folder is the pointer's degenerate case,
  and stage 1 does not unify the two spellings.
- `hosts/shared/services/` — placed on every host of the repository. Stays in stage 1
  because Traefik lives there; stage 2 empties it and removes it.

The `hosts/` layer stays on purpose: one host repository can describe several machines,
each of which points at it with its own `HOST_PATH`.

## 5. Migrations

Cradle gains `ansible/migrations/NNNN-<name>.yml`: task files the playbook includes
first, in order, on every converge. A migration is idempotent by construction — it
checks for the old state and does nothing when it is gone — so there is no state file to
keep and a half-applied migration finishes on the next tick. Stage 1 ships the first:

**`0001-storagebaby-to-cradle.yml`** moves what the pre-split engine put on the host:
`/opt/storagebaby/maintenance` → `/opt/cradle/maintenance`; removes
`storagebaby-deploy.{service,timer}`, `/usr/local/sbin/storagebaby-{deploy,svc}`, the
`storagebaby-render` udev rule and every `10-storagebaby-hosts.conf` drop-in (their
Cradle-named successors are rendered by the same converge); and leaves nothing under the
old names.

Two moves cannot wait for Ansible, because the tick needs the age key and the deploy
config before the playbook runs: `/etc/storagebaby` → `/etc/cradle`, and
`/var/lib/storagebaby/sources` → `/var/lib/cradle/sources`, with
`/var/lib/storagebaby/repo` (this repository's checkout) becoming `/var/lib/cradle/host`
and Cradle's own checkout appearing beside it as `/var/lib/cradle/cradle`.
`/var/lib/storagebaby/deploy.state` is **deleted**, not moved — this sentence had it
moving beside `sources`, which was wrong; the code was right at cutover, and deleting it
is what makes the first Cradle tick find no prior state and converge unconditionally.
**Bootstrap does those**, as its one adoption step: run on a host that has
`/etc/storagebaby` and no `/etc/cradle`, it moves the trees, deletes the old deploy
state, writes the new deploy config and disables the old timer. On a fresh host the step
is a no-op.

## 6. Bootstrap

`bootstrap.sh` is Cradle's. It takes the five deploy-config values as arguments, performs
the adoption step above if applicable, then does what it does today — repositories, one
`pacman -Syu`, packages, keys — and **clones Cradle before anything else and runs the
deploy script from that checkout.** The embedded copy of the script and the static test
that held the two byte-identical go away.

## 7. Tests

**Everything moves to Cradle.** The static suite renders Cradle's fixture host repository
and checks the contract against it. The VM suite converges a fixture host. Static checks
whose subject was _StorageBaby's placement_ — port uniqueness, routes under declared
zones, offered registries, secret recipients — keep running in Cradle against the
fixtures, and are no longer run against this repository's tree: those mistakes are
refused at converge, as decided.

**Fixtures in stage 1 are the real services, copied.** `tests/fixtures/host-repo/` is a
complete host repository holding `hosts/test-a` and `hosts/test-ci` as they are today,
with `paperless`, `kopia`, `yuzukam` and the rest of what the test hosts place copied in
as real folders (test-a) and symlinks (test-ci), plus `tcp-echo`, `build-echo` and
`ext-echo` unchanged. This is what gives Cradle a green CI and a `stable` on day one.
Stage 4 replaces the copied services with minimal fixtures; stage 1 does not.

**Host repository CI** (this repository): `fmt-check`, then fast-forward `stable`. No
VM, no render. Cradle's CI is today's CI, moved.

**The two `stable` branches can race.** A host commit that uses a contract key Cradle's
`stable` does not have yet promotes on formatting alone, errors at converge, and retries
every five minutes until Cradle's `stable` catches up. Accepted; written down here so it
is not rediscovered at 02:00.

## 8. Docs and skills

| Goes to Cradle                                                                                                            | Stays here                                                                                    |
| ------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `docs/ownership.md` (§ 7 items 13–16 stay here), `docs/architecture.md`, `docs/external-service.md`, `docs/quickstart.md` | `docs/runbooks/`, the StorageBaby-specific parts of the README and CLAUDE.md                  |
| The role READMEs, `.claude/skills/adding-a-service`                                                                       | A short README and CLAUDE.md for a host repository: this host, its services, how to place one |
| `docs/superpowers/{specs,plans}` as copies                                                                                | The same, as the originals                                                                    |

**Ownership doc edits that stage 1 makes** (the rest come with their stages): § 2 names
Cradle as the platform; the Platform table gains "the migration of its own state on a
host"; § 7 items 13–16 move to a short "known drift" list in this repository's README.

## 9. Cutover on StorageBaby

The stage 1 rollout is one runbook, written with the plan:

1. Cradle's CI is green and `stable` exists. This repository's split commit is on
   `stable`. From this point the old deploy tick fails every five minutes (it finds no
   playbook), which is harmless and expected.
2. On the host: run Cradle's `bootstrap.sh` with the five values. It adopts
   `/etc/storagebaby` and `/var/lib/storagebaby`, writes the deploy config, installs
   `cradle-deploy.timer`, disables the old timer, and runs the first tick.
3. The first converge runs migration 0001, re-renders every unit against `/etc/cradle/…`,
   and restarts every service once. Nothing else changes.
4. Verify: every service answers, `cradle-svc ps <name>` for each, the nightly maintenance
   unit still points at `/opt/cradle/maintenance`, the old names are gone from the host.

**What happened, 2026-10-17:** bootstrap ran `--local` (step 2 above did not start the
timer itself), and the converge that mattered — one `ansible-playbook` run, not two — was
carried by `cradle-deploy.timer`'s own first tick, started by the operator at 16:59:23
CEST, after a concurrency hazard between a hand-run converge and that same tick was found
and ruled on. The tick converged `changed=88 failed=0`, the next tick five minutes later
was a no-op, and the StorageBaby split merge's own converge, picked up by the host about
sixteen minutes after the merge, was `changed=0` — the render-parity bullet below,
proven on the live host, not only in `render_only`. `docs/runbooks/2026-10-17-cradle-cutover.md` is the full record: every
`--check` and converge recap, the blocker and the ruling, and the verification list.

## 10. Acceptance

- **Render parity.** `render_only` output for `hosts/StorageBaby`, produced by this
  repository before the split and by Cradle after it, differ only by the renames listed
  in § 3 (`storagebaby` → `cradle` in those paths and unit names). The diff after
  applying that substitution is empty. This is the one check that proves the split
  touched nothing.
- Cradle's static and VM suites pass; this repository's CI passes; both `stable`
  branches move.
- The live host converges green on the first Cradle tick and is idempotent on the second.

## 11. Open until the plan

- Whether Cradle's fixture host repository places all of test-a or trims it: trimming is
  stage 4's job, but a service that costs nothing to drop may go earlier.
- The exact argument syntax of `bootstrap.sh`.

Surfaced by the cutover, 2026-10-17:

All four were closed by the review's fix wave, 2026-10-17:

- **No mutual exclusion in `cradle-deploy`.** Two ticks of the _timer_ cannot overlap:
  the unit is a oneshot, systemd merges a start job that elapses while it runs into the
  running one, and that merged job re-elapses when it finishes — this cutover's 11 min 19 s
  converge is exactly what the journal shows (the 17:04:23 timer merged, re-elapsed at
  17:10:42). The real exposure is a **hand-run `cradle-deploy` or `--check` beside the
  timer's tick**, and a hand converge on a `deploy_timer: true` host whose timer is still
  disabled — the converge's own "Deploy timer state" task then starts it mid-run.
  `sync_checkout`'s `git reset --hard` + `git clean -fdx` landing in the middle of the
  other run's `ansible-playbook` is the damage. Closed by a `flock -w 900` on fd 9 against
  `/run/cradle-deploy.lock` at the top of the script, before the first fetch: it **waits**
  rather than fails, so a pre-flight blocks behind a tick and a tick fired mid-hand-converge
  waits and afterwards no-ops on the state file. The `--check` path `exec`s
  ansible-playbook, which inherits fd 9, so the lock covers the whole pre-flight.
- **Bootstrap left `/etc/cradle` at `0700`** until the first converge set it to `0711`,
  and `install -d -m` re-applied that on every re-run of a documented-idempotent script
  with no converge behind it. Closed: bootstrap creates it `0711`, held equal to the
  role's mode by `tests/static/test_bootstrap.py`.
- **Bootstrap's `$(hostname)` fails on a host without `inetutils`** — closed, `$(uname -n)`.
- **The converge-via-timer rule** stands as the operating rule, and the lock is what now
  makes a breach of it safe rather than merely documented.
