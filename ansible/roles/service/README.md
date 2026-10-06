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

A `*.container.j2` or `*.build.j2` is rendered **twice** per converge, and must render
standalone: once through `lookup('template')` in `images.yml`, only to read its `Image=`
lines (see "Images are pulled before anything of a service is written"), and then for
real through the `template` module. The lookup renders on the controller with the same
task vars the module gets, so everything in the table above is available — but nothing
the module alone provides is. A template reaching for `ansible_managed`, a `template_*`
variable, a `#jinja2:` header or the surrounding loop's `item` would render differently
in the two passes, or fail the first one outright, and no container or build template in
this repo does.

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

## Volume and bind owners

A `volumes` or a `binds` entry may name an `owner`, and it is **the uid the process has
inside the container** — `1000` for paperless-ngx and for jellyfin's linuxserver image,
`70` for `postgres:17-alpine`, `999` for a Debian-based postgres, `82` for `www-data` in
any `php:*-fpm-alpine`. Never a host uid: the host uid is what the role works out.

```yaml
volumes:
  data: { class: pool, owner: 1000 }
  database: { class: fast, owner: 70 }
binds:
  media:
    {
      host: /pool/shared/media,
      container: /media,
      mode: rw,
      group: media,
      owner: 1000
    }
```

The mapping is the rootless user namespace, nothing more:

| container uid | host uid               |
| ------------- | ---------------------- |
| `0`           | `svc-<name>`'s own uid |
| `n` (n ≥ 1)   | `subuid_start + n - 1` |

`subuid_start` is the service user's first subordinate id — the second field of its
`/etc/subuid` line, which the role allocates on the first converge. The gid is the same
arithmetic over `/etc/subgid` with the same `n`; no image on the platform needs the two
to differ. The off-by-one is the whole of the arithmetic: the range begins at container
uid 1, because container uid 0 is the service user itself.

**Adoption happens once.** A missing directory is created owned by the mapped uid and
gid. An existing one is compared — **the directory's own owner, one `stat` of that
inode, never a walk of the tree** — against the mapped uid, and only when the two differ
does the role run one `chown -R` and report changed. The converge after that finds the
owner already right and touches nothing. A volume is chowned `<uid>:<gid>`; a bind by
uid alone, so the group that carries the service user's access into a tree it does not
own survives. Neither changes a mode.

It runs before the units are started, so a service whose data was copied in by hand
comes up able to read it on the same converge.

**Leaving `owner` out is not `owner: 0`.** With no `owner` the role never compares and
never chowns: the directory is created owned by the service user and then left to
whatever the image does with it, which is what stirling-pdf relies on — it chowns its
mounts to a subuid of its own accord and documents no uid to declare, and re-asserting
svc-ownership nightly would take the running service's access away. `owner: 0` is the explicit statement that the
service user owns the tree, and **is** adopted.

**The failure mode is a wrong number.** An `owner` that is not what the image runs as
chowns the whole tree, once, to a uid nothing in the container is — and the role will
not chown it back, because the next converge finds the directory matching what the spec
says; the fix is to correct the spec and converge again. So the uid is read off the
image (`podman exec <container> id -u` on a running one, or its `USER` / `adduser` line)
rather than recalled, and a service whose uid is not established declares no `owner` at
all.

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

A third option guards a route rather than describing its backend:

```yaml
route:
  basic_auth: dashboard_htpasswd # a podman secret of traefik holding htpasswd lines
```

It renders a `basicAuth` middleware named `<name>-<domain>-auth` with
`usersFile: /run/secrets/<secret>` and attaches it to the router. The secret is
**traefik's**, whichever service's route it protects: Traefik is the process that opens
the file, and a rootless user cannot see another user's podman secrets. So the value
names a key in `hosts/shared/services/traefik/secrets.sops.yaml`, listed in traefik's
`secrets:` and mounted by a `Secret=` line in its unit — not one of the protected
service's own secrets. A `routes:` entry may override it like the other two. Today the
only consumer is traefik's own dashboard.

`scheme: https` is not about secrecy on a loopback hop — it is the only way Traefik
speaks **HTTP/2** to a backend, and therefore the only way it can carry gRPC. Kopia's
repository clients speak gRPC and cannot opt out, so `kopia/service.yml` declares both
keys: the server makes its own certificate, Traefik re-encrypts to it and skips a
verification nothing could pass. `insecure_skip_verify` renders a Traefik
`serversTransport` named after the route and attaches it to that route's loadBalancer.

## TCP ports

A service that has to be reached by something other than HTTP declares `tcp_ports`:

```yaml
tcp_ports:
  - { port: 21, target: 2121 } # Traefik listens on 21, forwards to 127.0.0.1:2121
  - { range: [21100, 21109] } # each port forwarded to the same port on loopback
```

`target` defaults to `port`. The `range` form exists for an FTP passive port range, where
the server advertises the port it is listening on and the two sides therefore have to
agree — so a range forwards each port to itself and takes no `target`. Both forms are
plain TCP: no TLS, no host name, nothing Traefik can inspect.

Two halves make it work, and they are rendered in two different places:

- **The entrypoints** are Traefik's own static configuration, so they live in
  `hosts/shared/services/traefik/quadlet/traefik.container.j2` and come from the
  playbook's `placed_tcp_ports` — every port of every service placed on the host, ranges
  expanded, unique and sorted. One entrypoint `tcp-<port>` on `<tcp_bind_address>:<port>`
  each. Because it is rendered into traefik's unit, placing or unplacing a TCP port changes
  that unit and restarts Traefik once; the sorted, de-duplicated list is what keeps it from
  restarting on every converge.
- **The routers** are dynamic, so the role renders them per service into
  `/etc/storagebaby/traefik/dynamic.d/<name>-tcp.yml`: one TCP router and one TCP service
  per port, both named `<name>-tcp-<port>`, the router on `entryPoints: [tcp-<port>]` with
  `rule: HostSNI(`*`)` and the service pointing at `127.0.0.1:<target>`. A service that
  claims no ports has the file removed, like the pre-Phase-3 route file — a stale router
  would keep forwarding a port the service no longer owns, to whatever has since been
  published on it.

**Why the entrypoint binds one address and not the wildcard.** A wildcard listener
(`:<port>`, which is what `--entrypoints.<name>.address=:<port>` means) and a listener on
`127.0.0.1:<port>` are mutually exclusive on Linux: whichever binds second gets EADDRINUSE,
`SO_REUSEADDR` or not, and that holds for the IPv6 dual-stack wildcard Go really opens. So
a wildcard entrypoint rules out every entry where `target == port` — and that is precisely
the shape an FTP passive range has no choice about, because an FTP server advertises the
port it is itself listening on. Traefik therefore binds one concrete address,
`tcp_bind_address`, and the service publishes the same number on loopback; the two coexist,
and Traefik is still the only process on a routable address. `tcp_bind_address` defaults to
`ansible_default_ipv4.address` — the address of the host's default route — and a host that
has several, or whose default route is not the one clients arrive on, sets it as a top-level
key in its `host.yml`. It is also what an application that has to advertise its own address
should be given, because it is the address a client reached: paperless's FTP part renders
`-P {{ tcp_bind_address }}` for exactly that reason, and an FTP transfer behind a TCP proxy
hangs with no error on either side when the advertised address is wrong.

Two things follow from binding a concrete address. The value is a _fact_, so it cannot be
rendered on the controller: the static render passes a placeholder
(`tests/static/conftest.py`, `RENDER_TCP_BIND_ADDRESS`) and asserts only that the flag
carries `<address>:<port>`. And a host whose address changes needs a converge to re-render
the unit — with a consequence worth knowing before it happens, **measured** on the test VM
with Traefik v3.7.13: an entrypoint whose address the host does not hold is fatal to the
whole process, not to that entrypoint. Traefik logs
`error while building entryPoint tcp-<port>: … bind: cannot assign requested address`, exits
1, comes back on `Restart=always`, fails the same way — and while it does, **nothing** of
that host is served: `ss -ltn` shows no listener of the service at all, 80 and 443 included.
A stale `tcp_bind_address` is therefore a host-wide outage, not an FTP problem, and the
answer is a static address or a DHCP reservation for a host that declares a TCP port.
`hosts/shared/services/traefik/README.md` has the transcript.

Three more consequences worth knowing before declaring one:

- **A port belongs to exactly one service per host.** `HostSNI(`*`)` is the only rule a
  non-TLS TCP router can carry, so the entrypoint is the whole of what selects the
  backend: there is no second thing to route on. `tests/static/test_ports.py` treats route
  ports, TCP entrypoints and TCP targets as one namespace per host and rejects a
  duplicate, an overlapping range and 80 or 443.
- **The pod must publish every target on loopback**, exactly as it publishes its route
  ports — `PublishPort=127.0.0.1:<target>:<container port>`, or one
  `127.0.0.1:<lo>-<hi>:<lo>-<hi>` line for a range.
  `test_pod_publishes_exactly_the_route_ports` holds both sides.
- **A port below 80 moves the unprivileged-port sysctl.** Traefik is rootless, and
  `host_base` sets `net.ipv4.ip_unprivileged_port_start` to the minimum of 80 and the
  host's declared TCP ports for that reason — FTP's control port 21 is the case it exists
  for. The setting is host-wide and it is not Traefik's: the day a service declares 21,
  **every** unprivileged user on the host — every `svc-*`, and any future one — may bind
  21–79, which includes 25 and 53. It is still the right trade-off (the alternatives are a
  capability on the binary or a privileged listener), and it is bounded in the one way that
  matters: the value is derived from the placement, so it comes back up to 80 on the
  converge after the declaration goes.

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
host's network namespace. Measured on the test VM, from inside the containers of two
different services: a connect to the VM's own `192.168.122.53:443`
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

A drop-in of a unit this service no longer carries is removed. `units.yml` finds every
`10-storagebaby-hosts.conf` under the unit directory after rendering and deletes the
`.d/` of any whose parent unit is not in the carrier list; a removal reloads the user
manager exactly as a render does, and finding nothing to remove is the normal case, so
the run stays idempotent.

That is a deliberate exception to the platform's "stale files stay" rule, because a
stale drop-in is not the harmless thing a stale unit file is: an orphaned unit is inert,
it generates a unit nobody starts, while an orphaned `<unit>.d/` attaches to a unit that
still exists and changes what it does. The case is this repo's own migration path — a
service that gains a `<name>.pod.j2` stops carrying the drop-in on its containers, and
the `<stem>.container.d/` left behind would make podman refuse to start that container
("extra host entries must be specified on the pod: network cannot be configured when it
is shared with a pod") on that converge and on every nightly deploy after it, until
someone logged into the host and deleted a file by hand. Unplacing a whole _service_
still leaves its unit directory behind, as every other stale file does — but those units
belong to a service nobody starts any more, and the list itself needs no cleanup:
unplacing a service shortens it, which changes the drop-in of every service still
placed, which restarts them.

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

**A container that never becomes healthy does not fail the play.** The wait gives up
after its ten minutes, the service's hooks are skipped — all of them, because a
service's hooks are one ordered sequence against one application and half of nextcloud's
five `occ` calls is worse than none — and the service name and its container are
appended to the play-level `hooks_not_run`. The play carries on with the next service.
`ansible/playbook.yml` fails on that list as its very last task, so the deploy still
reports failure and names what was skipped.

The split matters because the role is included once per placed service from a **single
play**, in sorted order: a hook that failed where it waits would fail the play there,
and every service sorted after it would never converge at all — ten minutes later, every
night, from the deploy timer. The realistic trigger is an application that cannot reach
its database (a `database_password` that does not match a migrated cluster), which is
exactly the state in which the rest of the host must still come up.

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
service's pod (hence the contract: `backup` requires a `<name>.pod.j2` — and hence, too,
how the sidecar gets its `kopia.<domain>` mapping: the host-gateway drop-in is carried by
the templates in the service folder, never by the generated sidecar, so the pod's drop-in
is the only thing that resolves the one name the sidecar cannot work without), mounts
every volume in `backup.paths` read-only at `/data/<volume>`, connects to
`https://kopia.<domain>` as `<name>@<host>` with the `kopia_password` host secret, applies
the retention policy and then runs a Kopia server so the scheduler takes the snapshots at
`backup.schedule`.

`kopia_password` reaches the sidecar as an **env-type** podman secret
(`Secret=kopia_password,type=env,target=KOPIA_PASSWORD`) rather than as a file under
`/run/secrets`. A `podman exec` inherits the container's environment but not the start
script's, and both the sidecar's `HealthCmd` (`kopia repository status`) and every
administrative kopia command are execs — with the value only in the script, each of them
dies at an interactive prompt. Kopia's own persisted-credentials mechanism does not
cover it: `repository connect server` writes `repository.config` and no password file
beside it, unlike the repository connects the kopia server itself makes.

What an env-type secret costs in visibility, measured on the test VM: `podman inspect`
lists the variable in `Config.Env` with its value replaced by seven asterisks
(`KOPIA_PASSWORD=*******`), and the real value is only in the process
(`podman exec <c> printenv <VAR>` returns it). So the name is visible and the value is
not — unlike an `Environment=` line, which would put the value in the unit file, and
unlike a file secret, which keeps even the name out of the container's config.

The Kopia server the sidecar then runs listens on `http://127.0.0.1:51516`
`--insecure --without-password`, and in a pod `127.0.0.1` is the whole pod's namespace
— so every other container of that service can drive that API, which can list and
delete snapshots. It is a deliberate trade: the scheduler needs a listener, the port is
published by no pod so nothing off the host can reach it, and the blast radius is one
service's own backups, reachable only from processes that already hold that service's
live data.

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

`HealthCmd=`, `HealthOnFailure=kill`, `Restart=always` and `ContainerName=`; the static
test enforces all four. They are contract, not style: the integration verifier runs
`podman healthcheck run <ContainerName>` for every container of every placed service, so a
unit without a health command or without its own name fails the suite.

## Images are pulled before anything of a service is written

`images.yml` pulls every image this service's units name, and `host.yml` includes it as
early as the pull's own prerequisites allow — right after the service user's manager is
up and `podman-as` is installed, and therefore **before** the volume and bind
directories, the config copy, the secret sync, every unit file, every Traefik route and
every timer.

Without it a first start **is** the pull. Podman fetches the image from inside
`systemctl start`, a container unit inherits the user manager's default
`TimeoutStartSec=90s`, and podman only holds that window open while the transfer keeps
reporting progress — so a large image over an ordinary link has its start job killed
mid-pull. Measured on the test host: `immich-server` and `immich-machine-learning` both
died at 4m15s, `apache/tika` sat on docker.io for 25 minutes without a byte. Each came
back by itself seconds later on `Restart=always`; the casualty was the converge, which
had already failed at "Start units that are not up" and took every service behind it
down with it.

So the pull is play time now, and systemd's **default start timeout stays** — no
`TimeoutStartSec=` is rendered anywhere. A service that needs more than 90 s to _start_,
with its image already local, is a service worth looking at. The same holds for a
version bump: the image is on disk before the restart, so the downtime is the restart
and nothing else.

### What a failed pull has already done to the host, and what it has not

The ordering is the point, not a convenience. Everything the role writes for a service —
config, `podman secret`s, unit files, drop-ins, route files, timers — reports `changed`
exactly **once**, on the run that really writes it, and that one `changed` is what the
restarts at the end of `units.yml` are keyed on. Write first and fail after, and the
retry finds all of it already matching, restarts nothing, and goes green over a service
still running the old config, the old secret and the old image.

So on a pull that cannot be done, for the service the play stops at:

- **already done:** `svc-<name>` exists with subuid/subgid and linger, its extra groups
  exist and it is a member of them, `/usr/local/sbin/podman-as` and
  `podman-secret-sync` are installed. The pull needs all of it. A _membership_ change
  also stopped and started the user manager by then (see "What the role does to the
  host"), which is the one thing ahead of the pull that touches a running container.
- **not done:** volume directories, bind directories, `{{ config_dir }}`, the config
  copy, the `podman secret` store, the unit directory, every rendered unit and drop-in,
  the drop-in cleanup, the Traefik route files, `daemon-reload`, every `restart`, every
  `start`, the timers, the hooks.

Every service sorted **after** it in the play is untouched entirely, and every service
sorted before it has already converged — the failed pull aborts the play, it does not
roll anything back.

The image list comes from `lookup('template')` on the service's own `*.container.j2` and
`*.build.j2` plus the generated sidecar, not from the files the role is about to render —
because at that point those files do not exist yet, and must not.

One hole this does **not** close, because it has the same shape and predates the pull:
`Render quadlet units` in `units.yml` is a loop, and a `template` failure on item _n_
leaves items `1..n-1` written with no `daemon-reload` and no restart. It is far less
dangerous — a broken template fails on every converge, so nothing goes green while it is
broken, and a brand-new unit is still picked up by "Start units that are not up"; the
residue is an _updated_ unit that rendered before the failing one. Note that the
pre-pull's `lookup('template')` covers only `*.container.j2` and `*.build.j2`, so a
broken `*.pod.j2` or `*.volume.j2` is exactly this case and not a pull failure.

### What is not pulled

An `Image=` that names another unit (`<stem>.build`, `<stem>.image`), a value equal to an
`ImageTag=` one of this service's `.build` units produces, and anything under
`localhost/`. None of them exists in a registry, and none of them needs this: Quadlet
writes a `.build` as `Type=oneshot`, which systemd starts with **no** timeout, so
whatever its Containerfile pulls cannot hit the wall above.

A `.image` unit is a `Type=oneshot` too and so has the same excuse — but unlike a
`.build` its own `Image=` _is_ a registry reference, and nothing here pulls it. So the
skip is only sound while no service declares one, and
`test_no_service_declares_an_image_unit` in `tests/static/test_quadlet_conventions.py`
keeps it that way: a `*.image.j2` fails the static suite until this file learns to read
it.

### Already local is left alone

Each image is probed with `podman image exists` first and pulled only when it is missing —
pull policy `missing`, spelled out. That is deliberate for the `AutoUpdate=registry`
services: a floating tag is moved by the service user's own `podman-auto-update.timer`,
on its schedule and with its own restart. Re-pulling a tag that is already there would
fetch a newer digest behind auto-update's back and restart the service from a converge
that was meant to change nothing. This role only guarantees that the reference a unit
names exists locally before that unit starts.

The probe is also how `changed` is decided: `podman pull` prints an image ID whether it
fetched anything or not, so "it was not there, and now it is" is the honest reading, and
it is made against podman's own lookup rather than against progress lines podman is free
to reword. An attempt that did **not** fetch the image is not reported `changed` either —
the journal of a failed nightly deploy is the one place that must read at face value.

`podman image exists` answers rc 0 for found and rc 1 for missing, and **only those two
are an answer**. Anything else — 125 for a store it cannot open, 127 for a broken
`podman-as`, a user manager that went away — fails the task with podman's stderr, rather
than being read as "the image is missing" and turned into a registry error for a fault
that has nothing to do with the registry.

### Under `--check` the probe runs (once the service user exists) and the pull does not

The probe is read-only, and it is what makes a pre-flight before a version bump say
which images a push would have to fetch. The pull is skipped by its own `when:` rather
than by the module's check-mode handling, because a conditional skip never enters the
retry loop, while a module-level skip produces a result the `until` then has to be able
to read.

### Retries, and how long a dead registry can cost

Three attempts per image, thirty seconds apart, each wrapped in `timeout -k 30 600`. The
timeout is the program and not the task's `timeout:` keyword, because ansible-core raises
a task timeout as a `BaseException` that bypasses the `until` loop — a stalled pull would
then fail the converge instead of being retried, and a stall is the case this defends
against.

The loop **stops at the first image it cannot fetch** (`loop_control.break_when`);
without that, ansible would run every remaining item and only then fail the task, so an
unreachable registry would cost the full budget per image — immich alone is five, close
to three hours, and `storagebaby-deploy.service` is a `Type=oneshot` with no
`TimeoutStartSec` to cut it short. With the break, the worst case **per service** is
3 × 600 s + 2 × 30 s ≈ **31 minutes** to the first failure, plus the time any images of
that same service pulled successfully before it. The converge then fails, naming the
image, with every service on the host still running.

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

Creates `svc-<name>` (lingering, subuid/subgid allocated), any missing volume directory
(0750, owned by the uid the spec's `owner` maps to, or by the service user when it names
none), every group named by `groups` or by a bind (system groups) with `svc-<name>` a
member, and any missing bind directory (2775, `<owner or root>:<group>`).

The class roots themselves (`storage_roots`) are not this role's: `host_base` creates them
root-owned and 0755, precisely so that a volume directory created here never silently
becomes one, 0750 and owned by whichever service converged first.

Both kinds of directory are created and then never re-permissioned, with the single
exception of a declared `owner`, which is adopted once (see "Volume and bind owners").
An existing bind directory's permissions otherwise belong to the operator, not to the
role. An existing volume directory belongs to the image: entrypoints routinely
`chown`/`chmod` their data tree on every start — usually to a non-root uid inside the
container, which on the host is a subuid of `svc-<name>`, not `svc-<name>` itself.
Enforcing 0750 svc-owned on each converge would report changed forever _and_ take the
running service's access away; with `HealthOnFailure=kill` that is a restart loop, run
nightly by the deploy timer. So the role hands over a directory that starts out owned by
whoever the spec says owns it and then leaves it alone. No mode is ever re-asserted,
adoption included.
A membership change stops and starts the user manager so the new groups take effect, which
stops the service's containers; the unit tasks at the end of the same run start them again.
