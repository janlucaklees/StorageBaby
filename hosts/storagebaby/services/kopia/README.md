# kopia

The central [Kopia](https://kopia.io) repository server. It owns one encrypted
repository and hands out per-client accounts; the application-owned backup clients
(immich, paperless, nextcloud) get a Kopia account each and push their snapshots into
it. Those clients are Phase 3 — right now this is a server with an empty repository
and no users but the admin.

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

### No TLS inside, and no fingerprint any more

The old `start.sh` generated a self-signed certificate on first start and wrote its
fingerprint to `server.fingerprint`, because the server spoke HTTPS directly. This one
runs `--insecure` on `127.0.0.1:51515` and Traefik terminates TLS in front of it, the
same as every other service on the platform. Repository clients therefore connect to
`https://kopia.<domain>` with an ordinary publicly-trusted certificate and need **no**
`--server-cert-fingerprint`. The old fingerprint file is dead state; nothing reads it.

### Health check

```ini
HealthCmd=curl -s -i http://127.0.0.1:51515/ | head -n 1 | grep -q -e 200 -e 401
```

Not the `curl -sf` every other unit uses. The server username and password put the UI
behind basic auth, so `/` answers **401** to an unauthenticated probe and `-f` would
turn the healthy steady state into a kill loop (the same trap stirling-pdf's README
describes, without stirling's unauthenticated status endpoint to escape into). So the
status line is what is checked: 401 is the expected answer, 200 is accepted as well so
the probe keeps working if auth is ever dropped, and everything else — no response at
all, a 404, a 5xx — fails. `HealthStartPeriod=60s` covers the first start, where the
repository is created or connected before the server binds.

The same 401 is what makes the route pass the platform's HTTPS check: Traefik reaching
a backend that answers 401 proves the route, where a 404 would mean no router matched.

## Registering a client (Phase 3)

The old stack had `make register USER=... PASSWORD_FILE=...`. The platform equivalent
runs against the container as the service user — `podman exec` needs that user's
runtime directory and session bus, which is what the `env` prefix is for:

```sh
uid=$(id -u svc-kopia)
cd /tmp && runuser -u svc-kopia -- env \
	XDG_RUNTIME_DIR=/run/user/$uid \
	DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus \
	podman exec kopia kopia server users add immich@storagebaby --user-password=…
```

No password prompt: the repository credential is persisted (above). Kopia reloads its
user list on `SIGHUP` (`podman exec kopia kill -HUP 1`).

Each client gets only its own Kopia account: no Backblaze credentials, no repository
password, and by default visibility of only its own snapshots and policies. Wiring that
up per client is Phase 3's job, and it will not be done by hand at a shell — the client
services declare their own `kopia_password` secret.

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
