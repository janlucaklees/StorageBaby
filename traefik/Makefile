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

.PHONY: upgrade
upgrade:
	$(MAKE) pull
	$(MAKE) remove
	$(MAKE) start
	docker image prune
