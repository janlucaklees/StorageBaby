include .env

DEVTOOLS_IMAGE := storagebaby-devtools
SCENARIO ?= test-ci
DEVTOOLS_RUN := docker run --rm -t \
	-v $(CURDIR):/repo -w /repo \
	-v /var/run/docker.sock:/var/run/docker.sock \
	-v storagebaby-molecule-cache:/root/.cache/molecule \
	-v $(HOME)/.config/sops/age:/root/.config/sops/age \
	-e HOME=/root \
	$(DEVTOOLS_IMAGE)

.PHONY: pull
pull:
	rsync -aHAXvh --exclude-from='.rsyncignore' $(SERVER):$(REMOTE_PATH) ./

.PHONY: push
push:
	rsync -aHAXvh --exclude-from='.rsyncignore' ./ $(SERVER):$(REMOTE_PATH)

.PHONY: devtools
devtools:
	docker build -t $(DEVTOOLS_IMAGE) devtools

.PHONY: devtools-shell
devtools-shell:
	$(subst --rm -t,--rm -it,$(DEVTOOLS_RUN)) bash

.PHONY: format
format:
	$(DEVTOOLS_RUN) prettier --ignore-unknown --write .

.PHONY: fmt-check
fmt-check:
	$(DEVTOOLS_RUN) prettier --ignore-unknown --check .

.PHONY: test
test: test-static test-integration

.PHONY: test-static
test-static:
	$(DEVTOOLS_RUN) pytest tests/static -v

.PHONY: test-integration
test-integration:
	$(DEVTOOLS_RUN) sh -c 'cd tests/integration && molecule test -s $(SCENARIO)'

.PHONY: molecule
molecule:
	$(DEVTOOLS_RUN) sh -c 'cd tests/integration && molecule $(CMD) -s $(SCENARIO)'

.PHONY: test-clean
test-clean:
	$(DEVTOOLS_RUN) sh -c 'cd tests/integration && molecule destroy -s $(SCENARIO)'
	docker volume prune -f

.PHONY: sops
sops:
	$(subst --rm -t,--rm -it,$(DEVTOOLS_RUN)) sops $(FILE)

.PHONY: install-hooks
install-hooks:
	lefthook install
