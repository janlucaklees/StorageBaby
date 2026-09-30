"""The scanner path: FTP through Traefik into Paperless's consume directory.

Almost one test, on purpose. Splitting "can log in", "can upload" and "the document
appeared" apart would let the first two pass on a platform where nothing is ever
ingested -- and the thing this replaces (`paperless-upload`) was exactly a service that
looked healthy while doing nothing. What is split off is the one added capability, because
it is the reason the part runs at all and a failure there deserves its own sentence.

The client is `curl` on the VM, aimed at the VM's own routable address on 21: the TCP
entrypoints bind `tcp_bind_address` and nothing listens on `127.0.0.1:21`, so
loopback is not a shortcut here -- it is the wrong address. This is the path a printer on
the LAN takes.
"""

import re
import time

import pytest
from conftest import run_as
from test_service import HOSTS, SPECS, load_spec, placed_tcp_entries, quadlets, volume_path
from test_traefik import traefik_tcp_bind_address, vm_address

# A plain text file and not a PDF, deliberately. What is under test is the path -- an FTP
# login, a passive transfer through a second entrypoint, a file appearing in a directory
# another container polls, an ingest, a deletion. A PDF adds OCRmyPDF and tesseract to that
# list, on a VM where they are the slowest thing in the pod, and makes a transport failure
# and a parser failure the same red. paperless-ngx ingests `text/plain` with its own
# parser, and that is one document row either way.
PROBE = "storagebaby-ftp-probe.txt"
# A nonce in the body, not decoration: paperless-ngx checksums an incoming file and refuses
# one that matches a document it already holds ("It is a duplicate of ..."), and with
# `PAPERLESS_CONSUMER_DELETE_DUPLICATES` at its default it leaves the rejected file in the
# consume directory. With a constant payload a second `verify` against a VM left up by
# `converge` -- the documented iteration loop -- would fail both closing assertions at once
# and read exactly like a broken consumer. The cost is one more document and one more
# `/tmp` file per run on a VM that gets destroyed, which is the right side of that trade.
PAYLOAD = "Storagebaby FTP probe document, run %s.\\nUploaded by the integration suite.\\n"

COUNT_SQL = "select count(*) from documents_document"
# `227 Entering Passive Mode (a,b,c,d,p1,p2)` out of curl's protocol trace.
PASV_REPLY = re.compile(r"227 [^(]*\((\d+,\d+,\d+,\d+),(\d+),(\d+)\)")


def paperless(host):
    """(spec, host vars, spec path) for the paperless placed on this VM, or skip.

    The spec path is the placed one -- a symlink into `hosts/storagebaby` on a test host --
    so the template read below is the file that host really renders.
    """
    hostname = host.check_output("uname -n")
    for owner, spec_path in SPECS:
        if spec_path.parent.name == "paperless" and owner in ("shared", hostname):
            return load_spec(spec_path), load_spec(HOSTS / hostname / "host.yml"), spec_path
    pytest.skip("paperless is not placed on this host")


def documents(host) -> int:
    """How many documents paperless holds, asked of its database rather than its API.

    `/api/documents/` needs a user and a token, and inventing a superuser on a test host
    to read a count is a production hazard for a value the database states plainly. The
    row count is the same fact: the consumer creates exactly one row per ingested file.
    """
    r = run_as(
        host,
        "svc-paperless",
        f"podman exec paperless-database psql -U paperless -d paperless -tAc '{COUNT_SQL}'",
    )
    assert r.rc == 0, r.stderr
    return int(r.stdout.strip())


def ftp_password(host) -> str:
    """The password the running container got, read out of its own environment.

    Not decrypted here: the point is the value the service is really running with, and a
    test that read the sops file would pass while the container ran with something else.
    """
    r = run_as(host, "svc-paperless", "podman exec paperless-ftp printenv FTP_USER_PASS")
    assert r.rc == 0 and r.stdout.strip(), f"paperless-ftp runs without FTP_USER_PASS: {r.stderr}"
    return r.stdout.strip()


def control_port(host) -> int:
    entries = [e for e in placed_tcp_entries(host) if e[0] == "paperless"]
    assert entries, "paperless declares no tcp ports on this host"
    # The lowest is the control port; everything above it is the passive range.
    return min(port for _, port, _ in entries)


def test_the_ftp_part_runs_with_the_one_added_capability(host):
    """`AddCapability=AUDIT_WRITE` is in the template, and the container really has it.

    Under podman's default capability set this image exits 252 with no diagnostic anywhere
    -- pure-ftpd logs to syslog and there is none in the container. The missing capability
    is CAP_AUDIT_WRITE, which is in Docker's default set and not in podman's. A unit that
    lost the line would leave a container restarting forever; the generic health and unit
    checks would catch that, but not why, and the next person would bisect it again.

    It is also the claim that this is not a privilege: the capability is in
    `svc-paperless`'s own user namespace, which the kernel's audit subsystem does not
    answer to, exactly like `AddCapability=MKNOD` on nextcloud's Collabora.
    """
    _, _, spec_path = paperless(host)
    template = next(t for t in quadlets(spec_path, "container") if t.name.startswith("paperless-ftp"))
    assert "AddCapability=AUDIT_WRITE" in template.read_text(), template
    r = run_as(host, "svc-paperless", "podman inspect paperless-ftp --format '{{.EffectiveCaps}}'")
    assert r.rc == 0, r.stderr
    assert "CAP_AUDIT_WRITE" in r.stdout, r.stdout


def test_an_ftp_upload_becomes_a_document(host):
    spec, hostvars, _ = paperless(host)
    port = control_port(host)
    bind = traefik_tcp_bind_address(host)
    address = vm_address(host)
    consume = volume_path(hostvars, spec, "consume")

    # Bound once: it is read out of the running container, so an inline call would be a
    # second `podman exec` per interpolation -- and it is the value scrubbed out of the
    # failure messages below.
    password = ftp_password(host)
    before = documents(host)
    r = host.run(f"printf '{PAYLOAD}' \"$(date +%s%N)\" > /tmp/{PROBE}")
    assert r.rc == 0, r.stderr
    # `--disable-epsv` is the flag that makes this say something about the advertised
    # address: curl tries EPSV first, and a `229` reply carries no address at all, so the
    # whole `-P` half of the unit would go unmeasured. `--no-ftp-skip-pasv-ip` then stops
    # curl from *ignoring* the address it was given, which is its default. Between them the
    # data connection really goes where the server said -- which is what an older scanner's
    # client does, and what fails silently when `-P` is wrong.
    # The two `-Q` commands are the chroot assertion, and they carry `-` so curl sends them
    # *after* the transfer -- nothing about the upload can depend on them -- and `*` so a
    # refusal does not fail the transfer either. Both outcomes prove the same thing: from a
    # virtual user's home, `CWD ..` either goes nowhere or is denied, and `PWD` answers `/`.
    # It is the one security property of the FTP drop (`-l puredb:` plus pure-ftpd's default
    # chroot for virtual users) that nothing else here measures.
    r = host.run(
        f"curl -sS -v --ftp-pasv --disable-epsv --no-ftp-skip-pasv-ip "
        f"--connect-timeout 20 --max-time 120 -Q '-*CWD ..' -Q '-*PWD' "
        f"-T /tmp/{PROBE} ftp://{spec['config']['ftp_user']}:{password}@{address}:{port}/"
    )
    # `curl -v` writes its whole FTP command trace to stderr, `PASS <the real password>`
    # included, and these failure messages end up in the Molecule output and in a CI job log.
    trace = r.stderr.replace(password, "***")
    assert r.rc == 0, f"the upload to {address}:{port} failed: {trace}"

    found = PASV_REPLY.search(trace)
    assert found, f"no 227 reply in curl's trace:\n{trace}"
    advertised = found.group(1).replace(",", ".")
    assert advertised == bind, f"the server advertised {advertised}, Traefik binds {bind}"
    data_port = int(found.group(2)) * 256 + int(found.group(3))
    # This service's range and not the host's: `placed_tcp_entries` answers for every placed
    # service, so an unfiltered set would accept a data connection that landed on another
    # service's entrypoint -- `tcp-echo`'s 7777 on both test hosts -- and would print
    # "outside 7777-21109", which is not a range.
    passive = sorted(p for name, p, _ in placed_tcp_entries(host) if name == "paperless" and p != port)
    assert passive, "paperless declares no passive range on this host"
    assert data_port in passive, f"the data connection went to {data_port}, outside {passive[0]}-{passive[-1]}"
    assert re.search(r'257 "/"', trace), f"the account is not chrooted to its home:\n{trace}"

    # Polled: the consumer wakes on its polling interval, then parses and files the
    # document. Two minutes is generous for a one-line text file on this VM.
    deadline = time.monotonic() + 120
    after = before
    while time.monotonic() < deadline and after <= before:
        time.sleep(5)
        after = documents(host)
    if after <= before:
        journal = host.run("journalctl _SYSTEMD_USER_UNIT=paperless-app.service --no-pager | tail -60").stdout
        pytest.fail(f"no document appeared after the upload ({before} -> {after})\n{journal}")

    # And the inbox is empty again: the consumer deletes what it ingested, which is what
    # keeps the volume a spool and out of the backup paths.
    left = host.run(f"ls -1 {consume}").stdout.strip()
    assert left == "", f"{consume} still holds {left!r}"
