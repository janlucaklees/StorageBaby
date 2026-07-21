export TZ=Europe/Berlin

.PHONY: start
start:
	docker compose up -d

.PHONY: ps
ps:
	docker compose ps

.PHONY: logs
logs:
	docker compose logs -f

.PHONY: stop
stop:
	docker compose stop

.PHONY: down
remove:
	docker compose down

.PHONY: clear
clear:
	docker compose down --volumes
