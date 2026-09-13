MAKEFILE_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
ODYSSEUS_REPO := https://github.com/odysseus-dev/odysseus.git

export TZ=Europe/Berlin
export DOMAIN=odysseus.home.klees.io

# Single source of truth for which upstream commit is pinned. `start` and
# `update` both clone/check out src/ against this file on whatever host
# runs them - local dev box and the no-.git rsync target alike.
export ODYSSEUS_VERSION:=$(shell cat $(MAKEFILE_DIR)ODYSSEUS_VERSION)

.PHONY: ensure-src
ensure-src:
	@[ -d $(MAKEFILE_DIR)src/.git ] || git clone $(ODYSSEUS_REPO) $(MAKEFILE_DIR)src
	git -C $(MAKEFILE_DIR)src fetch origin $(ODYSSEUS_VERSION)
	git -C $(MAKEFILE_DIR)src checkout $(ODYSSEUS_VERSION)

.PHONY: start
start: ensure-src
	docker compose up -d

.PHONY: update
update:
ifndef REF
	$(error Usage: make update REF=<commit-sha>)
endif
	echo $(REF) > $(MAKEFILE_DIR)ODYSSEUS_VERSION
	$(MAKE) start

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
down:
	docker compose down

.PHONY: clean
clean:
	docker compose down --volumes
	rm -rf $(MAKEFILE_DIR)volumes
	rm -rf /pool/apps/odysseus/volumes
