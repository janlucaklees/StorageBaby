#!/bin/sh
# Tonight's dump of the LibreChat database, run by librechat-dump.service through
# `podman exec` into the database container.
#
# A script rather than a line in the unit for two reasons: systemd expands `$` in an
# ExecStart= itself, so the `$(cat ...)` below could not live there; and this is the form
# an operator can run by hand, which is what the README's restore section leans on.
#
# The archive is a single uncompressed BSON stream, and both halves of that matter. Single
# file, so the `.tmp` and the `mv` make it atomic -- the backup sidecar may read /backups
# at any moment and a rename cannot be caught half-done. Uncompressed, because the sidecar
# snapshots it into a deduplicating repository and a compressed dump differs in every byte
# from one night to the next. `ansible/roles/service/README.md`, "The dump form", argues
# both for pg_dump; nothing in it is specific to postgres.
#
# The password reaches mongodump on its argv, which is visible to `ps` inside this
# container -- mongodump offers no file form of it. That namespace is the service user's
# own, and the service user can read the secret itself, so this exposes it to nobody who
# did not already have it.
#
# The user, database and paths are written out because config/ is copied verbatim and never
# templated. They match service.yml's config: block.
set -eu

mongodump --quiet \
	--username=librechat \
	--password="$(cat /run/secrets/mongo_password)" \
	--authenticationDatabase=admin \
	--db=LibreChat \
	--archive=/backups/LibreChat.archive.tmp

mv /backups/LibreChat.archive.tmp /backups/LibreChat.archive
