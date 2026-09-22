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

## Unit names and order

`<stem>.container` → `<stem>.service`, `<stem>.pod` → `<stem>-pod.service`, `<stem>.build` →
`<stem>-build.service`. The role starts and restarts them in that order — build, pod,
container — and a changed `.build` also restarts every container of the service, because
their image has just been rebuilt.

## What the role does to the host

Creates `svc-<name>` (lingering, subuid/subgid allocated), the volume directories (0750,
owned by the service user), every group named by `groups` or by a bind (system groups) with
`svc-<name>` a member, and any missing bind directory (2775, root:<group>). An existing bind
directory is left exactly as it is — its permissions belong to the operator, not to the role.
A membership change terminates the user session so the new groups take effect, which stops
the service's containers; the unit tasks at the end of the same run start them again.
