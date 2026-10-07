#!/usr/bin/env python3
"""A throwaway git server for the test VM: /srv/git over HTTP with basic auth, on 127.0.0.1:8418.

It speaks git's *dumb* HTTP protocol, which is static files -- a bare repository whose
`info/refs` and `objects/info/packs` are kept current by `git update-server-info` (the
repository's `post-update` hook runs it on every push). That is enough for a clone and a
fetch, and it is the standard library only: a smart server would mean `git http-backend`
behind a CGI runner, on a host that is supposed to look like storagebaby.

The deploy script's `GIT_ASKPASS` path is what this exists for: every request without a
valid token is answered 401 with a Basic challenge, which is what makes git ask for
credentials and present the host's token for that pointer. Tokens are read from the credentials
file on *every* request, one per line, so a test can revoke one by editing the file and
put it back afterwards, without restarting anything. The username is not checked -- GitHub
ignores it for a token too, and the script sends `x-access-token`.
"""

import base64
import http.server

ROOT = "/srv/git"
CREDENTIALS = "/etc/test-git-http/credentials"
LISTEN = ("127.0.0.1", 8418)


def allowed(header: str | None) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        _, _, password = base64.b64decode(header[6:]).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    with open(CREDENTIALS) as fh:
        tokens = {line.strip() for line in fh if line.strip()}
    return password in tokens


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def challenge(self) -> bool:
        if allowed(self.headers.get("Authorization")):
            return False
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="test-git-http"')
        self.end_headers()
        return True

    def do_GET(self) -> None:
        if not self.challenge():
            super().do_GET()

    def do_HEAD(self) -> None:
        if not self.challenge():
            super().do_HEAD()


class Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    Server(LISTEN, Handler).serve_forever()
