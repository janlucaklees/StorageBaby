IMAGE := storagebaby-devtools

.PHONY: build fmt fmt-check install-hooks

build:
	docker build -t $(IMAGE) devtools/

fmt: build
	docker run --rm -v $(CURDIR):/repo -w /repo $(IMAGE) prettier --ignore-unknown --write .

fmt-check: build
	docker run --rm -v $(CURDIR):/repo -w /repo $(IMAGE) prettier --ignore-unknown --check .

install-hooks:
	lefthook install
