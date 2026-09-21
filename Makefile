-include .env

DEVTOOLS_IMAGE := storagebaby-devtools
MOLECULE_CACHE := storagebaby-molecule-cache
AGE_DIR := $(HOME)/.config/sops/age
SCENARIO ?= test-ci

# Base: repo mount only. Everything that neither drives Docker nor reads the
# operator's age key runs with this.
DEVTOOLS_RUN := docker run --rm -t \
	-v $(CURDIR):/repo -w /repo \
	-e HOME=/root \
	$(DEVTOOLS_IMAGE)

# Adds the Docker socket and the Molecule cache volume.
DEVTOOLS_RUN_DOCKER := docker run --rm -t \
	-v $(CURDIR):/repo -w /repo \
	-e HOME=/root \
	-v /var/run/docker.sock:/var/run/docker.sock \
	-v $(MOLECULE_CACHE):/root/.cache/molecule \
	$(DEVTOOLS_IMAGE)

# Adds the operator's age key directory.
DEVTOOLS_RUN_AGE := docker run --rm -t \
	-v $(CURDIR):/repo -w /repo \
	-e HOME=/root \
	-v $(AGE_DIR):/root/.config/sops/age \
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
	mkdir -p $(AGE_DIR)
	$(subst --rm -t,--rm -it,$(DEVTOOLS_RUN_AGE)) bash

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
	$(DEVTOOLS_RUN_DOCKER) sh -c 'cd tests/integration && molecule test -s $(SCENARIO)'

.PHONY: molecule
molecule:
	$(DEVTOOLS_RUN_DOCKER) sh -c 'cd tests/integration && molecule $(CMD) -s $(SCENARIO)'

.PHONY: test-clean
test-clean:
	$(DEVTOOLS_RUN_DOCKER) sh -c 'cd tests/integration && molecule destroy -s $(SCENARIO)'
	docker volume rm -f $(MOLECULE_CACHE)

.PHONY: sops
sops:
	mkdir -p $(AGE_DIR)
	$(subst --rm -t,--rm -it,$(DEVTOOLS_RUN_AGE)) sops $(FILE)

.PHONY: install-hooks
install-hooks:
	lefthook install
