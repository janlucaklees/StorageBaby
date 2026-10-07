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
`service.yml`. The host has to offer that registry (`registries` in its `host.yml`) and hold
a login for it (`hosts/<host>/secrets/registries.sops.yaml`); a service naming a registry
the host does not offer is refused at converge time. A floating tag with
`AutoUpdate=registry` is picked up by the service user's `podman-auto-update.timer`; a
pinned tag changes when you push a commit that changes it.

## 4. The pointer (StorageBaby repository)

    hosts/StorageBaby/services/<name>/source.yml
      repo: https://github.com/<you>/<repository>
      ref: main
      path: deploy/storagebaby

For a private repository: a fine-grained personal access token (resource owner you, only
this repository, Contents: read-only), stored under `<name>: { token }` in
`hosts/StorageBaby/secrets/sources.sops.yaml` (`mise run sops` on that file). Commit, let
CI promote `stable`; the next tick fetches and converges.

## 5. Looking at it

On the host: `journalctl -u storagebaby-deploy.service -n 50` (the fetch and the converge),
`storagebaby-svc ps <name>`, `storagebaby-svc logs <name>`. A tick that could not fetch
fails the unit and names the repository; the service keeps running on what it has.
