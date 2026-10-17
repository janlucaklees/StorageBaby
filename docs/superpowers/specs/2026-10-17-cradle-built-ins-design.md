# Cradle: built-ins (stage 2 of the split)

Status: design, approved in conversation 2026-10-17 (JLK's answers to the six questions below); implementation authorised to run without a chat gate
Date: 2026-10-17
Builds on: `2026-10-16-cradle-split-design.md` (stage 1, implemented), `docs/ownership.md` in Cradle (§ 1 the rule, § 3 platform, § 5 administrator, § 6.1 backups)

## 1. Goal

Cradle owns the ingress and the backup mechanism; the host configuration says how they
are used. Traefik and the kopia server stop being service folders a host repository has
to carry and become **built-ins**: folders Cradle ships and places on every host itself.
The host declares backups in one block, Traefik's and kopia's credentials live in the
host's secrets, the kopia client passwords are generated on the host and never written
by anyone, and `hosts/shared/` goes.

Decisions (JLK, 2026-10-17):

1. **The host backup block** is `backup: none` or
   `backup: { repository: <path>, username: <ui user>, domain: kopia, replica: none | { s3_endpoint, s3_bucket, must_exist } }`.
   The replica is Cradle's mechanism with the host's settings; `volume_overrides: kopia/repo`
   and `service_config.kopia.*` go away.
2. **Host secrets for the built-ins:** `hosts/<name>/secrets/backup.sops.yaml` with
   `repository_password`, `server_password`, `b2_key_id`, `b2_application_key`;
   `hosts/<name>/secrets/ingress.sops.yaml` with `porkbun_api_key`, `porkbun_secret_api_key`,
   `dashboard_htpasswd`. The operator creates them by moving the values out of the two
   service folders; nothing in this repository or in Cradle ever holds the values.
3. **The kopia web UI and Traefik's dashboard keep their routes**, `kopia.<domain>` and
   `traefik.<domain>`, each overridable per host (`backup.domain`, `ingress.domain`).
4. **Generated client passwords** live on the host only: one per backed-up service, created
   on first converge, never in git. A fresh host re-registers clients with new passwords;
   the repository password stays the operator's.
5. **`backup: none`** places no kopia server, and a service that declares a backup policy
   there is refused at converge. Every host gets Traefik.
6. **The rollout happens with the operator present**, outside the 02:00 maintenance window
   and not while the hourly replica push runs.

Non-goals: the provider model (`ownership.md` § 6.1), per-domain certificate methods and
the host-level fallback certificate (§ 7 items 2–3), port assignment (stage 3), minimal
fixtures (stage 4), restore.

## 2. Shape

```
cradle/ansible/builtins/
  traefik/
    service.yml                  name, port, domain (label), route {internal, wildcard_cert, basic_auth}, volumes {letsencrypt}, secrets [...]
    quadlet/traefik.container.j2 today's shared/traefik unit, unchanged in substance
    README.md
  kopia/
    service.yml                  name, port, domain, route {scheme: https, insecure_skip_verify}, volumes {config, cache, logs, repo}, secrets [...]
    quadlet/kopia.container.j2   today's unit, reading backup.* instead of service.config.*
    quadlet/kopia-sync.{service,timer}.j2
    config/{start.sh,sync.sh}
    README.md

hosts/<name>/host.yml            + backup: {...} | none      + ingress: { domain: traefik }   (optional)
hosts/<name>/secrets/backup.sops.yaml, ingress.sops.yaml
hosts/shared/                    gone
```

A built-in is a service folder in every respect the `service` role can see: a `svc-<name>`
user, volumes under the host's storage roots, podman secrets, Quadlet units, a route
file. The two things the role does not do for a plugin it does for a built-in, and both
are the playbook's business, not the role's:

- **Secrets come from the host.** A built-in has no `secrets.sops.yaml`; its secrets file is
  `hosts/<name>/secrets/ingress.sops.yaml` (traefik) or `backup.sops.yaml` (kopia). The
  playbook hands the role that path the way it hands a per-host override today.
- **Settings come from `host.yml`.** The playbook applies `ingress.domain` and `backup.*`
  onto the built-in's spec before the role sees it, the way `service_config` is merged
  today: `backup.repository` becomes the `kopia/repo` volume path, `backup.domain` the
  route label, `backup.username` and `backup.replica.*` the template's environment.

## 3. Placement

The playbook places, in this order: traefik (always), kopia (when `backup` is a block),
then the host's own folders and pointers. `hosts/shared/` is no longer discovered, by the
playbook or by the deploy script's pointer glob; the host repository contract (stage 1
spec § 4) loses that line.

`backup` is **required** in `host.yml`, `none` or a block, because configuration over
convention: a host that says nothing is refused with a message naming the key. A service
whose `backup` is a block on a host whose `backup` is `none` is refused at the same
place, naming the service.

## 4. The kopia built-in

Unchanged from today's folder in what it runs: the pinned server image, `start.sh`
creating or connecting the filesystem repository, registering one user per client,
generating its own TLS certificate, serving the UI and the gRPC endpoint behind Traefik;
`sync.sh` pushing the repository to the S3 replica hourly, or doing nothing when
`replica` is `none`. What changes is where the facts come from:

| Was                                              | Is                                                                                           |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| `service.config.repository: filesystem`          | always filesystem (the S3 repository form goes)                                              |
| `volume_overrides: kopia/repo: <path>`           | `backup.repository`                                                                          |
| `service.config.server_username`                 | `backup.username`                                                                            |
| `service.config.sync`, `s3_*`, `sync_must_exist` | `backup.replica` (`none` or the block)                                                       |
| `secrets: [..., client_paperless, ...]`          | generated; the unit renders one `Secret=client_<svc>` per placed service with a backup block |
| `kopia_password` in every consumer's secrets     | generated                                                                                    |

**Generated client passwords.** The playbook holds the list of placed services with a
backup block, `backup_clients`. For each, the `host_base` role ensures a 32-byte random
value in `/var/lib/cradle/backup-clients/<svc>` (root, 0600), created once and never
rewritten. The `service` role syncs that value into the consumer's podman secret
`kopia_password` (what the sidecar reads today, unchanged) and the playbook syncs the
same values into the kopia user's podman secrets `client_<svc>`. A consumer's
`service.yml` therefore no longer lists `kopia_password` under `secrets`, and a stale
`kopia_password` key in a service's `secrets.sops.yaml` is ignored, not refused.

The client sidecar (`kopia-client.container.j2`, `kopia-client.sh`) is unchanged except
that `KOPIA_SERVER_URL` reads the built-in's route name.

## 5. The Traefik built-in

Today's shared folder moved into Cradle, with three changes: the secrets file is the
host's `ingress.sops.yaml`; the route label is `ingress.domain` (default `traefik`); the
dashboard's `basic_auth: dashboard_htpasswd` stays, as a secret of that file. The ACME
resolver, `cert_zones`, the TCP entrypoints from `placed_tcp_ports`, the `readTimeout=0`
for kopia's gRPC upload, the default certificate on `acme: false` hosts: all unchanged.
`readTimeout=0` stops being drift item 10: both ends are Cradle's now.

## 6. Tests

- **Static:** the contract tests see built-ins as placements (`conftest.placements()`
  includes them for every fixture host, with their secrets file resolved from the host's
  secrets directory); `test_hosts.py` asserts `backup` is present and well-formed, and
  refuses a service backup block on a `backup: none` host; the render covers the two
  built-ins; `hosts/shared/` is refused if present.
- **Fixture host repository:** `hosts/shared/` and the `kopia` folders are deleted;
  `test-a` and `test-ci` get `backup: { repository: /pool/backups/kopia, username: test, domain: kopia, replica: none }`;
  `prepare.yml` generates `ingress.sops.yaml` and `backup.sops.yaml` with throwaway values
  and stops generating `kopia_password`/`client_*`.
- **Integration:** the existing kopia and traefik verifiers keep passing against the
  built-ins; new cases: generated client passwords exist, are 0600, survive a second
  converge unchanged, and the sidecars connect with them; a `backup: none` host (a third
  fixture host is not added — the refusal is covered statically and by a check-mode case
  that flips the fixture's `backup` to `none` and expects the named refusal).

## 7. Rollout on StorageBaby

Preconditions: Cradle `stable` carries stage 2 and is green; the operator created
`hosts/StorageBaby/secrets/backup.sops.yaml` and `ingress.sops.yaml` with the values moved
from `hosts/StorageBaby/services/kopia/secrets.sops.yaml` and
`hosts/shared/services/traefik/secrets.sops.yaml`; the StorageBaby PR (below) is reviewed.

The StorageBaby change: delete `hosts/shared/` and `hosts/StorageBaby/services/kopia/`;
add to `host.yml`
`backup: { repository: /pool/backups/devices/StorageBaby/kopia, username: jlk, domain: kopia, replica: { s3_endpoint: s3.eu-central-003.backblazeb2.com, s3_bucket: GTMyXab9-jlkkb, must_exist: true } }`
and drop `volume_overrides` and `service_config`; remove `kopia_password` from the
`secrets:` list of every backed-up service's `service.yml`; drop the `hosts/shared` rule
from `.sops.yaml`.

What the converge does: Traefik and kopia are re-rendered from the built-ins and restart
once each (same users, same volume paths, same data); every backed-up service's sidecar
gets a new generated password, so the role restarts those pods once (a secret changed);
the kopia server registers the new passwords on its restart. Order of the first tick:
`host_base` generates the passwords before any service is touched, traefik and kopia
converge before the consumers. The old `client_*` and `kopia_password` podman secrets
that nothing declares any more are left in place (the role does not prune podman
secrets; drift item 6).

Rollout steps: `cradle-deploy --check` first (expected: the two built-ins' units changed,
the backup-clients files to be created, every backed-up pod's secret to change; no
remount, no volume move); then the operator merges and lets the timer's tick converge;
verification: `cradle-svc ps traefik|kopia|<each pod>`, kopia's UI answers, every sidecar
reports healthy within 10 minutes, the hourly replica push's next run succeeds.

## 8. Ownership doc edits

Platform table gains: "The ingress — Traefik as a built-in, placed on every host";
"The backup mechanism — the kopia server as a built-in, the client sidecar, the replica
push; never a backup program's policy". Administrator table gains: "Whether backups run
at all, where the repository lives, where the replica goes — `backup` in `host.yml`";
"The ingress's dashboard name — `ingress` in `host.yml`". A new refusal in § 3: a backup
policy on a host whose `backup` is `none`. § 7: item 4 rewritten as the decision taken
(the platform contains the backup _mechanism_ by decision; the provider model of § 6.1
stays open); item 10 removed; item 9 stays (TCP entrypoints are still rendered into the
unit).

## 9. Acceptance

- Cradle: static and integration suites green on both fixture hosts; CI green; `stable`
  moved.
- StorageBaby: the PR renders with `mise run render` against the new Cradle and the only
  differences from the previous render are the two built-ins' units and the dropped
  `kopia_password` secrets; the live rollout per § 7 ends with every service active and
  every sidecar healthy.

## 10. Open

- The consumers' `secrets.sops.yaml` files still carry a `kopia_password` key each; the
  operator removes them when next editing those files.
- A host whose backups are off still has every consumer's sidecar declared by its
  service folder; refusing is the chosen behaviour, a per-host "skip backups" override is
  not.
