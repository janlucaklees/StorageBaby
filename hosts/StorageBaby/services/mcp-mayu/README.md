# MCP (Mayu)

Mayu's own Model Context Protocol endpoint. <https://mcp.mayu.klees.io> — her zone, where
every service of hers lives. One registered server for now: `dm`, the dm.de shop.

## Why a second hub rather than a second account on the first one

The hub's login is one password and its servers are one list — whoever signs in gets every
adapter it carries. The `mcp` hub carries the operator's Nextcloud (notes, calendar,
contacts, files, under his own account's app password), his Paperless archive and his
Mealie. None of that is hers to read, and no setting inside the hub makes a subset of it
hers. A second hub is the whole of the separation: her own address, her own login, her own
signing key and OAuth clients, her own list.

Its own pod rather than a second container in his, and that is the deliberate part: a
shared pod would have put his Nextcloud, Paperless and Mealie adapters next to her hub,
running and unconfigured for her. Nothing in this pod is anything she has not asked for.

What it costs: two unit templates and two config files that look alike, and **a second
dm.de browser** — the `mcp-dm` container of the other pod does the same work with its own
session and its own 2.5 GB of image. A pod is the smallest thing on this platform that two
containers can share a loopback through, so one dm.de server for two hubs and two separate
pods cannot both be true.

## Shape

```
Mayu's client ──► Traefik ──► 127.0.0.1:<derived> ──► mcp-mayu-hub :3002
                                                      │
                                                 /dm  └─► :3000 mcp-mayu-dm ──► chromium ──► dm.de
```

Both containers are in this pod, so the hub reaches the dm.de server on the pod's own
loopback and that server publishes nothing: no port, no route, no credential. Path routing
gives it `/dm`, and `/hub` aggregates everything behind six meta-tools — the one connector
to register in a client that allows only a single MCP server.

The pod carries `ShmSize=1g` because of the browser: chromium keeps renderer shared memory
in `/dev/shm`, Podman's default is 64 MB, and a container inside a pod shares the infra
container's IPC namespace and cannot set its own.

No `.build` unit and no Containerfile here: the other hub builds its image only because it
runs stdio adapters that have to be installed into it first. The one server this hub offers
speaks HTTP, so the upstream image is used as it comes, pinned to the same version the
other hub builds from. Move the two together. If she ever wants an npm-installed adapter —
`@ni-c/caldav-mcp` and its siblings — this folder gains a Containerfile of its own and the
hub's image reference changes to the build unit, the way the `mcp` folder does it.

## Configuration

`config/hub/mcp.json` is Claude Code's `mcpServers` format verbatim, and it carries no
`${…}` name at all: the one server it lists is `http://127.0.0.1:3000/mcp`, this pod's own
loopback, which needs no credential and means the same thing on every host this folder is
placed on. The file is copied rather than templated (`ansible.builtin.copy`), so a literal
was the only thing that could go in it — and a loopback address is the one literal that is
not a host fact.

Adding a server later is one entry here plus, if it needs a credential, one secret. The
`allowTools` / `denyTools` filters are per server and unset: `"denyTools": ["delete_*"]` on
an adapter is the knob to reach for when a server carries destructive calls she should not
have. `dm` has none — checkout is out of scope in dm-mcp by design, so the worst its tools
do is change a cart.

## Secrets

| secret               | what it is                                                 |
| -------------------- | ---------------------------------------------------------- |
| `hub_password_hash`  | bcrypt hash of her login to this hub, `PASSWORD_HASH`      |
| `dm_session_cookies` | raw `cookie:` header of a logged-in `signin.dm.de` request |
| `kopia_password`     | the backup client's, generated on the host, in no file     |

`hub_password_hash` encrypts nothing — it is the login for the hub's own sign-in page, used
when approving a new client, and freely rotatable: a new hash simply means a new password.
The operator generates it, and the plaintext never reaches the host:

```sh
docker run --rm -it alpine:latest \
	sh -c 'apk add -q apache2-utils && htpasswd -nBC 10 "" | tr -d ":\n"'
```

In a throwaway container because `htpasswd` is not installed on the workstation and has no
business being: the password is typed at the prompt rather than passed as an argument, so
it reaches neither the shell history nor `ps`, and the container is gone either way. The
output is the bare `$2y$10$…` hash, which is what `PASSWORD_HASH` wants.

`dm_session_cookies` is **the one decision this folder does not make.** Two browsers hold
a dm.de login now, hers and his, and the files are identical whichever way it goes:

- **The same account in both.** One cart, which is what a household cart is, and what his
  hub already had on its own. The risk is that dm.de rotates a session on renewal:
  whichever browser refreshes first may invalidate the other's cookies, and the loser
  reports `expired` and falls back to a bootstrap header that is stale by then.
  Unmeasured; running it is the only way to find out.
- **Her own dm.de account.** No interference and two separate carts, at the cost of a
  second account and a second cookie export by hand whenever it expires.

There is no `github_token` in either pod: the container can file a `session-needed` issue
about its own dead login, and that is off for now — it takes both `GITHUB_TOKEN` and
`GITHUB_REPOSITORY`, the server refuses to start on exactly one, and a podman secret cannot
hold an empty string. So an expired session here is silent until a tool call fails, and
`mcp-mayu-dm.container.j2` carries the two lines to re-add. In practice the SSO cookie
lives 30 days and renews on every use, so the pod that goes unused is the one that
expires — which, for a hub with one server on it, is worth knowing.

## No `basic_auth` on the route

The hub carries OAuth 2.1 with dynamic client registration plus long-lived API tokens, and
a `basicAuth` middleware at the edge would 401 every MCP client before it got that far.
That login is therefore the only thing in front of this endpoint, which is what makes the
password above worth choosing well.

## Connecting a client

A browser-capable client does OAuth against the hub with the password above. One that
cannot uses a resource-bound token, created on the host and printed exactly once:

```sh
doas /usr/local/sbin/podman-as svc-mcp-mayu podman exec mcp-mayu-hub \
	node /app/dist/admin.js tokens create --resource hub --days 90 --label phone
```

`journalctl CONTAINER_NAME=mcp-mayu-hub` is where its log lives — `podman logs` is empty on
this platform, the containers log to journald. On a test host the hub complains that
`PASSWORD_HASH` is not a bcrypt hash and disables the login by design; on storagebaby that
line means the hash is wrong.

## Not verified on the machine yet

Written from the contract and from upstream, not from a converge.

- `mayu.klees.io` is a new zone: it had to be added to `cert_zones` in `host.yml`, and the
  first converge after that asks Porkbun for a wildcard it has never held. Until that
  certificate is issued the name is served Traefik's default one and a browser errors on it.
- Two dm.de browsers on one account is untested — see the session note above.
- The ghcr.io login the host already holds has to be able to read a package of a private
  repository. A failure is a pull error in the deploy log, before anything starts.
