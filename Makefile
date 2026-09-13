include .env

.PHONY: pull
pull:
	rsync -aHAXvh --exclude-from='.rsyncignore' $(SERVER):$(REMOTE_PATH) ./

.PHONY: push
push:
	rsync -aHAXvh --exclude-from='.rsyncignore' ./ $(SERVER):$(REMOTE_PATH)

.PHONY: format
format:
	docker run --rm -v ./:/repo -w /repo storagebaby-devtools prettier --ignore-unknown --write .

.PHONY: fmt-check
fmt-check:
	docker run --rm -v ./:/repo -w /repo storagebaby-devtools prettier --ignore-unknown --check .

.PHONY: install-hooks
install-hooks:
	lefthook install
