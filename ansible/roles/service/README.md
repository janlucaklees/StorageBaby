# The `service` role

Converges one placed service: user, volumes, bind groups, config, secrets, Quadlet units,
Traefik routes, timers, after-change hooks and the generated backup sidecar.
It is included once per `hosts/**/services/<name>/service.yml` by `ansible/playbook.yml`.

## Variables a `quadlet/*.j2` template may use

| Variable                             | What it holds                                                                                             |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| `service`                            | the spec, with `config` already deep-merged: `host.yml`'s `service_config[<name>]` over `service.config`  |
| `svc_user`                           | `svc-<name>`, the rootless user the units belong to                                                       |
| `volumes`                            | volume name → absolute host path (class root, or a `volume_overrides` entry)                              |
| `binds`                              | bind name → `{host, container, mode, group}`, straight from the spec                                      |
| `devices_enabled`                    | true when the host declares `gpu: true` **and** the service declares `devices`                            |
| `keep_groups`                        | true when the service has any `groups` or any bind group                                                  |
| `config_dir`                         | `/etc/storagebaby/<name>`, where `config/` of the service folder is deployed (root-owned, world-readable) |
| `routes`                             | list of `{domain, port, fqdn}`, one per declared route; empty when the service has none                   |
| `fqdn`                               | the first route's fqdn, empty when the service has none — the one-route shorthand                         |
| `hostname`                           | `inventory_hostname`, the host this service is placed on                                                  |
| `backup_enabled`                     | true when the service declares a `backup` block                                                           |
| `domain`, `tz`, `acme`, `acme_email` | from `host.yml`                                                                                           |

The merge of `service.config` happens in the playbook, not in the role: `service` is an
include_role param, and a role param outranks any `set_fact` the role could make.

## Idioms

```jinja
{% for name, b in binds.items() %}
Volume={{ b.host }}:{{ b.container }}:{{ b.mode | default('ro') }}
{% endfor %}
{% if devices_enabled | bool %}{% for d in service.devices %}
AddDevice={{ d }}
{% endfor %}{% endif %}
{% if keep_groups | bool %}
GroupAdd=keep-groups
{% endif %}
```

`| bool` on the two flags: they come from a `set_fact`, so a template must not rely on
them being a real boolean rather than the string `"False"`, which would be truthy.

## Routes

A service declares either `routes: [{domain, port}, ...]` or the one-route shorthand
`domain:` + `port:`; the role turns both into `routes`, each entry gaining `fqdn`. Every
route gets its own Traefik file `/etc/storagebaby/traefik/dynamic.d/<name>-<domain>.yml`
and its own router and service, both named `<name>-<domain>`.

The name carries the domain because Traefik reads one flat directory: two routes of one
service would otherwise write the same file, and the second would win. The role removes a
pre-Phase-3 `<name>.yml` on every converge, because a leftover would keep serving its old
router beside the new ones.

`service.route` is something else: a block of options that apply to every route of the
service. `internal: api@internal` points the router at a Traefik-internal service instead
of a rendered one and `wildcard_cert: true` asks for the wildcard certificate — both
traefik's own. The other two describe the backend:

```yaml
route:
  scheme: https # http (default) or https: how Traefik reaches 127.0.0.1:<port>
  insecure_skip_verify: true # only with https, and only for a certificate nothing can vouch for
```

A `routes:` entry may carry the same two keys and then overrides them for itself.

`scheme: https` is not about secrecy on a loopback hop — it is the only way Traefik
speaks **HTTP/2** to a backend, and therefore the only way it can carry gRPC. Kopia's
repository clients speak gRPC and cannot opt out, so `kopia/service.yml` declares both
keys: the server makes its own certificate, Traefik re-encrypts to it and skips a
verification nothing could pass. `insecure_skip_verify` renders a Traefik
`serversTransport` named after the route and attaches it to that route's loadBalancer.

## Reaching another service through Traefik

A template never writes an `AddHost=<name>:host-gateway` line, and a static test says
so. The role writes them, for **every** route name placed on the host, into a Quadlet
drop-in — so a service that calls another one only has to use its public FQDN.

```
/etc/containers/systemd/users/<uid>/<name>.pod.d/10-storagebaby-hosts.conf
/etc/containers/systemd/users/<uid>/<stem>.container.d/10-storagebaby-hosts.conf
```

The pod when the service has one, each `.container` when it has not. Podman refuses
`--add-host` on a container that joins a pod ("extra host entries must be specified on
the pod: network cannot be configured when it is shared with a pod"), so the pod is the
only place it can go — and that covers every container of a pod service, because
`test_pod_publishes_exactly_the_route_ports` asserts they all carry `Pod=`, generated
sidecar included. On traefik's `Network=host` container the line is accepted and
resolves to `127.0.0.1`, which in the host's own namespace _is_ the host, so it needs
no exception.

Why it is needed at all: rootless, a container's network namespace is pasta's, and
pasta copies the **host's own address** onto that namespace's interface. A name that
resolves to the host therefore resolves, from inside the container, to the container —
where nothing listens on 443, because Traefik is the single listener on 80/443 in the
host's network namespace. Measured on the test VM: a connect from inside
`paperless-upload` and from inside `paperless-app` to the VM's own `192.168.122.53:443`
is refused, `127.0.0.1:443` is refused, and only `169.254.1.2:443` — podman's
`host-gateway` — answers. It is not a test-host quirk: the LAN address is unreachable
from a container on any rootless host, DNS or no DNS.

The list is the **host's**, not the service's, which is why it is a drop-in and why
`placed_fqdns` is computed in `ansible/playbook.yml` beside `placed_services` rather
than in the role: the role runs once per service and sees only its own spec. It is
sorted and de-duplicated so the rendered file is byte-stable — an unstable order would
restart every service on every converge.

A changed drop-in is a changed unit. `units.yml` registers the drop-in render, maps
each changed one back to its parent unit's file name and ORs that into the `changed`
flag the unit file itself contributes, so the parent lands in `changed_units` and gets
the daemon-reload plus restart it needs. Quadlet generates a unit from the unit file
and its drop-ins together, so nothing less would reach a running container.

Stale drop-ins are handled exactly as stale units are, which is to say not at all: a
service removed from a host keeps its unit directory, and now its `<unit>.d/` with it,
until someone clears `/etc/containers/systemd/users/<uid>/`. The case that matters is
covered without cleanup — unplacing a service shortens the list, which changes the
drop-in of every service still placed, which restarts them.

## Host secrets

`secrets:` are the service's own, from its folder. `host_secrets:` are values shared
between services on one host:

```yaml
host_secrets:
  kopia_password: kopia-clients.paperless # <set>.<key>
```

The set is `hosts/<host>/secrets/<set>.sops.yaml`, encrypted under that host's rule, so
only that host can read it. The role copies the referenced sets to the host, decrypts them
there and syncs each entry into a Podman secret of `svc-<name>` with the same helper the
own secrets use — so a changed value restarts the service's units exactly like any other.
Key names are plaintext in a sops file, so a static test checks every reference resolves.

That is how one string is the same on both sides: the kopia server declares
`client_<service>: kopia-clients.<service>` and the client `kopia_password:
kopia-clients.<service>`.

## Hooks

```yaml
hooks:
  after_change:
    - {
        container: nextcloud-app,
        user: www-data,
        command: 'php occ db:add-missing-indices'
      }
```

Run in declared order after the role restarted or started the service's units, each one
waiting first until `podman healthcheck run <container>` succeeds (60 × 10 s) — the
container is up moments after a restart, its application is not. Ten minutes because
that is the longest `HealthStartPeriod` any unit in the repo declares (nextcloud's app,
600 s, for a first start that copies an installation in and then runs the installer);
a wait shorter than the start period a container is allowed would fail the play on
exactly the converge that needed it most.

`when: unit_changed` (the default) runs the hook only on a converge that actually changed
something, which is what makes it a deploy step and not a nightly one: a version bump in a
`.container.j2` runs the post-upgrade commands, the next run does not. `when: always` opts
a single command out of that.

## Timers

`quadlet/<stem>.timer.j2` and `quadlet/<stem>.service.j2` are plain systemd user units,
not Quadlet ones — they are rendered into `~svc-<name>/.config/systemd/user/`, enabled,
started and restarted on change, while every other `quadlet/*.j2` goes to the Quadlet
directory. The static contract requires the two halves as a pair: a timer without its
service never fires, a service without its timer never runs. Used for database dumps and
for cron-like commands; they address containers by name, e.g.
`ExecStart=/usr/bin/podman exec paperless-database sh -c 'pg_dump ... > /backups/x.dump'`.

## The generated backup sidecar

A service with a `backup` block gets `<name>-backup.container` generated from the role's
own `kopia-client.container.j2` — it is not in the service folder. The sidecar joins the
service's pod (hence the contract: `backup` requires a `<name>.pod.j2`), mounts every
volume in `backup.paths` read-only at `/data/<volume>`, connects to `https://kopia.<domain>`
as `<name>@<host>` with the `kopia_password` host secret, applies the retention policy and
then runs a Kopia server so the scheduler takes the snapshots at `backup.schedule`.

`kopia_password` reaches the sidecar as an **env-type** podman secret
(`Secret=kopia_password,type=env,target=KOPIA_PASSWORD`) rather than as a file under
`/run/secrets`. A `podman exec` inherits the container's environment but not the start
script's, and both the sidecar's `HealthCmd` (`kopia repository status`) and every
administrative kopia command are execs — with the value only in the script, each of them
dies at an interactive prompt. Kopia's own persisted-credentials mechanism does not
cover it: `repository connect server` writes `repository.config` and no password file
beside it, unlike the repository connects the kopia server itself makes.

The sidecar also verifies an existing connection before trusting it: the server
certificate fingerprint it pinned at connect time is frozen in `repository.config`, and
Traefik hands out a fresh self-signed default certificate on every restart of a host
with `acme: false`. A stored connection that cannot open the repository is therefore
deleted and made again rather than retried forever.

The role adds two implicit volumes, `backup-config` and `backup-cache` (class `fast`),
before the volume directories are created, so they are made and owned like any other.
Databases are backed up as dumps, not as data directories: a dump timer writes into a
volume that is listed in `paths`.

## What every `.container` must declare

`HealthCmd=` (plus `HealthOnFailure=kill` and `Restart=always`, which the static test
enforces) and `ContainerName=`. Both are contract, not style: the integration verifier runs
`podman healthcheck run <ContainerName>` for every container of every placed service, so a
unit without a health command or without its own name fails the suite.

## Unit names and order

`<stem>.container` → `<stem>.service`, `<stem>.pod` → `<stem>-pod.service`, `<stem>.build` →
`<stem>-build.service`. The role starts and restarts them in that order — build, pod,
container — and a changed `.build` also restarts every container of the service, because
their image has just been rebuilt.

A **changed pod is the only restart its containers need**, and they are dropped from the
restart list when it is in there. Quadlet binds them to the pod's unit, so restarting the
pod already stops them and brings them back; restarting each of them again seconds later
kills a container that has only just begun its first start — the one start that must not
be interrupted, because it is the one that migrates a schema or lays a data directory
down. Three services broke on one converge before this: a paperless migration left half
applied, a meilisearch data directory it could not read afterwards, and a Nextcloud tree
copied but never installed. Whatever the pod does not bring back is started once by
"Start units that are not up".

What is dropped is _every_ `.container` of the service, not only the ones the rendered
units bind to the pod, and that is safe for one reason worth recording:
`test_pod_publishes_exactly_the_route_ports` in
`tests/static/test_quadlet_conventions.py` asserts that in a service with a `.pod.j2`
every `*.container.j2` carries `Pod=<name>.pod`, and the role's own generated backup
sidecar does too. So no container of a pod service stays outside the pod. A container
that deliberately did would be dropped from the restart list and never restarted —
already `active`, so "Start units that are not up" skips it — and would keep running
against a unit file that has changed under it.

The caveat that first `.build` was expected to hit, and did: `Start units that are not up`
starts anything whose `is-active` is not `active`, and the Podman on the test VM writes
build units as `Type=oneshot` **without** `RemainAfterExit`, so a build that ran and
succeeded reads `inactive`. Taken at face value that rebuilds the image on every converge —
reported changed, idempotence gone, a pointless rebuild every night from the deploy timer.

So a `-build.service` is up when it has run to a successful exit, which the role reads as
`Result=success` **and** a non-zero `ExecMainStartTimestampMonotonic`. The timestamp is not
decoration: `Result=success` is also what systemd reports for a unit that has never run,
which is precisely the case that still has to be started. Everything else still has to be
really `active`.

## What the role does to the host

Creates `svc-<name>` (lingering, subuid/subgid allocated), any missing volume directory (0750,
owned by the service user), every group named by `groups` or by a bind (system groups) with
`svc-<name>` a member, and any missing bind directory (2775, root:<group>).

The class roots themselves (`storage_roots`) are not this role's: `host_base` creates them
root-owned and 0755, precisely so that a volume directory created here never silently
becomes one, 0750 and owned by whichever service converged first.

Both kinds of directory are created, never re-permissioned. An existing bind directory's
permissions belong to the operator, not to the role. An existing volume directory belongs to
the image: entrypoints routinely `chown`/`chmod` their data tree on every start — usually to a
non-root uid inside the container, which on the host is a subuid of `svc-<name>`, not
`svc-<name>` itself. Enforcing 0750 svc-owned on each converge would report changed forever
_and_ take the running service's access away; with `HealthOnFailure=kill` that is a restart
loop, run nightly by the deploy timer. So the role hands over a directory that starts out the
service's own and then leaves it alone.
A membership change stops and starts the user manager so the new groups take effect, which
stops the service's containers; the unit tasks at the end of the same run start them again.
