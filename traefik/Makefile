.PHONY: context
context:
	docker context use default

.PHONY: configs
configs:

.PHONY: secrets
secrets:
	docker secret create traefik_ui_basicauth traefik_ui_basicauth.secret

.PHONY: start
start:
	docker compose up -d

.PHONY: ps
ps:
	docker compose ps

.PHONY: stop
stop:
	docker compose stop

.PHONY: remove
remove:
	docker compose down

.PHONY: clean
clean:
	docker compose down --volumes
