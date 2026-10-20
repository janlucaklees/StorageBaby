# MCP

One Model Context Protocol endpoint in front of every adapter on this host.
<https://mcp.home.klees.io> — private, so a VPN client or another service on this host, and
nothing from the internet.

## Why this is one service and not four

An MCP server is neither part of the agent nor part of the application: it is an adapter
that calls the application's REST API over HTTPS. Where it runs is decided by who needs to
reach it and where the token should live, and here the answer to both is "this host" —
the agent is itself a service, so "next to the agent" and "next to the application" are the
same box.

`mcp-hub` is then the piece that makes one service possible at all. Two of the three
adapters speak HTTP and could in principle be services of their own; `mealie-mcp` speaks
stdio only and cannot. The hub runs the stdio ones as child processes, proxies the HTTP
ones, and gives all of them one door, one credential store and one config file.

## Shape

```
Traefik ──► 127.0.0.1:<derived> ──► mcp-hub :3002 ┬─ stdio ──► mealie-mcp ──► mealie.klees.io
                                                  ├─ http ───► :8000 mcp-nextcloud ──► nextcloud.klees.io
                                                  └─ http ───► :3001 mcp-paperless ──► paperless.klees.io
```

The pod publishes the hub's 3002 — its `container_port` — and nothing else, on the loopback
port Cradle derives for the `mcp` route. 8000 and 3001 exist only on the pod's own loopback,
which is the point: `mcp-nextcloud` in single-user mode authenticates _to_ Nextcloud with an
app password and asks its own clients for nothing, so the hub's OAuth 2.1 has to be the only
way in. Each adapter reaches its application by the application's public route name through
Traefik — the role renders the `AddHost=<fqdn>:host-gateway` drop-in that makes that resolve
from inside a pod, and no template here writes one.

## The image is built on the host

`mcp-hub` bundles none of the servers its documentation catalogues — its `dependencies` are
the MCP SDK, express and the OAuth provider. Upstream's documented entry is
`npx -y @ni-c/mealie-mcp`, fetched from the npm registry the first time the hub starts that
server on demand.

`config/build/Containerfile` installs it instead: `FROM ghcr.io/ni-c/mcp-hub:0.11.6` plus
`npm install -g @ni-c/mealie-mcp@0.6.0`. The version pins in git, the image is built by
`mcp-hub-build.service` before anything runs it, and a registry outage becomes a converge
failure the deploy timer retries and the journal records — rather than a 03:00 on-demand
start nobody is watching.

Adding an adapter from the same catalogue is two edits: a line in the Containerfile and an
entry in `config/hub/mcp.json`. `@ni-c/caldav-mcp`, `@ni-c/carddav-mcp`, `@ni-c/imap-mcp`
and `@ni-c/smtp-mcp` would reach the MXroute mailbox the same way Calino does.

## Configuration

`config/hub/mcp.json` is Claude Code's `mcpServers` format verbatim, and `${VAR}` in it
expands from the hub's own environment — which is the whole mechanism by which a token
reaches a child server's env or an upstream's headers without being committed. Its two
`${…}` names, `MEALIE_API_TOKEN` and `PAPERLESS_TOKEN`, are `type=env` secrets on
`mcp-hub.container.j2`; an unset one is fatal at startup, which is why every name in that
file has a matching secret. The hub's third env secret, `PASSWORD_HASH`, is its own login
and appears in no config file.

`config/` is copied, not templated (`ansible.builtin.copy`), so the URLs in `mcp.json` are
literal and duplicated from `service.yml`'s `config:` block by hand. They agree on every
host because mealie, nextcloud and paperless each declare a full `klees.io` name of their
own, identical wherever the folder is placed.

Mounted as a directory and not a file: the hub reloads `mcp.json` when it changes, and a
save by rename is a new inode that a single-file bind mount cannot follow. `config/build/`
lives beside that mount rather than inside it.

No tool filters are set. The hub supports `allowTools` / `denyTools` per server, and
`"denyTools": ["delete_*"]` on an adapter is the knob to reach for if an agent ever needs to
be kept away from destructive calls.

## Secrets

| secret               | what it is                                                    |
| -------------------- | ------------------------------------------------------------- |
| `hub_password_hash`  | bcrypt hash of the hub's own login password, `PASSWORD_HASH`  |
| `mealie_api_token`   | Mealie → Settings → API Tokens, on a dedicated account        |
| `nextcloud_user`     | the Nextcloud account the adapter signs in as — see below     |
| `nextcloud_password` | an app password for that account                              |
| `paperless_token`    | Paperless API token for a dedicated user                      |
| `kopia_password`     | the backup client's password, `client_mcp` in kopia's secrets |

`nextcloud_user` is not a secret and is declared as one anyway: it is half of a single
credential, and putting it beside the password means one file to open and no pair that can
drift apart.

It names the **operator's own account**, not a dedicated one. Nextcloud's notes, calendar,
contacts and files are per-user, so an adapter signed in as a fresh `mcp` account would see
an empty Nextcloud rather than a narrower one — a dedicated account there isolates by
making the adapter useless. What makes it safe enough is the app password itself: a
separate credential, revocable on its own from Settings → Security → Devices & sessions,
without disturbing the login. It is not scoped to parts of the account, so it does carry
everything that user can reach.

Paperless is the opposite case and keeps its dedicated user: a document with no explicit
owner is visible to every user, so a second account sees the archive without being the
operator.

`hub_password_hash` encrypts nothing — it is the login for the hub's own sign-in page,
used when approving a new client, and freely rotatable: a new hash simply means a new
password. Nothing in this service becomes unreadable if it changes, unlike mailflow's
`encryption_key`.

The hash is generated by the operator, never here:

```sh
htpasswd -bnBC 10 "" 'the-password' | tr -d ':\n'
```

`PASSWORD_HASH` wins over `PASSWORD`, so the plaintext never exists on the host.

## No `basic_auth` on the route

The hub carries OAuth 2.1 with dynamic client registration plus long-lived API tokens, and
a `basicAuth` middleware at the edge would 401 every MCP client before it got that far. The
private space is the second layer instead.

## Connecting a client

A browser-capable client does OAuth against the hub with the password above. One that
cannot — Hermes, and anything driving the OpenAI Responses API — uses a resource-bound
token, created on the host and printed exactly once:

```sh
doas /usr/local/sbin/podman-as svc-mcp podman exec mcp-hub \
	node /app/dist/admin.js tokens create --resource hub --days 90 --label hermes
```

Path routing gives each adapter its own endpoint — `/mealie`, `/nextcloud`, `/paperless` —
and `/hub` aggregates all of them behind six meta-tools, which is the one connector to
register when a client only allows a single MCP server.

## Home Assistant

Not in `mcp.json` yet. Home Assistant ships its own MCP server as an integration, so when
that service lands it is added here as a remote upstream — `/api/mcp/assist` with a
long-lived access token as a bearer header — and gains a `ha_token` secret. Until then the
entry is deliberately absent so `/health` tells the truth.

## What a healthy first start looks like

```
mcp-hub: PASSWORD_HASH is not a bcrypt hash … the operator login is disabled
mcp-hub: TRUSTED_PROXIES is not set — login rate limiting falls back to a single global counter
mcp-hub listening on :3002
[nextcloud] down (failed to start: Version negotiation probe failed: fetch failed), restarting in 1s
[paperless] up (paperless-mcp 1.0.0)
[mealie] up (mealie-mcp 0.6.0)
[nextcloud] up (Nextcloud MCP)
```

Three of those lines look like faults and are not:

- The `PASSWORD_HASH` complaint appears **only on a test host**, where `prepare.yml`
  generates 32 random bytes for every secret — 64 hex characters, not a bcrypt hash. The hub
  disables the login and keeps serving, by design: "they need the process, not the login".
  On storagebaby the operator's `htpasswd` hash makes it go away. If it appears there, the
  login is dead and the hash is wrong.
- `TRUSTED_PROXIES` is the deliberate choice above.
- The `[nextcloud] down … restarting in 1s/2s/4s/8s` sequence is the hub probing an upstream
  that has not finished binding yet. `After=`/`Wants=` orders the _units_, not the moment a
  process is listening, and the hub's supervisor retries with backoff for exactly this. Four
  attempts over fifteen seconds is normal; a server still down after that is not.

`journalctl CONTAINER_NAME=mcp-hub` is where those lines live — `podman logs` is empty on
this platform, the containers log to journald.

## Health

`/livez` is liveness and needs no auth; `/health` reports each child server and wants a
bearer token. The container's health check is `/livez` on purpose: upstream's design is that
one degraded child returns 503 from `/health` while the hub itself is fine, and killing the
container over that would take the healthy servers down with it.
