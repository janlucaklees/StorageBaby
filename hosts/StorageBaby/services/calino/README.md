# Calino

A CalDAV and CardDAV client for the browser, against the MXroute mailbox.
<https://calino.klees.io>. Upstream: <https://github.com/Ivan-Malinovski/calino>.

Nextcloud also speaks CalDAV and is deliberately not the backend here: JLK finds its
calendar too slow, and MXroute already hosts the mail these calendars belong to.

## It is a static page, and everything follows from that

There is no Calino server. The bundle runs in the browser, holds its working data in
`localStorage` and IndexedDB, and talks CalDAV **directly** to `dav.mxroute.com`. Two
consequences shape this whole folder:

- **No volumes, no backup.** Nothing here accumulates state. What a user builds up lives in
  their own browser, and a restore means opening the page again.
- **CORS is the obstacle.** Upstream says it plainly: "a web CalDAV/CardDAV/JMAP server must
  allow requests from your Calino origin with CORS headers." MXroute sends none, and the
  app's Caddy serves static files only — it proxies nothing. So the pod runs upstream's own
  proxy beside the app, and that is why this service has two routes.

## Two routes, and neither is guarded by the platform

| route             | port | what it is                                     |
| ----------------- | ---- | ---------------------------------------------- |
| `calino.klees.io` | 8085 | the app — Caddy serving the built bundle       |
| `dav.klees.io`    | 8086 | the proxy — relays CalDAV to `dav.mxroute.com` |

**The app carries no `basic_auth`.** Calino's master password is its authentication, and a
second prompt in front of it guards nothing that is not already encrypted. The accepted
consequence: the bundle is downloadable by anyone who finds the name, and with it the
AES-256-GCM ciphertext of the accounts, so the master password is attacked offline rather
than through a login. PBKDF2 at 600,000 iterations is what makes that expensive — which
puts the whole weight on choosing a strong one. Before step 2 below it is moot, since the
bundle then carries no accounts at all.

Guarding the proxy was never an option anyway: the browser reaches it cross-origin, and a
cross-origin request carries no basic credentials, so a middleware there would 401 every
CalDAV call. It is pinned by configuration instead, and it is worth being exact about what
that buys:

- `ALLOWED_TARGETS=dav.mxroute.com` is the real limit. The proxy can fetch that host and
  nothing else, so the worst an unauthenticated caller gets is a relay to MXroute — where
  they still need an MXroute password.
- `ALLOWED_ORIGINS` is a courtesy. A browser reports `Origin` honestly; a script sends
  whatever it likes. It stops another _site_ from using this proxy, not another program.

The proxy answers `/health`, which is both its health check and the route's `probe_path` —
to this service a path is a URL-encoded target, so `/` is a malformed request rather than a
home page.

This service declares no secrets at all, and needs none: the accounts live encrypted inside
the bundle, and the proxy holds no credential of its own.

## The image is built here, and the accounts are baked into it

This is the awkward part of the design, and it is upstream's, not the platform's: the
accounts are compiled into the JS bundle at build time, encrypted with a master password.
`docs/SELF_HOSTED_CONFIG.md` over there is blunt about the consequence — "anyone who loads
the page gets the encrypted config in the JS bundle" — which is by design, and why the
master password has to be a good one. It is AES-256-GCM with PBKDF2 at 600,000 iterations.

So `config/build/Containerfile` mirrors upstream's two stages: pnpm and vite produce the
bundle, Caddy serves it. **That mirroring is a maintenance cost to accept knowingly**: when
upstream changes its build, this file has to follow. The source tag is pinned, so it follows
on a version bump rather than by surprise.

### The two-step bootstrap

The config file cannot be generated outside a browser, so the first deploy does not have it:

1. **Now.** No `calino.config.json` in `config/build/`. The Containerfile notices and
   builds without it, giving upstream's default behaviour: each browser is told the CalDAV
   account once, and keeps it. A working service.
2. **Later.** Open `https://calino.klees.io/setup`, enter the MXroute account, choose a
   master password, and save the generated `calino.config.json` into
   `hosts/StorageBaby/services/calino/config/build/`. Commit it — it is ciphertext, and
   sops would add friction without adding secrecy. The next converge rebuilds the image and
   the app then asks for the master password instead.

Step 2 repeats for every account change: the config is in the bundle, so changing it is a
rebuild. That is the price of "open it anywhere and it works".

## What to check after a deploy

```sh
curl -skI https://calino.klees.io/ | head -1 # the app is serving
curl -sk https://dav.klees.io/health         # the proxy is up
```

The last one is only observable from the browser: open the app, unlock it, and see whether a
calendar loads. A `/health` that answers while CalDAV fails means `ALLOWED_TARGETS` or the
account's URL is wrong, not that the proxy is down.
