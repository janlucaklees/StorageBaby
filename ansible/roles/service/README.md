# The `service` role

Converges one placed service: user, volumes, bind groups, config, secrets and Quadlet units.
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
| `fqdn`                               | `<domain>.<host domain>`, empty when the service has no `domain`                                          |
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

One caveat for the first `.build` that lands: `Start inactive units` starts anything whose
`is-active` is not `active`. Quadlet writes build units as `Type=oneshot` with
`RemainAfterExit=yes`, so a finished build reads as active — but on a Podman that drops the
`RemainAfterExit`, a successful build would read `inactive` and be rebuilt on every
converge, which reports changed and breaks idempotence. Fix it there by treating a build
unit's `Result=success` as up rather than by making the test tolerant.

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
