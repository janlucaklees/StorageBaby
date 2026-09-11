MAKEFILE_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

export TZ=Europe/Berlin
export DOMAIN=nextcloud.home.klees.io
export COLLABORA_DOMAIN=collabora.home.klees.io

export TRAEFIK_CONTAINER_IP:=$(shell docker inspect --format '{{ .NetworkSettings.Networks.traefik_network.IPAddress }}' $(shell docker ps -aqf "name=traefik"))

export VERSION=33-fpm-alpine

export DATABASE_NAME=nextcloud
export DATABASE_USER=oc_jlk

# This is a crutch as Collabora does not support docker secrets.
export COLLABORA_USERNAME:=$(shell cat $(MAKEFILE_DIR)collabora_username.secret)
export COLLABORA_PASSWORD:=$(shell cat $(MAKEFILE_DIR)collabora_password.secret)

DATABSE_SNAPSHOT_PATH=$(MAKEFILE_DIR)snapshot.sql

.PHONY: start
start:
	docker compose up -d nextcloud
	sleep 5
	docker compose up -d nginx

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

.PHONY: sh
sh:
	docker compose exec nextcloud sh

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
	docker compose up -d nextcloud
	docker compose exec --no-TTY                 nextcloud chown -R www-data:www-data /var/www/html
	docker compose exec --no-TTY --user www-data nextcloud php occ upgrade
	docker compose exec --no-TTY --user www-data nextcloud php occ maintenance:mode --off
	docker compose exec --no-TTY --user www-data nextcloud php occ db:add-missing-columns
	docker compose exec --no-TTY --user www-data nextcloud php occ db:add-missing-indices
	docker compose exec --no-TTY --user www-data nextcloud php occ db:add-missing-primary-keys
	docker compose exec --no-TTY --user www-data nextcloud php occ maintenance:repair --include-expensive
	$(MAKE) start
	docker image prune
