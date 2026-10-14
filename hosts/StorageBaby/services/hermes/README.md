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
  `PublishPort` on the pod. No flag the vendor warns against is passed — their warning is
  about putting an unauthenticated dashboard on a LAN, which a loopback publish behind
  Traefik does not do.

`gateway run` is the command. Inside the image that process is a `sleep infinity` heartbeat
and s6 supervises the real gateway, so a crash is restarted in seconds without the
container exiting — and `storagebaby-svc restart hermes` still stops everything cleanly.

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
