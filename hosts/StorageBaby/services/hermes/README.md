# Hermes

Nous Research's self-hosted agent. <https://hermes.home.klees.io> — private, so a VPN
client or another service on this host, and nothing from the internet.
Upstream: <https://github.com/NousResearch/hermes-agent>.

This replaced OpenClaw, which the spec originally carried: JLK judged Hermes the better
fit and dropped the comparison. Nothing of OpenClaw's reasoning survives here except the
shape — an agent that is itself a service, so the MCP adapters it uses can live beside it
rather than on a laptop.

## One container, and no host networking

Upstream's compose runs two containers, both with `network_mode: host`. Neither is needed:

- **One container.** `HERMES_DASHBOARD=1` registers the dashboard as an s6-supervised
  service alongside the gateway in the same container, which is what the image's own docker
  guide recommends. The dashboard then reaches the gateway's API over the container's
  loopback and nothing has to be published for it.
- **No host networking.** This platform reserves that for Traefik. Hermes does not need it:
  every messaging channel it speaks is an outbound connection, and `HERMES_DASHBOARD_HOST`
  already defaults to `0.0.0.0`, so the dashboard is reachable through an ordinary
  `PublishPort` on the pod.

`gateway run` is the command. Inside the image that process is a `sleep infinity` heartbeat
and s6 supervises the real gateway, so a crash is restarted in seconds without the
container exiting — and `storagebaby-svc restart hermes` still stops everything cleanly.

## The dashboard's login is mandatory, and three secrets are what start it

Not a policy decision taken here — upstream's, enforced in its code, and it cost a debugging
round to find out, so it is written down. Two guards do it:

- **The auth gate.** It engages on any non-loopback bind, and if no auth provider is
  registered the dashboard _fails closed at startup_. There is no unauthenticated public
  bind: "to keep it local, bind to `127.0.0.1` and reach it over an SSH tunnel or
  Tailscale." The failure mode is not a login page, it is a dashboard that never starts —
  s6 supervises a dead service, nothing listens on 9119, and the health check kills the
  container every ten seconds. That was the first measured state of this folder.
- **The DNS-rebinding guard.** It accepts the bound address and the host of
  `HERMES_DASHBOARD_PUBLIC_URL` as an HTTP `Host` and a WebSocket `Origin`, exactly, with no
  wildcards. Traefik forwards the browser's `Host`, and the chat is a WebSocket, so without
  that variable the page would load and the chat would close with 4403.

Which rules out the shape this folder would otherwise have had: bind the dashboard to the
container's loopback and let Traefik be the only thing in front of it. Declaring the public
URL a proxied browser needs engages the gate by itself, "even when the backend binds to
loopback" — deliberately, so that the local SPA's session token cannot become a remote
authentication mechanism through a proxy.

So the bundled username/password provider is configured — the one upstream recommends for a
homelab container behind a VPN, and the only one of its three that needs no identity
provider:

| secret                  | what it is                                                        |
| ----------------------- | ----------------------------------------------------------------- |
| `dashboard_user`        | the login name, in sops beside the password rather than in a unit |
| `dashboard_password`    | the password, plaintext; the image takes a scrypt hash too        |
| `dashboard_session_key` | signs the session tokens — without it every restart logs you out  |

The VPN-only route is therefore the second lock, not the only one. `basic_auth` on the route
would be a third and is deliberately absent: it would prompt before a login page that is
already there, and it would break the dashboard's own WebSocket, which carries no basic
credentials.

## No chat channel yet, by choice

Hermes supports about thirty channels — Telegram, WhatsApp, Matrix, Discord, Slack, SMS,
e-mail and more. **Signal is not among them**, which is worth recording because it was the
channel chosen while OpenClaw was the candidate.

None is configured. JLK wanted to try the agent before deciding how to reach it, so the
dashboard is the only surface. Adding one later is a dashboard step plus, for most channels,
one secret — no change to this folder's shape.

## The provider key lives in the volume, not in a secret

Hermes takes model provider keys either from the environment — `OPENROUTER_API_KEY`,
`GOOGLE_API_KEY` and a long list of others — or from the dashboard. The dashboard is used
here on purpose: it lets a provider be swapped without a commit, which is what "try it and
see" needs.

The consequence is worth knowing rather than discovering: that key is then part of `data`,
and `data` is snapshotted nightly, so it reaches the kopia repository. If a provider key
ever needs to be out of backups, it moves to a `secrets` entry and a `Secret=…,type=env`
line, and this paragraph changes.

## Volumes

| volume | class | owner | what it holds                                                     |
| ------ | ----- | ----- | ----------------------------------------------------------------- |
| `data` | fast  | 10000 | the profile, memory, self-written skills, sessions, provider keys |

`10000` is `HERMES_UID`'s own default, set explicitly in the unit. The image runs as root
and its s6 init remaps an internal user to that uid, then drops each supervised service to
it — so the files end up owned by 10000 regardless, and declaring it is what makes the
role's one-time chown agree instead of fighting it.

## Reaching the MCP hub

Not configured yet, and it is the next thing worth doing: Hermes is an MCP client, with a
catalogue of about a hundred vendor integrations under `optional-mcps/` and a plain way to
add one of your own. A remote server is a `mcp_servers.<name>` entry with a
`transport: {type: http, url: …}` and a `headers` map for the bearer token — so the hub is:

```yaml
mcp_servers:
  storagebaby:
    transport:
      type: http
      url: https://mcp.home.klees.io/hub
    headers:
      Authorization: Bearer <token from the hub's admin CLI>
```

`/hub` rather than a single adapter: it aggregates every server behind six meta-tools, which
is the one endpoint to register when a client wants one MCP server rather than four. The
token is created on the host and printed once — `hosts/StorageBaby/services/mcp/README.md`,
"Connecting a client".

That configuration lives in the profile inside `data`, which Hermes writes itself from the
dashboard. So it is an operator step, not a file this folder renders — the same situation as
Home Assistant's `configuration.yaml`.

## Backup

`data` is the whole service, snapshotted at 03:00. There is no database and no dump timer:
Hermes keeps its state in SQLite inside that tree, and the snapshot carries it.
