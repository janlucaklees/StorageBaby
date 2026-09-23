-include .env

# `-include .env` makes these make variables, not environment ones, and the bare
# `-e MOLECULE_*` pass-through below forwards only what is already in the
# environment -- so without this, a value set in .env never reached the container and
# Molecule silently fell back to its defaults.
# MOLECULE_HOST picks which host folder the integration scenario converges (default
# `test-a`, CI uses `test-ci`); it is exported here so `.env` can set it too, and
# `MOLECULE_HOST=test-ci make test-integration` works because make re-exports what it
# inherited.
export MOLECULE_VM_MEMORY_MIB MOLECULE_VM_VCPUS MOLECULE_HOST

DEVTOOLS_IMAGE := storagebaby-devtools
MOLECULE_CACHE := storagebaby-molecule-cache
AGE_DIR := $(HOME)/.config/sops/age
SCENARIO ?= test-ci
LIBVIRT_SOCK := /var/run/libvirt/libvirt-sock
LIBVIRT_IMAGES := /var/lib/libvirt/images

# Molecule's per-scenario state inside the cache volume. It writes the inventory it
# generates for the running VM to $(MOLECULE_EPHEMERAL)/inventory, which is what
# molecule-exec below hands to Ansible.
MOLECULE_EPHEMERAL := /root/.cache/molecule/$(SCENARIO)

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
	-e MOLECULE_EPHEMERAL_DIRECTORY=$(MOLECULE_EPHEMERAL) \
	-e MOLECULE_VM_MEMORY_MIB -e MOLECULE_VM_VCPUS -e MOLECULE_HOST \
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

# The non-interactive counterpart: one command on the running test VM, for scripts,
# CI and anything without a terminal. `molecule login` needs a real TTY, so without
# this the only way to look at a VM from a script is a throwaway testinfra test.
# Ansible's ad hoc mode against the inventory Molecule already wrote is enough --
# it carries the VM's address and key, so there is no second source of truth.
# CMD is pasted into a single-quoted shell word, so a command containing a single
# quote needs `make molecule-login` instead.
.PHONY: molecule-exec
molecule-exec:
	@[ -n "$(CMD)" ] || { \
		echo "molecule-exec: CMD is required, e.g. make molecule-exec CMD='podman ps -a'" >&2; \
		exit 2; \
	}
	$(DEVTOOLS_RUN_VM) ansible all -i $(MOLECULE_EPHEMERAL)/inventory -m shell -a '$(CMD)'

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

# Host-side service control. Unlike everything above, these run on a host as root
# and not in the devtools image: a service's units belong to the `svc-<name>`
# user's systemd manager, which is reachable only from that host.
define require_service
	@[ -n "$(SERVICE)" ] || { \
		echo "$@: SERVICE is required, e.g. make $@ SERVICE=traefik" >&2; \
		exit 2; \
	}
endef

# A multi-container service is one pod unit with its containers pulled in behind it;
# a single-container service has no pod at all. Which of the two a service is cannot be
# read off its name, so it is asked of the service user's manager: `$(call with_unit,
# <command using $$unit>)`. grep and not the exit status of list-unit-files, because
# that command is happy to list nothing.
define with_unit
	unit=$(SERVICE).service; \
	if systemctl --user -M svc-$(SERVICE)@ list-unit-files $(SERVICE)-pod.service 2> /dev/null \
		| grep -q '^$(SERVICE)-pod.service'; then \
		unit=$(SERVICE)-pod.service; \
	fi; \
	$(1)
endef

.PHONY: start stop restart
start stop restart:
	$(require_service)
	$(call with_unit,systemctl --user -M svc-$(SERVICE)@ $@ "$$unit")

.PHONY: ps
ps:
	$(require_service)
	$(call with_unit,systemctl --user -M svc-$(SERVICE)@ status "$$unit")

# journalctl has no `--user -M` form, so units are addressed by name instead.
.PHONY: logs
logs:
	$(require_service)
	$(call with_unit,journalctl _SYSTEMD_USER_UNIT="$$unit" -f)
