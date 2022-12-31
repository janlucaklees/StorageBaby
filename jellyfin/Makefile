PUID = $(shell id -u jellyfin)
PGID = $(shell id -g jellyfin)

# The base command to start the docker setup.
DC = PGID=$(PGID) PUID=$(PUID) docker-compose


.PHONY: up
up:
	$(DC) up

.PHONY: daemon
daemon:
	$(DC) up -d

.PHONY: logs
logs:
	$(DC) logs -f watchtower

.PHONY: down
down:
	$(DC) down

.PHONY: clear
clear:
	$(DC) down -v

.PHONY: start
start:
	$(DC) start

.PHONY: stop
stop:
	$(DC) stop

