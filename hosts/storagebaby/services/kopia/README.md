# kopia

The central [Kopia](https://kopia.io) repository server. It owns one encrypted
repository and hands out per-client accounts; the application-owned backup clients
(immich, paperless, nextcloud) get a Kopia account each and push their snapshots into
it. Accounts are not clicked together either: the server registers one per
`client_<service>` secret on every start — see [Client registration is declared
too](#client-registration-is-declared-too). Until the first pod lands, no service
declares one, so this is a server with an empty repository and no users but the admin.

Web UI: `https://kopia.<host domain>` — `kopia.home.klees.io` on storagebaby.

## The repository connection is declared, not clicked

The old compose stack started a bare server and left the repository to be created by
hand in the UI, which made the connection a piece of undocumented runtime state. Here
it is part of the spec:

```yaml
config:
  server_username: jlk
  repository: s3
  s3_endpoint: s3.eu-central-003.backblazeb2.com
  s3_bucket: storagebaby-kopia
```

`config/start.sh` reads those through the unit's `Environment=` lines and connects the
repository **once**, before the server starts. `KOPIA_CONFIG_PATH` lives on the `config`
volume, so the connection survives a restart and the script is a no-op on every start
after the first.

A host overrides the backend through `service_config` in its `host.yml`. That is what
the test VM does — it has no Backblaze account:

```yaml
service_config:
  kopia:
    repository: filesystem
```

`filesystem` creates the repository under the `repo` volume (`/app/repo` in the
container) on first start and connects to it afterwards. `s3` and `filesystem` are the
only accepted values; anything else makes the container exit 2 with a named reason
rather than starting a server with no repository.

### Connect, then create — and what that means for a live bucket

Both backends create the repository if it is not there yet. The filesystem branch tells
the two cases apart by looking (`/app/repo` non-empty), the S3 branch by trying:
`kopia repository connect s3` first, `kopia repository create s3` with the same flags
only when that failed. So the two cases an operator can be in with a real B2 bucket are:

- **The bucket is fresh** (no repository in it). The script creates one, encrypted with
  the generated `repository_password` from `secrets.sops.yaml`. Nothing else to do —
  but back that password up (below), because it is now the only key to the snapshots.
- **The bucket already holds a repository** (the one the old compose stack created).
  Then the generated password is the wrong one and **must be replaced with the existing
  repository's password before the first deploy**:

  ```sh
  make sops FILE=hosts/storagebaby/services/kopia/secrets.sops.yaml
  ```

  Getting this wrong does not quietly make a second repository beside the first: kopia
  refuses to `create` over a bucket that already holds a repository, so both calls fail,
  the unit restart-loops, and the journal says so. The failure is loud on purpose.

> **`s3_endpoint` and `s3_bucket` are placeholders.** They were written from the old
> README's Backblaze instructions, not read off a live account — the bucket may not
> exist under that name and the region may be wrong. **Confirm both with the operator
> before the first real deploy**, and fix them in `service.yml`.

## Secrets

Four, all `Secret=` in the unit and read from `/run/secrets/<name>`:

| Secret                | What it is                                              |
| --------------------- | ------------------------------------------------------- |
| `server_password`     | password for `server_username` in the web UI            |
| `repository_password` | the repository's encryption password (`KOPIA_PASSWORD`) |
| `b2_key_id`           | Backblaze application key id, used as the S3 access key |
| `b2_application_key`  | Backblaze application key, used as the S3 secret key    |

Four of its own, that is. The `client_<service>` passwords are `host_secrets` and come
from the host's `kopia-clients` set instead, because the client services need the same
values — the registration section below has the whole mechanism.

> **The two B2 values in `secrets.sops.yaml` are `REPLACE_ME`.** There was no
> credential to carry over from the old stack. Put the real ones in with
>
> ```sh
> make sops FILE=hosts/storagebaby/services/kopia/secrets.sops.yaml
> ```
>
> before the first real deploy. Until then a converge on storagebaby brings the
> container up, both the S3 connect and the S3 create fail on the credentials, and the
> unit restart-loops — which is the visible failure it should be.

> **The repository password cannot be recovered.** Kopia derives the repository's
> encryption keys from it; there is no reset, no escrow and no way back into the
> snapshots without it. The value in `secrets.sops.yaml` was generated for this
> migration and is readable only with the operator's age key. **Back it up outside
> this repository** (password manager, paper) before the repository holds anything
> worth restoring.

`server_password` is the one that can be rotated freely: change it in
`secrets.sops.yaml`, converge, and the role's secret sync reports changed and restarts
`kopia.service` with the new value. Rotating `repository_password` is **not** a
password change — it would make the existing repository unreadable. Use
`kopia repository change-password` against the running server instead.

## How it runs

Rootless Podman + Quadlet as `svc-kopia`, one `.container` unit, converged by the
`service` role.

```ini
Entrypoint=/bin/sh
Exec={{ config_dir }}/start.sh
```

Both lines are needed: the image's own entrypoint is `/bin/kopia`, so pointing `Exec=`
at the script alone would have kopia try to run it as a subcommand. The script is
deployed with the rest of `config/` to `/etc/storagebaby/kopia/` and bind-mounted into
the container at the same path, read-only — so editing it in git is all it takes to
change how the container starts, and the role's config copy restarts the unit.

The image is **pinned** (`kopia:0.23.1`) and has no `AutoUpdate=registry`, unlike the
other services here. A kopia upgrade can carry a repository format upgrade, and that is
not a decision for a nightly timer. Bump the `Image=` line to upgrade.

### The repository password is persisted, on purpose

```ini
Environment=KOPIA_PERSIST_CREDENTIALS_ON_CONNECT=true
Environment=KOPIA_USE_KEYRING=false
```

The image ships `KOPIA_PERSIST_CREDENTIALS_ON_CONNECT=false`, and these two lines
reverse it. Without them, `KOPIA_PASSWORD` exists only in the start script's own
process — and `podman exec` does **not** inherit that, so every administrative
command (`kopia repository status`, `kopia server users add`, maintenance) dies at an
interactive password prompt: `password prompt error: inappropriate ioctl for device`.
Under the old compose stack the password was an ordinary `environment:` entry, which
`docker compose exec` did inherit; this restores the same ergonomics without putting
the value into the unit or into `podman inspect`.

Both are environment and not flags because kopia consults them when _reading_ the
persisted password too, not only when writing it — a flag on the connect would make
`kopia repository status` prompt again. The password lands in
`/app/config/repository.config.kopia-password` on the `config` volume, base64 but not
encrypted, 0600. That is the same class of exposure as the podman secret store the
value already sits in on the same host, and `--no-use-keyring` is not a choice: a
container has no keyring to store it in.

`repository.config` itself is the second copy of a credential, and on storagebaby it is
the more interesting one: with the S3 backend the connect writes the **B2 key id and
application key** into it, because that is how kopia reaches the bucket on every later
start without being handed the keys again. So the `config` volume on the pool — not
just the `.kopia-password` file beside it — holds Backblaze credentials in the clear.
Two consequences: it is not a directory to copy off the host casually, and rotating the
B2 key is **not** done by editing `secrets.sops.yaml` alone. The start script only
connects when `repository.config` is absent, so a rotation is: new value in the secret,
delete `repository.config`, restart. The repository is unchanged by that, so `cache`
stays valid — unlike the case at the end of this file.

### TLS inside, because the repository protocol is gRPC

This server speaks TLS on `127.0.0.1:51515` and Traefik **re-encrypts** to it, which is
not what the rest of the platform does and not a preference:

```yaml
route:
  scheme: https
  insecure_skip_verify: true
```

A repository client — every backup sidecar — opens the repository over **gRPC**, and
gRPC is HTTP/2. Traefik reaches a backend over HTTP/2 only when the backend is TLS: an
`http://` backend is downgraded to HTTP/1.1, the client's session call is forwarded as
an HTTP/1.1 request the server never answers, and the client dies a minute later on

```
rpc error: code = Unavailable desc = unexpected HTTP status code received from
server: 504 (Gateway Timeout)
```

An `h2c://` backend does not help either: kopia's `--insecure` listener answers no
HTTP/2 preface at all (`curl --http2-prior-knowledge http://127.0.0.1:51515/` is
refused outright), so Traefik reports 500. And the client cannot be told to stay on
REST: kopia 0.23's `repository connect server` has no `--no-grpc`. The web UI works
over HTTP/1.1 throughout, which is why an insecure server looks perfectly healthy while
no client can connect to it.

So `config/start.sh` generates a certificate on first start, onto the `config` volume
beside `repository.config`:

```sh
openssl req -x509 -newkey rsa:4096 -nodes -keyout /app/config/server.key \
	-out /app/config/server.cert -days 3650 -subj "/CN=kopia" \
	-addext "subjectAltName=DNS:kopia,DNS:$KOPIA_PUBLIC_HOST"
```

and serves `--address=https://0.0.0.0:51515 --tls-cert-file=… --tls-key-file=…`.
`KOPIA_PUBLIC_HOST` comes from the unit as `kopia.{{ domain }}`. Nothing verifies this
certificate — Traefik is told not to, and nothing could, since it vouches for a name
that exists only inside this host — and nothing needs to: the hop is to `127.0.0.1`.
It is generated once and kept for ten years because it is never seen outside the host
and rotating it buys nothing.

**Clients still see Traefik's certificate, not this one.** They connect to
`https://kopia.<domain>`, where the Let's Encrypt certificate is on storagebaby and a
self-signed default on a test host; the sidecar pins the fingerprint only in the second
case. This certificate is invisible to them, which is why it can be a throwaway.

### Health check

```ini
HealthCmd=curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1:51515/ | grep -qE '^(200|401)$'
```

Not the `curl -sf` every other unit uses. The server username and password put the UI
behind basic auth, so `/` answers **401** to an unauthenticated probe and `-f` would
turn the healthy steady state into a kill loop (the same trap stirling-pdf's README
describes, without stirling's unauthenticated status endpoint to escape into). So the
status code is what is checked: 401 is the expected answer, 200 is accepted as well so
the probe keeps working if auth is ever dropped, and everything else — no response at
all, a 404, a 5xx — fails. `-k` for the certificate above. `HealthStartPeriod=60s`
covers the first start, where the certificate is generated and the repository created
or connected before the server binds.

The same 401 is what makes the route pass the platform's HTTPS check: Traefik reaching
a backend that answers 401 proves the route, where a 404 would mean no router matched.

## Client registration is declared too

The old stack had `make register USER=... PASSWORD_FILE=...` — one shell command per
client, run by hand, remembered nowhere. Here a client account is three lines of git.

**1. The shared value.** Both sides of a client password are the same string, so it
lives once, in the host's secret set `hosts/storagebaby/secrets/kopia-clients.sops.yaml`:

```yaml
paperless: <the password>
immich: <the password>
…
```

**2. The server's side.** `service.yml` names the keys it needs, prefixed `client_`:

```yaml
host_secrets:
  client_paperless: kopia-clients.paperless
```

The role syncs each into a podman secret of `svc-kopia`, and the unit template renders
one `Secret=client_<service>` line per key with that prefix — so the container sees
`/run/secrets/client_paperless` and nothing that is not declared.

**3. The client's side.** The backing service declares the other half of the same
reference, and the role's generated sidecar picks it up:

```yaml
host_secrets:
  kopia_password: kopia-clients.paperless
```

`config/start.sh` then does the registration itself, on every start, after the
repository is connected and before the server binds:

```sh
for f in /run/secrets/client_*; do
	name="${f##*/client_}"
	kopia server users add "$name@$KOPIA_CLIENT_HOSTS" --user-password="$(cat "$f")" \
		|| kopia server users set … # when the user already exists
done
```

`kopia server users add` is a **repository** command, not a server one — it writes a
manifest — which is why it can run before `kopia server start` and needs no running
server and no password prompt (the repository credential is persisted, above).
`add` refuses an existing user with `user already exists`; that one case falls through
to `set`, the update form of the same command. Every other failure aborts the start,
loudly, rather than bringing up a server whose clients cannot authenticate.

`KOPIA_CLIENT_HOSTS` is rendered from `{{ hostname }}`, the host the service is placed
on. It is the second half of the identity kopia matches a client by: the sidecar
announces `KOPIA_CLIENT_USERNAME=<service>` and `KOPIA_CLIENT_HOSTNAME=<host>`, so the
account this script creates has to be exactly `<service>@<host>`.

> **The password goes through argv.** kopia 0.23.1's `server users add|set` accept only
> `--user-password`, `--user-password-hash` and the interactive `--ask-password` — there
> is no `--user-password-file` (`cli/command_user_add_set.go`). So for the lifetime of
> that one call the value is visible in the container's `ps` and in the host's
> `/proc/<pid>/cmdline` for a process owned by a subuid of `svc-kopia` — readable by
> root and by `svc-kopia`, both of which already hold the value in the podman secret
> store. Hashing it first buys nothing: `server users hash-password` takes the password
> through argv as well. Revisit if a later kopia grows a file or envar form.

**Adding a client** is therefore: put the value in the set
(`make sops FILE=hosts/storagebaby/secrets/kopia-clients.sops.yaml`), add
`client_<service>` to this service's `host_secrets`, add `kopia_password` to the
client's, converge. The secret change restarts `kopia.service`, which re-runs the
registration. Nothing is typed at a shell, and a rotated password is one edit on one
line rather than two that can drift apart.

Each client gets only its own Kopia account: no Backblaze credentials, no repository
password, and by default visibility of only its own snapshots and policies.

> The set's four values are `REPLACE_ME` — placeholders for paperless, openarchiver,
> immich and nextcloud, whose pods arrive over the rest of Phase 3. A test host never
> uses this file: `prepare.yml` generates its own set, with a fresh random value per
> key, encrypted to the VM's own age key.

A running server rereads its user list within 5–10 minutes, or immediately on
`kopia server refresh`.

## Volumes

| Volume   | Class  | Container path | What it holds                                        |
| -------- | ------ | -------------- | ---------------------------------------------------- |
| `config` | `pool` | `/app/config`  | `repository.config` — the repository connection      |
| `cache`  | `fast` | `/app/cache`   | content cache, rebuildable                           |
| `logs`   | `fast` | `/app/logs`    | kopia's own logs                                     |
| `repo`   | `fast` | `/app/repo`    | the repository itself, **only** in `filesystem` mode |

`repo` is mounted on every host but stays empty under `repository: s3`; on storagebaby
the repository lives in Backblaze. The unit states `KOPIA_CONFIG_PATH`,
`KOPIA_CACHE_DIRECTORY` and `KOPIA_LOG_DIR` explicitly even though the image sets the
same three, so the four mounts above are guaranteed to be the paths kopia really uses.

> **`cache` belongs to whichever repository `config` points at.** Starting over —
> wiping `repo` and `config` to create a fresh repository, or repointing the
> connection — means wiping `cache` in the same breath. A cache left behind from the
> previous repository is decrypted with keys the new one does not have, and kopia
> fails on it at startup with `cipher: message authentication failed`, which reads
> like a wrong password and is not one. (Found the hard way while developing this
> service; nothing in normal operation wipes a volume.)
