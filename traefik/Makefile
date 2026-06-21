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

.PHONY: remove
remove:
	docker compose down

.PHONY: clean
clean:
	docker compose down --volumes

.PHONY: cert
cert:
	docker compose run --rm -it certbot
