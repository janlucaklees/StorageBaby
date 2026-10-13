# ext-echo

BusyBox's `httpd` on port 8118, behind `ext-echo.<domain>`, serving a static page from
its own `config/www`.

`greeting` is a secret it never reads: it exists only to prove the external secrets
path -- that a pointer service's `secrets.sops.yaml` is read from the fetched tree.
