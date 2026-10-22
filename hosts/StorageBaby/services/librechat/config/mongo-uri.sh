# The database URL, composed from the one secret that holds the password.
#
# Sourced by the app unit's Exec=, never executed: LibreChat reads one MONGO_URI and no
# file form of it, so the choice is between this and a `type=env` secret carrying the whole
# string -- which would mean the password lived in the secrets file twice, once alone for
# the dump and once inside a URL. openproject's config/db-url.sh is the same shape and its
# comment carries the error that produced it.
#
# Inside a pod the parts share one network namespace, so the database is 127.0.0.1 and not
# the container name upstream's compose uses.
#
# authSource=admin because the user is the one mongod's own entrypoint created, and it
# creates it in the admin database.
#
# The user and database name are written out because config/ is copied verbatim and never
# templated. They match service.yml's config: block, and they are the same on every host
# this folder is placed on.
#
# The password must be URL-safe: a value containing @, : or / would silently produce a
# different connection, or none. Hence hex -- README, "The secrets, and how to fill them".
export MONGO_URI="mongodb://librechat:$(cat /run/secrets/mongo_password)@127.0.0.1:27017/LibreChat?authSource=admin"
