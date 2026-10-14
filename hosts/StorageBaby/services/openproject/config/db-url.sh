# The database URL, composed from the one secret that holds the password.
#
# Sourced, never executed: every process that talks to the database reads this instead of
# carrying its own copy of the string. That is three callers -- the web unit, the worker
# unit, and the `db:seed` hook -- and the hook is the reason this file exists rather than
# the `export` living in each unit's `Exec=`.
#
# `podman exec` starts a process with the *container's configured* environment, which is
# `Environment=` and `Secret=…,type=env` and nothing else. A variable exported by the
# entrypoint shell belongs to pid 1 alone, so a hook that ran `rake db:seed` found no
# DATABASE_URL and Rails fell back to a local unix socket:
#
#   ActiveRecord::ConnectionNotEstablished: connection to server on socket
#   "/var/run/postgresql/.s.PGSQL.5432" failed: No such file or directory
#
# Measured on test-a, not reasoned about.
#
# The user and database name are written out because `config/` is copied verbatim and never
# templated. They match `service.yml`'s `config:` block, and they are the same on every host
# this folder is placed on.
#
# The password must be URL-safe, which is why every generated password here is hex: a value
# containing `@`, `:` or `/` would silently produce a different connection.
export DATABASE_URL="postgres://openproject:$(cat /run/secrets/database_password)@127.0.0.1/openproject?pool=20&encoding=unicode&reconnect=true"
