#!/usr/bin/env python3
"""A throwaway SMTP sink for the test VM: one file per message under /var/spool/test-mail.

Speaks exactly as much SMTP as msmtp uses -- EHLO, AUTH, MAIL, RCPT, DATA, QUIT -- and
accepts any credentials: the password is a generated throwaway, and what the verifier
asserts is that a message arrived and what is in it, not who sent it. No TLS; both ends are
127.0.0.1 and the host declares `tls: false` for exactly that reason.

The standard library only, deliberately: `smtpd` left it in 3.12 and `aiosmtpd` would be a
pip package on a host that is supposed to look like storagebaby. `socketserver` is enough --
SMTP is line-oriented and this end never initiates anything.
"""

import itertools
import os
import socketserver

SPOOL = "/var/spool/test-mail"
LISTEN = ("127.0.0.1", 2525)
counter = itertools.count(1)


class Handler(socketserver.StreamRequestHandler):
    def reply(self, line: str) -> None:
        self.wfile.write(line.encode() + b"\r\n")
        self.wfile.flush()

    def handle(self) -> None:
        self.reply("220 test-smtp-sink")
        body: list[str] = []
        in_data = False
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if in_data:
                if line == ".":
                    # Written under a name that sorts by arrival, so the verifier can take
                    # the newest message without guessing.
                    path = os.path.join(SPOOL, f"{next(counter):04d}.eml")
                    with open(path, "w") as fh:
                        fh.write("\n".join(body) + "\n")
                    body, in_data = [], False
                    self.reply("250 2.0.0 Ok: queued")
                else:
                    # Dot-stuffing, undone: a body line that began with a period arrives
                    # with a second one in front of it.
                    body.append(line[1:] if line.startswith("..") else line)
                continue
            verb = line.split(" ", 1)[0].upper()
            if verb == "EHLO":
                self.reply("250-test-smtp-sink")
                self.reply("250 AUTH PLAIN LOGIN")
            elif verb == "HELO":
                self.reply("250 test-smtp-sink")
            elif verb == "AUTH":
                self.reply("235 2.7.0 Authentication successful")
            elif verb in {"MAIL", "RCPT", "RSET", "NOOP"}:
                self.reply("250 2.1.0 Ok")
            elif verb == "DATA":
                in_data = True
                self.reply("354 End data with <CR><LF>.<CR><LF>")
            elif verb == "QUIT":
                self.reply("221 2.0.0 Bye")
                return
            else:
                self.reply("502 5.5.2 Command not implemented")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    os.makedirs(SPOOL, exist_ok=True)
    Server(LISTEN, Handler).serve_forever()
