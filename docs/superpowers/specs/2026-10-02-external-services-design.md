# External services: deploying a service from its own repository

Status: draft, awaiting review
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

Non-goals: tags or commit SHAs as refs (branches only), several services from one
repository, a webhook or push trigger, a removal flow beyond dropping the fetched
checkout, review gates for repositories that are not JLK's, and the conversion of
`yuzukam` -- that is the first real use and an operator follow-up (section 10), not part
of this change.

## 2. Shape

```
hosts/<host>/services/<name>/
  source.yml          repo, ref, path -- the pointer; nothing else of the service lives here
  source.sops.yaml    git_token, and for a private image registry_user + registry_token

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

`source.sops.yaml` is a flat sops file like every other: `git_token` for a private
repository, `registry_user` and `registry_token` when the service declares `registries`
(section 5). A public repository with public images needs no `source.sops.yaml` at all.
The file sits under `hosts/<host>/` and is therefore already covered by the `.sops.yaml`
path rules and by the recipients test; nothing in `.sops.yaml` changes.

The external repository's `secrets.sops.yaml` is decrypted by the host with its own age
key, so the external repository's `.sops.yaml` lists the operator key and the placing
host's key. Both are public keys; the host key is the `&storagebaby` anchor in this
repository's `.sops.yaml`. A test host overrides it the way it overrides every service's
secrets today, `hosts/<host>/secrets/<name>.sops.yaml`, generated in prepare.

One new optional `service.yml` key, `registries`: a list of registry hosts the service
user logs into with `registry_user`/`registry_token` from the pointer's `source.sops.yaml`
before any image is pulled. It is accepted only on a pointer service -- an in-tree service
has no `source.sops.yaml` to read the credential from, and nothing in tree needs one.

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
3. Authenticate over HTTPS with `git_token`, read with `sops --decrypt --extract` and the
   host's age key. The token reaches git through `GIT_ASKPASS` with
   `credential.helper` disabled, so it is never written into a remote URL or `.git/config`
   and never lands on disk in the clear. A public repository has no token and clones
   anonymously.
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

`/var/lib/storagebaby/sources` is root-only (0700), like the repository checkout: it holds
decryptable secrets files and config the role copies to the service as root.

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
the `Images` include: it writes `~/.config/containers/auth.json` for the service user,
0600, from the pointer's `registry_user`/`registry_token`, and removes it when the key
goes. The file is a _pull prerequisite_, exactly like `podman-as` -- it is the one thing
beside the helpers that may be written before the pull, and the ordering comment in
`host.yml` says so. The service user's `podman pull` from the role and its own
`podman-auto-update.timer` both read that path. That they do is what the private-registry
fixture proves (section 6), not something the spec assumes. Reading the credential reuses
the existing decrypt tasks: `source.sops.yaml` is copied to
`/etc/storagebaby/<name>.source.sops.yaml` and decrypted on the host like the service's
own file.

Everything else in the role is untouched: users, volumes, binds, secrets, quadlets,
drop-ins, routes, timers, hooks, backup sidecar, restarts. An external service gets the
full contract.

## 5. Credentials

**Repository.** A fine-grained personal access token of JLK's account per external
repository: resource owner JLK, _only select repositories_ with the one repository,
permission _Contents: read-only_ (metadata read comes with it). It goes into
`hosts/<host>/services/<name>/source.sops.yaml` as `git_token` through `mise run sops`.
Rotation is a sops edit and a commit; the token's expiry is the operator's choice when
creating it, and an expired token fails the deploy tick visibly (section 3) rather than
silently freezing the service.

**Images.** For a private package on ghcr.io the service user needs a registry login.
`registry_user` is the GitHub account name and `registry_token` a token with read access
to the package. Whether a fine-grained token can read packages is verified during
implementation, against GitHub as it is then; if it cannot, `registry_token` is a classic
token with `read:packages`, which is account-wide, and the spec's shape does not change --
only which kind of token the operator pastes. A package pushed by the repository's own
Actions inherits the repository's visibility and permissions, so a repository the account
owns needs no extra package grant.

Why not the alternatives: a machine user with the host's SSH key would spread one
credential over every repository, which is the blast radius JLK ruled out; per-repository
deploy keys would do the same job as the token with a key pair per host and repository to
generate and register by hand; a submodule or recorded SHA would route every external
change through this repository's CI, which is the gate JLK ruled out.

## 6. Tests

**Static** (`tests/static/`):

- `test_sources.py`: every `source.yml` has exactly `repo`, `ref`, `path`; `repo` is
  `https://` or an absolute local path (the fixture's); no `service.yml` beside it; the
  folder name is a valid service name; a `source.sops.yaml` beside it holds only known
  keys (key names are plaintext in sops files, as `test_secrets.py` already relies on);
  `registries` appears only in a pointer service's spec.
- `conftest.placements()` resolves a pointer through `tests/fixtures/sources/<name>/<path>`
  when that exists, so the fixture goes through `test_contract.py`,
  `test_quadlet_conventions.py`, `test_ports.py` and `test_render.py` like any in-tree
  service. A pointer with no local source yields no placement and is covered by
  `test_sources.py` alone.
- `test_bootstrap.py`: the deploy script heredoc in `bootstrap.sh` equals the role's file.
- The existing recipients test covers `source.sops.yaml` with no change.

**Fixture.** `tests/fixtures/sources/ext-echo/deploy/storagebaby/` is a complete external
repository tree for a tiny HTTP echo service with one secret, one config file and a route,
pulling its image from a private registry. It is real-folder placed on both test hosts
(`hosts/test-a/services/ext-echo/source.yml` and `hosts/test-ci/services/ext-echo/source.yml`,
held identical by `test_sources.py` the way `tcp-echo`'s copies are), with
`repo: http://127.0.0.1:8418/ext-echo.git` (the throwaway git server below), `ref: main`,
`path: deploy/storagebaby`. The generated `source.sops.yaml` beside each pointer is
gitignored and excluded from that comparison and from the recipients test, by the same
rule that already excludes `hosts/test-*/secrets/`.

**Integration**, in `prepare.yml`:

- Generate the fixture's `secrets.sops.yaml` _into the fixture tree_ on the controller
  (gitignored, `tests/fixtures/sources/**/secrets.sops.yaml`), encrypted to the VM's
  age key with the same keep-if-ours rule as the other generated secrets. This is the
  external-secrets path, not the host override: prepare's existing generator only sees
  `service.yml` files under `hosts/` and so writes no override for `ext-echo`, which is
  what makes the Molecule converge read the file from the fixture tree and the deploy
  test read it from the pushed remote.
- Generate the pointer's `source.sops.yaml` into `hosts/<host>/services/ext-echo/`
  (gitignored by the same rule as `hosts/test-*/secrets/`) with a `git_token`,
  `registry_user` and `registry_token` of prepare's choosing.
- Run a throwaway git HTTP server on the VM (`files/test-git-http.py`, a sibling of the
  SMTP sink: python's `http.server` with HTTP basic auth serving `/srv/git/` over git's
  dumb protocol, `update-server-info` kept current by the bare repository's `post-update`
  hook). Seed `/srv/git/ext-echo.git` from the fixture tree with the secrets force-added,
  branch `main`. The deploy script's `GIT_ASKPASS` path is then exercised for real: a
  wrong token is a 401 and a failed tick.
- Run a throwaway registry on the VM (`registry:2` with an htpasswd of the same
  `registry_user`/`registry_token`, `127.0.0.1:5000`, declared insecure in
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
- a pointer with a wrong `git_token` (edited on the VM's checkout of this repository for
  the test, then restored): the tick fails, the journal names `ext-echo` and the 401, the
  service keeps running, the state file is unchanged;
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
3. Here: `hosts/storagebaby/services/<name>/source.yml`, `mise run sops
hosts/storagebaby/services/<name>/source.sops.yaml`, commit, let CI promote `stable`.
4. Watch one tick: `journalctl -u storagebaby-deploy.service -n 50`,
   `storagebaby-svc ps <name>`.

`yuzukam` is the first candidate: its in-tree folder moves to its repository and is
replaced by a pointer in one commit.
