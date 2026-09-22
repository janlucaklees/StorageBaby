# StorageBaby

Git-driven configuration for my self-hosted services and the Arch hosts they
run on. Design: `docs/superpowers/specs/2026-09-21-gitops-podman-platform-design.md`.

## Layout

- `hosts/<host>/host.yml` — everything specific to one host (domain, storage
  roots, later disks/snapraid/samba).
- `hosts/<host>/services/<name>/` — a service placed on that host.
- `hosts/shared/services/<name>/` — a service that runs on every host.
- `ansible/` — the site playbook and roles that turn the above into a running host.
- `tests/` — static checks and the Molecule integration scenario.

Each service folder holds `service.yml` (port, domain, volumes, secrets,
backup policy), `quadlet/*.j2` (Podman Quadlet units), optional `config/`, and
`secrets.sops.yaml`.

## New host

Copy the script to the fresh Arch install and run it as root:

    scp bootstrap.sh root@<host>:/root/ && ssh root@<host> 'bash /root/bootstrap.sh --repo git@github.com:janlucaklees/StorageBaby.git'

`stable` is created by CI on the first green push to master, so push and let CI
run before bootstrapping the first host.

Protect `stable` on GitHub: no direct pushes, no force pushes, no deletion — it
is moved by CI alone. GitHub Actions must still be allowed to push to it: the
`promote` job fast-forwards `stable` with the workflow token, so if "restrict
who can push" is enabled, add the Actions actor to the allow list or promotion
stops there.

The host's hostname must equal its folder name under `hosts/` — the deploy unit
converges `--limit <hostname>` and fails loudly if no such folder exists.

Add the printed deploy key to the repository, then add the printed age recipient
to `.sops.yaml` in two places: the host's own rule, and the `hosts/shared/**`
rule — every host runs the shared services and has to decrypt their secrets.
Then run `sops updatekeys` on every affected `*.sops.yaml` —
`make sops FILE=...` opens one for editing — then commit and push. The host
pulls `stable` every 5 minutes.

Until the host's age recipient is in `.sops.yaml` and `sops updatekeys` has run
on the secret files it needs, its first converge fails at secret decryption.
That is expected: the host cannot read anything it was not encrypted to.

## Operating a service

Every service runs as its own lingering user `svc-<name>`, so its units belong
to that user's systemd manager, not the system one. The Makefile wraps that —
run these on the host as root (they are the only targets that do not go through
the devtools image):

    make ps SERVICE=traefik
    make start SERVICE=traefik
    make stop SERVICE=traefik
    make restart SERVICE=traefik
    make logs SERVICE=traefik

They are thin wrappers, so the raw forms still work:

    systemctl --user -M svc-traefik@ status traefik.service
    systemctl --user -M svc-traefik@ restart traefik.service

`journalctl` has no `--user -M` equivalent, so read logs by unit name instead:

    journalctl _SYSTEMD_USER_UNIT=traefik.service -f

Nothing is ever changed on a host by hand — this is for looking, and for the
occasional restart. The fix belongs in git.

## Working on the repo

    make devtools              # build the tooling image (once)
    make format                # prettier over the whole repo
    make fmt-check             # check only, no writes
    make test-static           # contract, secrets, render checks
    make test-integration      # Molecule scenario test-ci in a KVM VM
    make molecule CMD=converge # a single Molecule step in that scenario
    make molecule-login        # SSH into the running test VM
    make test-clean            # destroy the VM and drop the Molecule cache
    make sops FILE=hosts/shared/services/traefik/secrets.sops.yaml
    make install-hooks         # once per clone: lefthook's formatting hook

Docker and lefthook are all the workstation needs for everything but the
integration tests. Those drive real KVM machines through the host's libvirt, so
they additionally need `qemu-base libvirt dnsmasq iptables-nft`, `libvirtd`
enabled, and libvirt's `default` network active. On a machine that also runs
Docker, set `firewall_backend = "iptables"` in `/etc/libvirt/network.conf` —
with the nftables backend Docker's rules drop the VM network's traffic.
