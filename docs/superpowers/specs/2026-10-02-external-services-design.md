# External services: deploying a service from its own repository

Status: implemented 2026-10-07
Date: 2026-10-02
Builds on: `2026-09-21-gitops-podman-platform-design.md` (the platform), `2026-09-23-phase-3-pods-and-backups-design.md` (the service contract as built), `2026-09-25-phase-4-storage-and-ftp-design.md` (the deploy path as built)

## 1. Goal

A repository of JLK's own carries everything needed to run it on a StorageBaby host --
the service contract, its templates, its config, its encrypted secrets -- and this
repository only says _where_ it runs and how the host reaches it. Once a repository is
placed, a push to the branch the host tracks is deployed by the next deploy tick, with no
commit here and no round through this repository's CI. The host-side mechanics (users,
secrets, quadlets, Traefik, backups) are the same `service` role as for every in-tree
service; nothing is translated and nothing is duplicated.

Decisions already made (JLK, 2026-10-01 and 2026-10-02):

- The external repository owns the whole service definition, secrets included. This
  repository holds placement and the credentials the _host_ needs to reach the repository
  and its images -- those are deploy infrastructure, not the service's secrets.
- Live fetch, not pinning: nothing external is gated by this repository's tests. The
  external repository brings its own tests if it wants any.
- Hand-selected: a repository is deployed because a pointer folder for it was committed
  here, never because something discovered it.
- Repositories and images may be private. Access is a fine-grained GitHub token per
  repository, so a leaked token opens one repository and not the account.
- One service per external repository. A repository that needs several is a later change.
- The external repository uses the existing contract verbatim, in a subfolder.
- Private images are in scope, with their test.
- (2026-10-07, after `docs/ownership.md` landed) **The host owns both credentials**, the
  way it owns storage classes and certifiable domains: the git tokens and the registry
  logins live in the host's own secrets directory, `host.yml` declares which registries
  the host offers, and a service only declares which registries it needs. A service asking
  for a registry its host does not offer is refused, loudly. The pointer folder holds the
  pointer and nothing else.

Non-goals: tags or commit SHAs as refs (branches only), several services from one
repository, a webhook or push trigger, a removal flow beyond dropping the fetched
checkout, review gates for repositories that are not JLK's, and the conversion of
`yuzukam` -- that is the first real use and an operator follow-up (section 10), not part
of this change.

## 2. Shape

```
hosts/<host>/services/<name>/
  source.yml          repo, ref, path -- the pointer; nothing else of the service lives here

hosts/<host>/host.yml
  registries: [ghcr.io]          the registries this host can log into (optional, default none)

hosts/<host>/secrets/
  sources.sops.yaml              <name>: { token }        one git token per pointed service
  registries.sops.yaml           <registry>: { user, token }   one login per offered registry

<the external repository>/<path>/
  service.yml         the contract, unchanged
  quadlet/*.j2
  config/
  secrets.sops.yaml   encrypted to the operator key and the host's age key
  README.md
```

A folder under `hosts/<host>/services/` holds either a `service.yml` (an in-tree service,
as today) or a `source.yml` (a pointer), never both; a static test holds that. Placement
keeps meaning the folder: a pointer under `hosts/shared/services/` runs on every host, a
pointer under a host folder on that host only, and a test host places a pointer with a
real folder of its own like the other fixtures.

`source.yml`, all three keys required, no defaults:

```yaml
repo: https://github.com/janlucaklees/yuzukam # https only; the host authenticates with the token
ref: main # the branch the host tracks
path: deploy/storagebaby # folder in that repository holding the contract
```

The credentials are the host's (`docs/ownership.md` § 2: the administrator owns what a
host offers; § 4.2 is the same split for certificates). Two files under
`hosts/<host>/secrets/`, both nested sops documents -- sops encrypts the leaf values and
keeps the structure, and the role already decrypts to JSON:

```yaml
# sources.sops.yaml -- keyed by the pointer folder's name; one repository per service, so
# one token per service, and a leaked token still opens one repository only.
yuzukam:
  token: github_pat_...

# registries.sops.yaml -- keyed by the registry host, exactly as a service names it.
ghcr.io:
  user: janlucaklees
  token: ghp_...
```

`host.yml` declares the offer: `registries: [ghcr.io]`, optional, default none. Every
registry it lists has an entry in `registries.sops.yaml` (key names are plaintext in a
sops file, so a static test holds that), and a service may only name a registry its host
offers. A public repository needs no entry in `sources.sops.yaml`; a host with no private
images needs neither `registries` nor the file. Both files sit under `hosts/<host>/` and
are covered by the existing `.sops.yaml` path rules and recipients test; nothing in
`.sops.yaml` changes. A pointer under `hosts/shared/services/` reads its token from each
host's own `sources.sops.yaml`.

The external repository's `secrets.sops.yaml` is decrypted by the host with its own age
key, so the external repository's `.sops.yaml` lists the operator key and the placing
host's key. Both are public keys; the host key is the `&storagebaby` anchor in this
repository's `.sops.yaml`. A test host overrides it the way it overrides every service's
secrets today, `hosts/<host>/secrets/<name>.sops.yaml`, generated in prepare.

One new optional `service.yml` key, `registries`: the registry hosts the service's images
are private on. The role logs the service user into each of them from the host's
`registries.sops.yaml` before any image is pulled. Any service may declare it, external
or in-tree; what it may not do is name a registry the host does not offer -- that fails
the converge with a message, the way a route under a zone outside `cert_zones` is refused.

## 3. The deploy script

`storagebaby-deploy.service` runs `/usr/local/sbin/storagebaby-deploy` instead of
`ansible-pull`. `ansible-pull` could watch exactly one repository and converge only when
that one checkout moved; a host that also follows external repositories needs both halves
generalised, and the script is small enough to own them outright. Each tick:

1. Read `/etc/storagebaby/deploy.conf` (`REPO_URL`, `BRANCH`, `EXTRA_ARGS`, as bootstrap
   writes it today). Clone or fetch this repository into `/var/lib/storagebaby/repo` and
   check out `origin/$BRANCH`, detached, over SSH with the host's deploy key exactly as the
   unit does now. Keep the `ExecStartPost` check that `hosts/<hostname>/` exists.
2. List this host's pointers: every `source.yml` under `hosts/<hostname>/services/*/` and
   `hosts/shared/services/*/`. For each, clone or fetch into
   `/var/lib/storagebaby/sources/<name>` and check out `origin/<ref>`, detached. The
   checkout is always a hard reset to the remote branch: the host never has local changes
   there, and a force-push upstream must not wedge the tick.
3. Authenticate over HTTPS with the pointer's token from
   `hosts/<hostname>/secrets/sources.sops.yaml`, decrypted once per tick with the host's
   age key. The token reaches git through `GIT_ASKPASS` with `credential.helper` disabled,
   so it is never written into a remote URL or `.git/config` and never lands on disk in
   the clear. A pointer with no entry clones anonymously, which is what a public
   repository needs.
4. Delete `/var/lib/storagebaby/sources/<name>` for every `<name>` that no longer has a
   pointer, so the sources directory mirrors the placement.
5. Compare the HEAD of the repository and of every source with
   `/var/lib/storagebaby/deploy.state`, the SHAs recorded by the last converge that ended
   `failed=0`. All equal: log one line and exit 0. Anything different, or no state file:
   run `ansible-playbook -i ansible/inventory/hosts.yml --limit <hostname> $EXTRA_ARGS
ansible/playbook.yml` from the repository checkout, and write the state only when it
   succeeds.

The state file changes one thing for in-tree services too, and it is an improvement: a
converge that failed is retried on the next tick, where `ansible-pull --only-if-changed`
left a failed deploy alone until the next commit. A fetch that fails (network, a revoked
token, a deleted branch) fails the unit loudly and leaves the state untouched, so nothing
is torn down and the journal says which repository and why.

`/var/lib/storagebaby/sources` itself is root-only (0700): it holds decryptable secrets
files and config the role copies to the service as root. A pointer's clone inside it is
0755, like the platform checkout -- the 0700 belongs to the parent directory, not to the
clones under it.

The script is one file, `ansible/roles/host_base/files/storagebaby-deploy`, installed by
`host_base` beside the deploy units. `bootstrap.sh` installs the same script from a
heredoc, because the first deploy runs before `host_base` has ever converged and bootstrap
cannot clone the repository (the operator registers the deploy key only after bootstrap
prints it). `tests/static/test_bootstrap.py` holds the heredoc byte-identical to the role's
file, the way it already holds the chaotic-aur values identical. It runs with python3 and
PyYAML, which ansible brings to every host, so `source.yml` is parsed and not pattern
matched.

**On the live host** this lands as follows: `host_base` rewrites the unit, the next tick
finds the `ansible-pull` clone and no state file, and runs one full converge that changes
nothing and writes the state. That single no-op run is the whole migration.

## 4. Playbook and role

Discovery in `ansible/playbook.yml` finds `source.yml` beside `service.yml`, in shared
and host folders alike, and resolves each pointer to
`{{ sources_root }}/<name>/<path>/service.yml`. From there a pointer service is a path to
a spec like any other, and _every_ loop that reads specs sees it: `placed_services`, the
route-fqdn collection for the host-gateway drop-in, the `tcp_ports` collection for
Traefik's entrypoints and the unprivileged-port floor, and the `service` role include with
its `service_config` merge. `service_dir` is the fetched folder, so templates, `config/`
and the secrets lookup (host override first, then `service_dir/secrets.sops.yaml`) work
unchanged.

`sources_root` is one variable with three values, because discovery, `lookup('template')`,
`copy src:` and the secrets `exists` test all run on the controller and the fetched tree
has to be where the controller is:

| Run                             | Controller             | `sources_root`                  | Passed by                   |
| ------------------------------- | ---------------------- | ------------------------------- | --------------------------- |
| a host's deploy tick            | the host itself        | `/var/lib/storagebaby/sources`  | the default in the playbook |
| Molecule converge / idempotence | the devtools container | `/repo/tests/fixtures/sources`  | `converge.yml`              |
| the static `rendered` suite     | the devtools container | `<repo>/tests/fixtures/sources` | `tests/static/conftest.py`  |

Two assertions join the pre-tasks:

- the `name` in a fetched `service.yml` equals the pointer folder's name, the same rule
  `test_contract.py` holds for in-tree folders;
- the loopback ports of all placed services on the host are unique. `test_ports.py`
  cannot see a pointer it cannot fetch, so the host checks it before writing a single
  route file.

A pointer whose source is not fetched yet is handled like a service user that does not
exist yet: outside check mode it fails the play with a message naming the deploy unit
that fetches it; under `--check` it prints that line and skips, so the operator's
pre-flight of a host that has never deployed (which prepare runs and asserts on both test
hosts) still reaches `failed=0`. In `render_only` the same skip applies, so a pointer to a
private repository on storagebaby renders nothing in the static suite while the fixture,
which is present, renders like any service.

The `registries` key is realised in the role's `host.yml`, between the helper install and
the `Images` include. First the refusal: every registry the service names has to be in
the host's `registries` list, or the converge fails naming the service, the registry and
the host. Then the login: the role copies the host's `registries.sops.yaml` to
`/etc/storagebaby/set-registries.secrets.sops.yaml` (the path the host-secret-set tasks
already use), decrypts it on the host, and writes `~/.config/containers/auth.json` for
the service user, 0600, with one entry per registry the service named; a service that
names none gets the file removed. The file is a _pull prerequisite_, exactly like
`podman-as` -- it is the one thing beside the helpers that may be written before the
pull, and the ordering comment in `host.yml` says so. The service user's `podman pull`
from the role and its own `podman-auto-update.timer` both read that path. That they do is
what the private-registry fixture proves (section 6), not something the spec assumes.
The role never learns which pointer a service came from: a service is a folder, wherever
the folder is.

Everything else in the role is untouched: users, volumes, binds, secrets, quadlets,
drop-ins, routes, timers, hooks, backup sidecar, restarts. An external service gets the
full contract.

## 5. Credentials

**Repository.** A fine-grained personal access token of JLK's account per external
repository: resource owner JLK, _only select repositories_ with the one repository,
permission _Contents: read-only_ (metadata read comes with it). It goes into the host's
`hosts/<host>/secrets/sources.sops.yaml` under the pointer's name, through `mise run sops`.
Rotation is a sops edit and a commit; the token's expiry is the operator's choice when
creating it, and an expired token fails the deploy tick visibly (section 3) rather than
silently freezing the service.

**Images.** For a private package on ghcr.io the host offers `ghcr.io` in `host.yml` and
holds the login in `hosts/<host>/secrets/registries.sops.yaml`: `user` is the GitHub
account name and `token` a token with read access to the packages the host's services
pull. One login per registry per host, not per service -- the same reasoning as
`docs/ownership.md` § 4.2 gives for ACME credentials. Whether a fine-grained token can
read packages is verified during implementation, against GitHub as it is then; if it
cannot, `token` is a classic token with `read:packages`, and the spec's shape does not
change -- only which kind of token the operator pastes. A package pushed by the
repository's own Actions inherits the repository's visibility and permissions, so a
repository the account owns needs no extra package grant.

Why not the alternatives: a machine user with the host's SSH key would spread one
credential over every repository, which is the blast radius JLK ruled out; per-repository
deploy keys would do the same job as the token with a key pair per host and repository to
generate and register by hand; a submodule or recorded SHA would route every external
change through this repository's CI, which is the gate JLK ruled out.

## 6. Tests

**Static** (`tests/static/`):

- `test_sources.py`: every `source.yml` has exactly `repo`, `ref`, `path`; `repo` is
  `https://`, or `http://127.0.0.1` for the test hosts' throwaway server; no `service.yml`
  beside it; the folder name is a valid service name; the folder holds nothing but the
  pointer and an optional README.
- `test_hosts.py`: `registries` is an optional list of registry hosts; on a real host every
  listed registry has an entry in `secrets/registries.sops.yaml` (key names are plaintext
  in a sops file, as `test_secrets.py` already relies on), and every placed service's
  `registries` is a subset of the host's -- the static half of the refusal.
- `test_contract.py`: `registries` is a sorted list of registry hosts, on any service.
- `conftest.placements()` resolves a pointer through `tests/fixtures/sources/<name>/<path>`
  when that exists, so the fixture goes through `test_contract.py`,
  `test_quadlet_conventions.py`, `test_ports.py` and `test_render.py` like any in-tree
  service. A pointer with no local source yields no placement and is covered by
  `test_sources.py` alone.
- `test_bootstrap.py`: the deploy script heredoc in `bootstrap.sh` equals the role's file.
- The existing recipients test covers the two host files with no change, and already
  excludes the test hosts' generated ones.

**Fixture.** `tests/fixtures/sources/ext-echo/deploy/storagebaby/` is a complete external
repository tree for a tiny HTTP echo service with one secret, one config file and a route,
pulling its image from a private registry. It is real-folder placed on both test hosts
(`hosts/test-a/services/ext-echo/source.yml` and `hosts/test-ci/services/ext-echo/source.yml`,
held identical by `test_sources.py` the way `tcp-echo`'s copies are), with
`repo: http://127.0.0.1:8418/ext-echo.git` (the throwaway git server below), `ref: main`,
`path: deploy/storagebaby`. Both test hosts offer `registries: ['127.0.0.1:5000']` in
their `host.yml`; their `sources.sops.yaml` and `registries.sops.yaml` are generated by
prepare into `hosts/test-*/secrets/`, which is already gitignored and excluded from the
recipients test.

**Integration**, in `prepare.yml`:

- Generate the fixture's `secrets.sops.yaml` _into the fixture tree_ on the controller
  (gitignored, `tests/fixtures/sources/**/secrets.sops.yaml`), encrypted to the VM's
  age key with the same keep-if-ours rule as the other generated secrets. This is the
  external-secrets path, not the host override: prepare's existing generator only sees
  `service.yml` files under `hosts/` and so writes no override for `ext-echo`, which is
  what makes the Molecule converge read the file from the fixture tree and the deploy
  test read it from the pushed remote.
- Generate the host's `sources.sops.yaml` (one token per pointer) and
  `registries.sops.yaml` (user `test` and a token per offered registry) into
  `hosts/<host>/secrets/`, with the same keep-if-ours rule, and read both back _on the VM_
  with its key to fill the git server's credentials file and the registry's htpasswd.
- Run a throwaway git HTTP server on the VM (`files/test-git-http.py`, a sibling of the
  SMTP sink: python's `http.server` with HTTP basic auth serving `/srv/git/` over git's
  dumb protocol, `update-server-info` kept current by the bare repository's `post-update`
  hook). Seed `/srv/git/ext-echo.git` from the fixture tree with the secrets force-added,
  branch `main`. The deploy script's `GIT_ASKPASS` path is then exercised for real: a
  wrong token is a 401 and a failed tick.
- Run a throwaway registry on the VM (`registry:2` with an htpasswd of the host's login
  for `127.0.0.1:5000`, declared insecure in
  `/etc/containers/registries.conf.d/` by prepare and by nothing in a role), and push the
  echo image into it. The fixture's unit names `127.0.0.1:5000/ext-echo:latest` and its
  spec declares `registries: ["127.0.0.1:5000"]`.

`converge.yml` passes `sources_root=/repo/tests/fixtures/sources`. The idempotence step
needs nothing new.

**Verify** (`test_deploy.py`, in the trailing `order(-1)` group with the other deploy
cases, plus `test_service.py` where the generic claims already loop over placements):

- the external service is up, healthy, answers through Traefik, its secret is a podman
  secret of its user, its `auth.json` exists with mode 0600 and the registry probe
  succeeds through it;
- a deploy tick with nothing changed anywhere is a no-op (state file equal, no restart);
- a commit to the external remote that changes the fixture's template, then one deploy
  tick: exactly `ext-echo.service` restarted, traefik and another placed service did not,
  and the state file records the new SHA;
- a commit that only touches the external repository's README: a tick runs the playbook
  and restarts nothing;
- a revoked token (the git server's credentials file edited on the VM, then restored):
  the tick fails, the journal names `ext-echo` and the 401, the service keeps running, the
  state file is unchanged;
- a service naming a registry the host does not offer: the converge refuses it, naming
  both (asserted on a check-mode run against an edited checkout, so nothing is torn down);
- removing the pointer folder from the remote and ticking removes
  `/var/lib/storagebaby/sources/ext-echo`.

## 7. Documentation

- `README.md`: a section "External services" -- the pointer, the external repository's
  folder, the token, placing and unplacing; the deploy section updated from `ansible-pull`
  to the script and the state file.
- `docs/external-service.md`: the page one reads _from the other repository_: what folder
  to create, which keys go in `.sops.yaml`, how to encrypt, what `registries` means, how
  to find out what the host did (`storagebaby-svc logs <name>`, the deploy journal).
- `ansible/roles/service/README.md`: `registries`; `ansible/roles/host_base/README.md` (or
  the role's header comments, whichever exists): the deploy script and state file.
- `CLAUDE.md`: the pointer folder under "Managing services", `registries` in the key
  list, the script in the deploy description.
- `docs/quickstart.md`: one step for placing an external repository.

## 8. Risks and open points

- **Fine-grained tokens and ghcr.** Verified in implementation; the fallback is named in
  section 5 and changes no shape.
- **The external `service.yml` is not statically checked here.** A malformed contract in
  a private repository fails at converge time on the host, with the role's own assertions
  and the two new pre-task asserts. That is the trade JLK chose; the external repository
  may copy `tests/static/` if it wants the checks earlier.
- **Expiry.** A fine-grained token expires on the date the operator set; the symptom is a
  failing deploy tick, visible in the journal and in the unit's state, while every service
  keeps running on what it has.
- **Serialised converges.** The tick is a `Type=oneshot` and runs one playbook; several
  pointers only add fetches, not runs.

## 9. Order of work

1. The deploy script, bootstrap heredoc, `host_base` install, identity test. The existing
   deploy tests must pass unchanged on it before anything external exists.
2. Playbook discovery and the two pre-task asserts; `sources_root`; check-mode skip.
3. `registries` in the role.
4. The fixture, the two throwaway servers in prepare, the gitignore rules, the static
   `test_sources.py` and the `placements()` resolution.
5. The verify cases.
6. Documentation.

## 10. Operator steps after merge (not implementation)

1. In the application repository: create `deploy/storagebaby/` with the contract, a
   `.sops.yaml` listing the operator key and `&storagebaby`, encrypt `secrets.sops.yaml`,
   push.
2. On GitHub: a fine-grained token for that one repository, Contents read-only; for a
   private image, the package token section 5 settles on.
3. Here: `hosts/StorageBaby/services/<name>/source.yml`; the token under `<name>` in
   `hosts/StorageBaby/secrets/sources.sops.yaml` (`mise run sops` on it); for a private
   image `registries: [ghcr.io]` in `hosts/StorageBaby/host.yml` and the login in
   `hosts/StorageBaby/secrets/registries.sops.yaml`; commit, let CI promote `stable`.
4. Watch one tick: `journalctl -u storagebaby-deploy.service -n 50`,
   `storagebaby-svc ps <name>`.

`yuzukam` is the first candidate: its in-tree folder moves to its repository and is
replaced by a pointer in one commit.

## 11. Amendments

Recorded during Tasks 1–4, against the shape above.

1. **`sources_root` is a Molecule group var, not a `converge.yml` argument.** § 4's table and
   § 6 said `converge.yml` would pass `sources_root=/repo/tests/fixtures/sources`; it is instead set
   once as a group var in `tests/integration/molecule/test-ci/molecule.yml`, which reaches
   the playbook the same way and needs no change to `converge.yml` itself.
2. **`storagebaby_repo_root` is a play var, repeated per play.** `prepare.yml`'s "Seed every
   pointed external repository on the VM's git server" play declares
   `storagebaby_repo_root: /repo` as its own play var, alongside the other plays in that
   file that need it, because Ansible play vars do not cross a play boundary — there is no
   single place to set it once for the whole file.
3. **`single_container_service()` and `other_placed_service()` in `test_deploy.py` skip
   pointer-placed services.** The cases built on them edit a template and push to `stable`,
   or restart a service and read its unit's restart count, against the seeded platform
   checkout at `/var/lib/storagebaby/repo`; a pointer service's spec is read from the
   fetched external tree instead, so picking one there would edit or watch a file the host
   never reads and see a silent no-op rather than the restart or drift being asserted on.
   `ext-echo` is covered by its own cases instead (§ 6, "Verify").
4. **The `REGISTRY_AUTH_FILE` fallback is not needed.** The implementation plan
   (`docs/superpowers/plans/2026-10-02-external-services.md`) named a fallback in case
   `runuser -u`'s `HOME` reset and containers/image's default lookup turned out not to find
   the service user's `auth.json`: a Quadlet drop-in setting `REGISTRY_AUTH_FILE` and the
   same export in `podman-as`. Neither is in the role or the templates. The `test-ci`
   integration runs of 2026-10-08 settled it: the converge pulled
   `127.0.0.1:5000/ext-echo:latest` as `svc-ext-echo` through the role-written
   `~/.config/containers/auth.json`, and `podman auto-update --dry-run` as that user
   reached the registry with the same file.
5. **The pointer folder carries no credential file.** § 1's decisions list the
   2026-10-07 host-owns-credentials amendment already; it is restated here only to confirm
   that Tasks 1–4 built exactly that — `source.yml` and nothing else but an optional README
   under the pointer folder, with `sources.sops.yaml` and `registries.sops.yaml` living
   under `hosts/<host>/secrets/` as designed.
6. **A pointer under `hosts/shared/services/` is supported but untested by any fixture.**
   The script resolves it and the playbook discovers it exactly like a host's own pointer
   (§ 2, § 4); nothing in the test suite places one there, because `ext-echo`, the only
   pointer fixture, is a real folder under each test host rather than shared. No fixture is
   added for this; the first service actually placed under `hosts/shared/services/` as a
   pointer is what exercises the path.
7. **A failed fetch inside a command substitution was swallowed, and is not any more.** The
   first `test-ci` run (2026-10-08) revoked the fixture's token and the tick still exited 0
   with "nothing changed": bash does not carry `errexit` into `$(...)` unless
   `shopt -s inherit_errexit` is set, so the failed `git fetch` inside
   `sha=$(sync_checkout ...)` never stopped the function, and `rev-parse HEAD` returned the
   old SHA. The script now sets `inherit_errexit`, every git step in `sync_checkout` returns
   on its own failure, and both call sites exit 1 naming the repository. The second run
   measured the refusal: the tick fails, the journal names `ext-echo` and
   `Authentication failed`, the state file is untouched, the service keeps running.
8. **Verified on `test-ci` only.** Both integration runs used `MOLECULE_HOST=test-ci`
   (prepare, converge, idempotence at zero changes, 172 verifier cases). The `test-a`
   placement, which runs every service, has not been run on this branch because the test VM
   was shared with other work; it is the whole-branch run the plan's last section asks for.
