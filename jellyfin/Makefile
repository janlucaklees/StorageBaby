.PHONY: context
context:
	docker context use default

.PHONY: configs
configs:

.PHONY: secrets
secrets:

.PHONY: deploy
deploy:
	docker stack deploy --detach -c docker-stack.yml jellyfin

.PHONY: ps
ps:
	docker stack ps jellyfin
