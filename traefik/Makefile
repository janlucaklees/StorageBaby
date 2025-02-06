.PHONY: context
context:
	docker context use default

.PHONY: configs
configs:

.PHONY: secrets
secrets:
	docker secret create traefik_ui_basicauth traefik_ui_basicauth.secret

.PHONY: deploy
deploy:
	docker stack deploy --detach -c docker-stack.yml traefik

.PHONY: ps
ps:
	docker stack ps traefik
