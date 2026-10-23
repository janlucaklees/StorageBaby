# cliproxy

[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) in front of the AI
subscriptions the operator already pays for. It signs in to Claude, Codex, Gemini,
Antigravity, Grok, Kimi, Meta or Devin **as their CLIs do**, holds the OAuth credentials,
and offers them back on this host as OpenAI-, Gemini- and Anthropic-compatible HTTP APIs
with one API key in front. One subscription, many clients, no per-token billing.

Route: `cliproxy.home.klees.io`. Two things answer there — the proxy API under `/v1`,
`/v1beta` and the Claude and Codex paths, and the management panel at
`/management.html`, which is where accounts are added and everything else is configured.

## Who owns the configuration

**This is the one service on this host whose running configuration is not in git.** The
application keeps a single `config.yaml` and its panel rewrites that file whenever
anything is changed in the browser — that is how it is designed to be operated, and it is
why the operator can add an account without a commit. The split is drawn in
`config/app/entrypoint.sh`, in one place, and it is the whole of what this folder promises:

| What                                      | Owner                  | How                                                                     |
| ----------------------------------------- | ---------------------- | ----------------------------------------------------------------------- |
| `access.api-keys`                         | this repository        | re-applied from the `client_api_key` secret on **every** start          |
| `server.port`, `oauth.auth-dir`           | this repository        | re-applied from the unit's `CPA_CONTAINER_PORT` and `/data` every start |
| the management password                   | this repository        | `MANAGEMENT_PASSWORD` in the environment, never written to a file       |
| everything else in `/data/config.yaml`    | the panel              | seeded once from `config/app/config.seed.yaml`, then the panel's        |
| the provider credentials in `/data/auths` | the panel, necessarily | written by each OAuth login, refreshed continuously                     |

Three consequences worth knowing before touching either side:

- **Editing `config.seed.yaml` changes nothing on a host that already has a config.** It is
  a first-boot state, not a desired state. Deleting `/data/config.yaml` reseeds — and
  throws away every setting the panel ever saved.
- **A second API key added in the panel does not survive a restart.** `access.api-keys` is
  set from the secret, not merged into it. Rotating the key is a sops edit and a push.
- **Changing the listener port in the panel does nothing** either, for the same reason: the
  pod publishes `container_port` and the entrypoint writes that same number back into the
  file on every start, so the two cannot come apart.

## The two secrets

Both are placed with the same placeholder value, `set-me-see-the-cliproxy-README`, and the
entrypoint treats that string as "no credential exists yet" rather than as a credential:

- **`client_api_key`** — what a client sends as `Authorization: Bearer …` (also accepted as
  `X-Api-Key`, `X-Goog-Api-Key` or `?key=`). While it is the placeholder, the entrypoint
  puts a **fresh random key** in the config instead and says so in the journal, so the
  proxy answers 401 to everybody — including whoever read the placeholder out of this
  repository. That is what makes placing this folder before the secret exists safe: an
  unconfigured CLIProxyAPI with an empty `api-keys` list authenticates nobody and
  therefore authorises everybody, which on a publicly resolvable name is an open AI
  gateway, and the placeholder is the one thing standing between those two states.
- **`management_password`** — the panel login, as `MANAGEMENT_PASSWORD`. While it is the
  placeholder the environment variable is not set at all, which leaves the management
  routes unmounted: `/v8/management/*` is a hard 404 and the panel cannot be logged in to.

**`client_api_key` has a second holder.** LibreChat's chat provider is this proxy, and its
own `cliproxy_api_key` has to be byte-identical to this value — a copy, taken deliberately
instead of a `host_secrets` set, so that this key stays in this folder. Rotating it means
both files: `hosts/StorageBaby/services/librechat/secrets.sops.yaml` too, or every
conversation there fails against a proxy that is up.
`hosts/StorageBaby/services/librechat/README.md` has that side.

Set both in one edit, from the repository root:

```sh
mise run sops hosts/StorageBaby/services/cliproxy/secrets.sops.yaml
```

Two values of `openssl rand -hex 32`'s shape are the right thing to put there. Commit,
push, and the deploy timer restarts the container within five minutes.

## Adding a provider account

In the panel, after the password is real. The flow is three steps and the middle one looks
like a failure:

1. Start the login for a provider. The panel opens the provider's own authorise URL —
   `claude.ai/oauth/authorize`, `auth.openai.com/oauth/authorize`, and so on.
2. Authorise. The provider then redirects to a **`http://localhost:54545/callback`** style
   URL (1455 for Codex, 54545 for Claude, others per provider), which fails to load:
   those ports are CLIProxyAPI's own callback listeners, and CLIProxyAPI is on the server,
   not on the browser's machine. **Expected.** The code is in the address bar.
3. Copy that whole failed URL back into the panel's callback field. The management API
   takes it as `redirect_url` and finishes the exchange.

The alternative is publishing those ports and pointing the provider at the host, which
would mean five more entrypoints on Traefik for a flow used once per account. Hence the
pod publishes none of them.

## Version bumps

The image is pinned in `config/build/Containerfile`, not in the unit, because the unit
runs a locally built image: upstream ships several releases a day — v8.0.26 to v8.0.39
between 2026-10-12 and 2026-10-22 — so there is no `AutoUpdate=registry` here and
`podman-auto-update.timer` leaves this service alone. Bump the `FROM` tag, push; Quadlet
rebuilds the image and restarts the container because the Containerfile changed. The
panel's own asset updates itself from GitHub independently of the image.

The build exists for one reason: the entrypoint has to edit a YAML file the panel
rewrites in its own style, the upstream image is `debian:bookworm` with one Go binary in
it, and `sed` cannot survive a re-indent. yq is a static binary and one `COPY` away.

## Operating it

```sh
cradle-svc ps cliproxy       # the pod
cradle-svc logs cliproxy-app # the server's own log, including the two placeholder lines
cradle-svc restart cliproxy  # re-applies the secrets into the config
```

`/` answers 200 with the endpoint list and no credential — that is the health check
(`config/app/health.sh`) and the integration verifier's probe, and it keeps answering
while the credential pool is empty, which is the state this service is placed in.

Another service on this host reaches it the way everything here reaches everything:
through `https://cliproxy.home.klees.io` and the host-gateway drop-in the role writes for
every placed route. Rootless containers of different users cannot see each other's
loopback, so there is no shorter path.

## Backup and restore

The `data` volume — `/var/lib/storagebaby/fast/cliproxy/data` — is the whole of it: the
config the panel owns, `auths/` with one credential file per account, and the panel asset.
Nightly at 03:00 into the kopia repository.

```sh
kopia snapshot restore < snapshot-id > /var/lib/storagebaby/fast/cliproxy/data
```

Restoring it brings the accounts back without a single provider login. Losing it costs
the logins again, and nothing else: the client key and the panel password come back from
sops on the next start.
