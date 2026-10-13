# Renaissance as the first external service, and the test harness that lets it land

Status: implemented 2026-10-13
Date: 2026-10-12
Builds on: `2026-10-02-external-services-design.md` (the mechanism this is the first real use of), `HANDOVER.md` on branch `external-services` (the checklist this follows)

## 1. Goal

By the end of the run, the Renaissance repository carries its own deploy contract under
`deploy/storagebaby/`, this repository carries the pointer and the host's credentials for
it, and the host `StorageBaby` runs the application at `https://renaissance.klees.io`
from the images its own CI publishes. Nothing is set up by hand on the host: the deploy
tick fetches, the playbook converges.

Three things stand between the branch and that state, and this spec covers all three:

1. **The integration suite cannot be trusted to go green in CI.** The last push to
   `master` (592e31d) sat 86 minutes inside prepare's post-bootstrap reboot until the
   90-minute job limit killed it, so `stable` is one commit behind `master` and nothing
   on this branch reaches the host until the suite passes. Section 3.
2. **Both CI pipelines run their slow suite on every push**, including a push that
   changes only deployment config or docs. A pointer commit here, or a deploy-folder
   commit there, would cost an integration run or an e2e run for nothing. Section 4.
3. **The deployment itself**: the Renaissance contract, the pointer, the host's two
   credential files, and the first live run of the external-services mechanism.
   Sections 5 to 7.

Decisions JLK made in chat (2026-10-12):

- Merge `external-services` into `master`, commit and push to both repositories, create
  and merge branches, run tests as needed. Work on the host is allowed, carefully.
- The two GitHub tokens are JLK's to create; he hands them over in files under
  `~/.config/storagebaby/`, never in chat.
- Mail is out of scope: the application may fail to send. The legacy Flask UI is out of
  scope. No backup block: this is a prototype for testing, without sensitive data.
- Admin account: `email@janlucaklees.de`, abbreviation `ADM`, generated password.
- Floating `:latest` images with the platform's auto-update; the application migrates
  its own database on every container start so an auto-updated image still migrates.
- The pointer tracks Renaissance's `stable` branch, so the deploy contract moves only
  with code that passed the full Renaissance suite.
- Spec and plan are committed.
- "One test suite at a time" means the workstation's Molecule VM. CI's `test-ci` runs on
  a GitHub runner and does not collide with a local run.

Non-goals: the verifier's own running time (section 3.5), the kopia pairing for an
external service, mail delivery, the legacy UI, a `ProxyFix` for absolute links the
Flask side renders, and any change to the service contract.

## 2. Where the integration time goes

Measured, not guessed. The last green CI run on `master` (37642679266, 2026-10-07) and
two sessions' local runs tonight:

| Step                                       | CI (`test-ci`, 5 GiB / 2 vCPU)               | Workstation (`test-a`) |
| ------------------------------------------ | -------------------------------------------- | ---------------------- |
| devtools image build                       | 75 s                                         | cached                 |
| create (download cached, DHCP, SSH)        | ~60 s                                        | ~60 s                  |
| bootstrap in prepare                       | 31 s                                         | similar                |
| **reboot after bootstrap**                 | **58 s when it works; 86 min when it hangs** | same                   |
| rest of prepare (disks, secrets, registry) | ~3 min                                       | ~3 min                 |
| converge                                   | ~6 min (test-ci)                             | ~10 min                |
| idempotence                                | 2 min                                        | ~3 min                 |
| **verify**                                 | ~15 min on master, **40 min on this branch** | **56 to 58 min**       |
| total                                      | 34 min on master, ~65 on this branch         | ~90 min                |

So the pre-built image JLK asked for is a reliability fix, not a speed fix: it removes
the one step that hangs, and a minute or two with it. The time is in the verifier, and
on this branch specifically in the deploy-path cases that each run a whole deploy tick.
That is a separate piece of work (3.5) and not tonight's.

Two more flakes the same change removes as a side effect, both observed tonight:

- `create.yml`'s cached-image task sends a conditional request for the stock cloud image
  on every run even when the 578 MB file is present (Ansible's `get_url` with
  `force: false` still asks the server whether the file moved), and one run died there
  on a DNS blip inside the devtools container.
- The reboot exists because the cached cloud image lags the repositories, so bootstrap's
  `pacman -Syu` replaces the kernel and the running one can no longer load modules. A
  base image upgraded within the week makes that upgrade a no-op.

## 3. The pre-upgraded base image

### 3.1 Shape

`create.yml` gains one stage before the overlay disks are made. It keeps a second file
beside the stock cloud image:

    /var/lib/libvirt/images/storagebaby/arch-cloudimg.qcow2           the stock image, downloaded once
    /var/lib/libvirt/images/storagebaby/arch-cloudimg-prepared.qcow2  the stock image after a full upgrade

When the prepared image is missing or older than seven days, the stage builds it: a
throwaway libvirt domain `storagebaby-base-builder` boots an overlay of the stock image
with a cloud-init seed whose `runcmd` upgrades the system and then cleans up after
itself, and powers off. The stage waits for the domain to reach `shut off`, flattens the
overlay into `arch-cloudimg-prepared.qcow2` with `qemu-img convert` (so the prepared
image has no backing file and the stock image can be replaced underneath it), and
removes the builder's domain, overlay and seed. Every test platform then overlays the
prepared image instead of the stock one.

The builder's cloud-init does, in this order:

1. `pacman -Syu --noconfirm` -- the whole point. Nothing else is installed: bootstrap
   still installs its seven packages in prepare, exactly as it does on a real host, and
   the roles still install theirs. The image is the stock image a week newer, not a
   pre-provisioned host.
2. `pacman -Scc --noconfirm` so the package cache is not carried into every overlay.
3. `cloud-init clean --logs` so the next boot, with the test platform's own seed, is a
   first boot: new instance-id, the platform's hostname and SSH key, fresh host keys.
4. `truncate -s 0 /etc/machine-id` so each VM gets its own machine-id on first boot.
   Without this every overlay shares the builder's, and systemd-networkd derives its
   DHCP client identifier from it: two test VMs would ask for the same lease.
5. `poweroff` through cloud-init's `power_state` module, which runs after `runcmd`.

The stock image is downloaded only when the file is absent (a `stat`, then `get_url`
guarded by it), never revalidated. It is otherwise touched only when the prepared image
is rebuilt and the stock image is older than 30 days, in which case it is re-downloaded
first so the weekly upgrade starts from something recent.

The reboot task in `prepare.yml` stays as it is. It compares the installed kernel with
the running one and reboots only on a mismatch; with a prepared image that is current
to the week, the mismatch happens only when a kernel lands between the build and the
run, which is the one case the reboot is still right for. The hang it can turn into is
not fixed here, only made rare; 3.5 notes it.

### 3.2 Files

- `tests/integration/molecule/test-ci/create.yml` -- the stage, as tasks imported from
  `tests/integration/molecule/test-ci/tasks/base-image-stage.yml` so the stage can be run on
  its own with a wrapper playbook while the Molecule VM is busy with another run.
- `tests/integration/molecule/templates/base-image-user-data.j2` -- the builder's
  cloud-init. `domain.xml.j2` is reused with `storage_disk_count: 0` for the builder.
- `tests/integration/molecule/test-ci/destroy.yml` -- unchanged: the prepared image is a
  cache, not a platform artifact, and `mise run test-clean` leaves it like the stock image.
- `mise.toml` -- a `test-base-image` task that rebuilds the prepared image on demand
  (delete and rebuild), for the day a kernel lands mid-week.
- `.github/workflows/ci.yml` -- see 3.3.
- `tests/README.md` and the root README's test section -- the two files and when they
  are rebuilt.

`tests/static/test_lint.py` syntax-checks every Molecule playbook; an imported task file
is checked through `create.yml`. No apostrophes inside `shell:` blocks, comments included.

### 3.3 In CI

The runner is fresh every time, so the two images are cached across runs with
`actions/cache/restore` before `mise run test-integration` and `actions/cache/save`
after it with `if: always()` -- the explicit pair rather than `actions/cache`, because
the default action saves only when the job succeeds, and a timed-out job is the one that
most needs its image kept. The key is the ISO week plus a hash of the builder's
user-data and task file; a `restore-keys` prefix falls back to the previous week's
images, which the seven-day rule then rebuilds from the cached stock image rather than
from the network.

The images directory is created runner-owned before the restore so the cache can be
written into it; the devtools container runs as root and creates world-readable files,
which is what the save step needs.

Expected effect on the job: no reboot, the stock download replaced by a cache restore of
roughly 1.3 GB on a hit, and a few minutes of build on a miss once a week. The 90-minute
budget stays.

### 3.4 Verification

- `mise run test-static` green (lint, parse).
- The stage on its own, while the Molecule VM is busy: a wrapper playbook under the
  scratchpad imports `tasks/base-image-stage.yml` with the create play's vars; the result is a
  prepared image that boots with a test seed and reports `pacman -Qu` empty.
- One full `mise run test-integration` with the default `test-a` on this branch, which is
  also the handover's open item 2. Expected: prepare's reboot task reports `skipping`,
  everything else as before.
- CI on the merge: the `integration` job green, with the reboot task skipped in its log.

### 3.5 Not in this spec, recorded for the next session

- The verifier's hour. The deploy-path cases in `test_deploy.py` each run a full
  `systemctl start storagebaby-deploy.service`; on this branch they are the bulk of the
  40-minute CI verify. Sharing one tick between cases, or measuring the tick once and
  asserting on its journal, is the lever.
- `ansible.builtin.reboot` hanging past its `reboot_timeout` on the runner. Seen once;
  the prepared image makes the task a no-op, so it is not diagnosed here.
- The role pulls `postgres:17-alpine` once per service user, because rootless means one
  image store per user. A shared additional image store is the lever, and it is a
  platform change, not a test-harness one.

## 4. Skipping the slow suite when nothing it tests changed

Both pipelines get the same shape: a first job lists the files the push changed and
decides whether the slow suite is needed; the slow jobs run on that decision; the
promote job's condition is spelled out in full so a skipped suite promotes only when the
decision said it could be skipped, and a failed or cancelled one never does.

The decision is made with `git diff --name-only <before> <sha>` after a `fetch-depth: 0`
checkout, not with a third-party action. An all-zero `before` (the first push of a
branch) and a diff that fails for any reason both count as "everything changed", so the
only way to skip is a clean diff whose every path matches the skip list. A push that
mixes code and config runs everything. Both repositories keep their single `push` trigger.

Why not `paths-ignore` on the workflow: a second workflow with the complementary `paths`
filter would also run on a mixed push and promote `stable` past an untested commit.
One workflow, one decision.

### 4.1 StorageBaby

Paths that skip `integration` (static always runs):

    hosts/StorageBaby/host.yml
    hosts/StorageBaby/secrets/**
    hosts/StorageBaby/services/*/source.yml
    docs/**
    **/*.md

Everything under a service folder that the test hosts symlink still runs integration; a
pointer folder is not symlinked and is only resolved on the real host. `promote` requires
`!cancelled()` and then either both jobs succeeded, or the decision was "skip" and
`integration` is `skipped` while `static` succeeded.

The filter lands in the same push as the merge, so the pointer commit that follows is the
first to use it.

### 4.2 Renaissance

Paths that skip `static` and `e2e`:

    deploy/**
    docs/**
    **/*.md

`promote` requires `!cancelled()` and then either both succeeded, or the decision was
"skip" and both are `skipped`. The comment in `ci.yml` that warns against `always()` is
rewritten to explain the spelled-out condition it now carries.

`docker-image.yml` gets `paths-ignore` for the same three patterns on its `stable`
trigger: a config-only promotion then moves `stable` without rebuilding images that
cannot have changed. This one is safe as a path filter because nothing downstream of
the image build gates on it.

## 5. The Renaissance deploy contract

`deploy/storagebaby/` in the Renaissance repository, the shape of
`tests/fixtures/sources/ext-echo/` here, with the pod shape of `paperless`.

### 5.1 `service.yml`

    name: renaissance
    port: 8300
    domain: renaissance.klees.io
    volumes:
      database: { class: fast, owner: 70 }
    config:
      database_name: renaissance
      database_user: renaissance
      admin_email: email@janlucaklees.de
      admin_abbreviation: ADM
    secrets: [database_password, secret_key, admin_password]
    backup: none
    registries: [ghcr.io]

`8300` is free on the host's loopback (checked against `ss -ltn`); the host asserts it
at converge. `klees.io` is in the host's `cert_zones`, so the full hostname is allowed
and the wildcard certificate already covers it. `owner: 70` is `postgres` in the alpine
image, read off the image as the role's README asks.

### 5.2 Units

One pod, four containers, the pod publishes the one port:

    renaissance.pod.j2             PublishPort=127.0.0.1:8300:80
    renaissance-proxy.container.j2    docker.io/library/nginx:stable-alpine, the split
    renaissance-app.container.j2      ghcr.io/janlucaklees/renaissance:latest, gunicorn on 5000
    renaissance-frontend.container.j2 ghcr.io/janlucaklees/renaissance-frontend:latest, node on 3000
    renaissance-database.container.j2 docker.io/library/postgres:13-alpine

**The proxy exists because of the platform's route model.** The frontend's generated API
client calls `/api/...` on its own origin, and the platform routes one hostname to one
port with no path rule. So the pod needs the split the old compose file did in Traefik
labels: `/api/` to the Flask backend, everything else to SvelteKit. The nginx config that
already lives at `docker/services/frontend-proxy/nginx.conf` in the repository does
exactly that and moves to `deploy/storagebaby/config/nginx.conf` with its upstreams
changed to the pod's loopback (`127.0.0.1:5000`, `127.0.0.1:3000`), mounted read-only
through `config_dir`. `X-Forwarded-Proto` is passed through from Traefik rather than set
from nginx's own `$scheme`, which inside the pod is always `http`.

**The application container** runs, on every start:

    sh -c 'set -e; renaissance database init; alembic upgrade head; exec gunicorn -b 0.0.0.0:5000 "renaissance:create_app()"'

`database init` creates the schema, stamps alembic `head` and creates the admin user on
an empty database, and prints "already initialized" and exits 0 on any later start. It
is non-interactive only when all three `ADMIN_*` variables are set; the password comes
in as `Secret=admin_password,type=env,target=ADMIN_PASSWORD`, the other two from
`service.config`. `alembic upgrade head` is a no-op right after `init` and the migration
step on every later start, which is what makes a floating `:latest` with
`AutoUpdate=registry` safe for a database-backed application. `set -e` makes a start
against a database that is not up yet fail, and `Restart=always` retries it five
seconds later; the unit is also `After=` the database unit.

Environment: `DB_HOST=127.0.0.1`, `DB_PORT=5432`, `DB_NAME` and `DB_USERNAME` from
config, `DB_PASSWORD_FILE=/run/secrets/database_password`,
`SECRET_KEY_FILE=/run/secrets/secret_key` (the config module reads `<NAME>_FILE`),
`FLASK_APP=renaissance`, `FLASK_ENV=production`, `DEBUG=0`, `TESTING=0`, `TZ`. No
`MAIL_*`: sends will fail, by decision.

**The frontend container**: `ORIGIN=https://renaissance.klees.io`,
`BACKEND_URL=http://127.0.0.1:5000` (its server-side client), `PORT=3000`.

**Health**, one per container, as the contract requires: nginx with its own `curl` against
`/login`; the app with `python3 -c` and `urllib` against `http://127.0.0.1:5000/login`
(the slim Python image has neither curl nor wget), with `HealthStartPeriod=120s` to
cover init and migrations; the frontend with busybox `wget --spider` against `/login`;
the database with `pg_isready`. `HealthOnFailure=kill` and `Restart=always` everywhere.

### 5.3 Secrets

`.sops.yaml` at the Renaissance root with the `&jlk` and `&storagebaby` public keys from
this repository's `.sops.yaml`, one rule for `deploy/storagebaby/secrets\.sops\.yaml$`.
The file holds `database_password`, `secret_key` and `admin_password`, generated with
`openssl rand` into a temporary plaintext file in the scratchpad, encrypted with
`sops --encrypt`, the plaintext shredded. No value is ever printed. The admin password is
handed to JLK only by telling him where it is: `sops --decrypt` on the file, which is his.

### 5.4 README

`deploy/storagebaby/README.md`: what runs, the one route, where the admin credentials
live, and the two decisions (no mail, floating images that migrate on start).

## 6. The pointer and the host's credentials

In this repository, on `master` after the merge:

    hosts/StorageBaby/services/renaissance/source.yml
      repo: https://github.com/janlucaklees/renaissance
      ref: stable
      path: deploy/storagebaby
    hosts/StorageBaby/host.yml                     + registries: [ghcr.io]
    hosts/StorageBaby/secrets/sources.sops.yaml    renaissance: { token }
    hosts/StorageBaby/secrets/registries.sops.yaml ghcr.io: { user: janlucaklees, token }

Both images are private (an anonymous manifest request answers 401), so the registry
login is required. The tokens are read from JLK's files with `sops set` and a command
substitution, never echoed, and the files are shredded afterwards. The static suite
checks the pointer, the host's `registries` and that an offered registry has a login.

The pointer commit is pushed only once both sops files hold real values: a pointer with
no token makes the tick fail on the fetch, loudly and without converging anything, which
is correct behaviour but a wasted tick.

## 7. Sequence, and what each step proves

1. Base-image stage built and tested standalone; `test-static` green; spec and plan
   committed on `external-services`.
2. Local `test-a` on the branch when the VM is free (handover item 2). Green, with the
   reboot skipped.
3. Merge `external-services` into `master` with the CI path filter, push. CI: static,
   integration with cached images, promote. If `integration` times out again, re-run it.
4. `stable` moves; the host's old ansible-pull tick pulls it and the `host_base` role
   installs the new deploy script and unit. The following tick is the first with the new
   script: no state file yet, so one full converge of every existing service that must
   report `changed=0` for all of them (handover § 4, step 10). Watched in the journal.
5. Renaissance: deploy folder, `.sops.yaml`, secrets, CI filter and the image workflow's
   path filter, committed on a branch from a fresh worktree (JLK's checkout has
   uncommitted work), merged to `master`, pushed. CI runs the full suite once (code
   under `.github/` changed), promotes, builds images.
6. Tokens into the two sops files; pointer, `host.yml`, `test-static`; commit and push.
   CI runs static only and promotes. The host's next tick logs
   `external repository for renaissance: ...`, fetches, converges the new service.
7. `storagebaby-svc ps renaissance`, `curl -sI https://renaissance.klees.io/login`, a
   browser login with the admin account, and `journalctl -u storagebaby-deploy.service`
   showing a no-op tick afterwards.
8. The external-services spec gets its amendments from what the real run taught; the
   handover file is updated or retired.

## 8. Risks named up front

- The first new-script tick converges every service on a host that is production for
  everything else JLK runs. Mitigation: the same playbook has run nightly on this host
  for a week; the state file is the only new thing; the tick is watched live.
- A fine-grained token may not read ghcr packages. JLK checks the token UI when creating
  it; the fallback is a classic token with `read:packages`.
- The Renaissance e2e suite is "in a not so well working state" (JLK). The last four CI
  runs were green. A red run is fixed, not re-run until green, and reported.
- `ansible.builtin.reboot` can hang past its timeout; made rare, not fixed.
