-include .env

DEVTOOLS_IMAGE := storagebaby-devtools
MOLECULE_CACHE := storagebaby-molecule-cache
AGE_DIR := $(HOME)/.config/sops/age
SCENARIO ?= test-ci
LIBVIRT_SOCK := /var/run/libvirt/libvirt-sock
LIBVIRT_IMAGES := /var/lib/libvirt/images

# Base: repo mount only. Everything that neither drives Docker nor reads the
# operator's age key runs with this.
DEVTOOLS_RUN := docker run --rm -t \
	-v $(CURDIR):/repo -w /repo \
	-e HOME=/root \
	$(DEVTOOLS_IMAGE)

# Adds the host's libvirt socket, the libvirt images directory and the Molecule
# cache volume. Test hosts are KVM VMs driven through libvirt, so the container
# itself stays unprivileged; --network host is needed because libvirt's NAT
# firewall rejects new connections from the Docker bridge to the VM network.
DEVTOOLS_RUN_VM := docker run --rm -t --network host \
	-v $(CURDIR):/repo -w /repo \
	-v $(LIBVIRT_SOCK):$(LIBVIRT_SOCK) \
	-v $(LIBVIRT_IMAGES):$(LIBVIRT_IMAGES) \
	-v $(MOLECULE_CACHE):/root/.cache/molecule \
	-e HOME=/root \
	-e MOLECULE_EPHEMERAL_DIRECTORY=/root/.cache/molecule/$(SCENARIO) \
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
	$(DEVTOOLS_RUN_VM) sh -c 'cd tests/integration && molecule test -s $(SCENARIO)'

.PHONY: molecule
molecule:
	$(DEVTOOLS_RUN_VM) sh -c 'cd tests/integration && molecule $(CMD) -s $(SCENARIO)'

# molecule login opens an interactive SSH session, so it needs a stdin the
# generic molecule target's `--rm -t` cannot give it.
.PHONY: molecule-login
molecule-login:
	$(subst --rm -t,--rm -it,$(DEVTOOLS_RUN_VM)) sh -c 'cd tests/integration && molecule login -s $(SCENARIO)'

.PHONY: test-clean
test-clean:
	$(DEVTOOLS_RUN_VM) sh -c 'cd tests/integration && molecule destroy -s $(SCENARIO)'
	docker volume rm -f $(MOLECULE_CACHE)

.PHONY: sops
sops:
	mkdir -p $(AGE_DIR)
	$(subst --rm -t,--rm -it,$(DEVTOOLS_RUN_AGE)) sops $(FILE)

.PHONY: install-hooks
install-hooks:
	lefthook install
