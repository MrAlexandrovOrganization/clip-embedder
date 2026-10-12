DOCKER_COMPOSE = docker compose
include versions.mk
-include .worker-release.env
export WORKER_CPU_IMAGE WORKER_GPU_IMAGE
ENV_FILE ?= .env
STACK_ROLE ?= server
ifneq (,$(wildcard $(ENV_FILE)))
include $(ENV_FILE)
export
endif
ifeq ($(STACK_ROLE),workers)
COMPOSE_FILE := compose.workers.yml
ifeq ($(WORKER_AUTODEPLOY),1)
COMPOSE_FILE := compose.workers.yml:compose.autoupdate.yml
endif
else
COMPOSE_FILE := docker-compose.yml
endif
export COMPOSE_FILE
DOCKER_COMPOSE += --env-file $(ENV_FILE)

SERVICE = clip-embedder

.PHONY: install
install:
	uv sync --locked --dev --extra cpu

# Install the pre-commit git hook (requires `make install` first).
.PHONY: hooks
hooks:
	uv run --locked --extra cpu pre-commit install

# Run all pre-commit hooks over the whole tree.
.PHONY: precommit
precommit:
	uv run --locked --extra cpu pre-commit run --all-files

# Regenerate Python gRPC stubs from proto/embed.proto into src/.
# proto/embed.proto is the canonical source — edit it here, then run make proto.
.PHONY: proto
proto:
	uv run --locked --extra cpu python -m grpc_tools.protoc \
		-I proto \
		--python_out=src \
		--grpc_python_out=src \
		--pyi_out=src \
		proto/embed.proto

.PHONY: run
run: proto
	uv run --locked --extra cpu python src/server.py

.PHONY: build
build:
	$(DOCKER_COMPOSE) build $(SERVICE)

.PHONY: up
up:
ifeq ($(STACK_ROLE):$(WORKER_AUTODEPLOY),workers:1)
	$(MAKE) worker-release
	$(MAKE) up-selected
else
	$(MAKE) up-selected
endif
up-selected:
	$(DOCKER_COMPOSE) up -d --build

.PHONY: down
down:
	$(DOCKER_COMPOSE) down

.PHONY: logs
logs:
	$(DOCKER_COMPOSE) logs -f

.PHONY: deploy
deploy:
	$(DOCKER_COMPOSE) up -d --build --no-cache $(SERVICE)

.PHONY: restart
restart:
	$(DOCKER_COMPOSE) restart

# Check formatting (fails if not formatted); `make fmt` to apply.
.PHONY: format
format:
	uv run --locked --extra cpu ruff check --fix --no-unsafe-fixes .
	uv run --locked --extra cpu ruff format .

.PHONY: fmt
fmt: format

.PHONY: lint
lint:
	uv run --locked --extra cpu ruff check .

.PHONY: test
test: proto
	uv run --locked --extra cpu python -c "import sys; sys.path.insert(0, 'src'); import embed_pb2, embed_pb2_grpc; print('proto stubs ok')"

.PHONY: clean
clean:
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -exec rm -rf {} +
	rm -f src/embed_pb2.py src/embed_pb2_grpc.py src/embed_pb2.pyi

.PHONY: check config-check ps versions compose-build
check: proto
	uv run --locked --extra cpu ruff check .
	uv run --locked --extra cpu ruff format --check .
config-check:
	$(DOCKER_COMPOSE) config --quiet
ps:
	$(DOCKER_COMPOSE) ps -a
versions:
	@$(foreach v,$(VERSION_VARS),printf '%s=%s\n' '$(v)' '$($(v))';)
compose-build:
	$(DOCKER_COMPOSE) build

WORKER_REGISTRY := ghcr.io/mralexandrovorganization/clip-embedder
SOURCE_REVISION ?= $(shell git rev-parse HEAD)
export WORKER_REGISTRY SOURCE_REVISION
.PHONY: worker-release publish-workers promote-workers up-selected
worker-release:
	python3 scripts/worker_release.py resolve
publish-workers:
	python3 scripts/worker_release.py publish --arch $(PUBLISH_ARCH)
promote-workers:
	python3 scripts/worker_release.py promote
