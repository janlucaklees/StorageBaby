MAKEFILE_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

export TZ=Europe/Berlin
export DOMAIN=openarchiver.home.klees.io

export VERSION=v0.6.0

export DATABASE_NAME=openarchiver
export DATABASE_USER=openarchiver

# OpenArchiver doesn't support docker secrets (`_FILE` env vars) for any of
# these, so - like Nextcloud's Collabora credentials - they're read from
# gitignored .secret files into plain env vars instead.
export DATABASE_PASSWORD:=$(shell cat $(MAKEFILE_DIR)openarchiver_database_password.secret)
export REDIS_PASSWORD:=$(shell cat $(MAKEFILE_DIR)openarchiver_redis_password.secret)
export MEILI_MASTER_KEY:=$(shell cat $(MAKEFILE_DIR)openarchiver_meili_master_key.secret)
export JWT_SECRET:=$(shell cat $(MAKEFILE_DIR)openarchiver_jwt_secret.secret)
export ENCRYPTION_KEY:=$(shell cat $(MAKEFILE_DIR)openarchiver_encryption_key.secret)
export STORAGE_ENCRYPTION_KEY:=$(shell cat $(MAKEFILE_DIR)openarchiver_storage_encryption_key.secret)

SECRET_FILES := \
	$(MAKEFILE_DIR)openarchiver_database_password.secret \
	$(MAKEFILE_DIR)openarchiver_redis_password.secret \
	$(MAKEFILE_DIR)openarchiver_meili_master_key.secret \
	$(MAKEFILE_DIR)openarchiver_jwt_secret.secret \
	$(MAKEFILE_DIR)openarchiver_encryption_key.secret \
	$(MAKEFILE_DIR)openarchiver_storage_encryption_key.secret

DATABSE_SNAPSHOT_PATH=$(MAKEFILE_DIR)snapshot.sql

.PHONY: secrets
secrets: $(SECRET_FILES)

$(SECRET_FILES):
	@umask 077; set -C; openssl rand -hex 32 > '$@'
	@echo 'Created $(notdir $@)'

.PHONY: start
start:
	docker compose up -d

.PHONY: ps
ps:
	docker compose ps

.PHONY: logs
logs:
	docker compose logs -f

.PHONY: restart
restart:
	docker compose restart

.PHONY: pull
pull:
	docker compose pull

.PHONY: stop
stop:
	docker compose stop

.PHONY: remove
remove:
	docker compose down


.PHONY: database_snapshot
database_snapshot:
	docker compose exec --no-TTY database pg_dump \
		--role=${DATABASE_USER} \
		--username=${DATABASE_USER} \
		--dbname=${DATABASE_NAME} \
		--format=tar \
		--no-security-labels \
		--no-owner \
		--verbose \
		> ${DATABSE_SNAPSHOT_PATH}

.PHONY: database_restore
database_restore:
	docker compose up -d database
	sleep 5
	docker compose exec --no-TTY database pg_restore \
		--role=${DATABASE_USER} \
		--username=${DATABASE_USER} \
		--dbname=${DATABASE_NAME} \
		--format=tar \
		--no-security-labels \
		--no-owner \
		--verbose \
		< ${DATABSE_SNAPSHOT_PATH}


.PHONY: upgrade
upgrade:
	$(MAKE) pull
	$(MAKE) remove
	$(MAKE) start
	docker image prune
