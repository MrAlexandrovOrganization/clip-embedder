DOCKER_COMPOSE = docker compose
include Makefiles/versions.mk
-include .worker-release.env
-include .server-release.env
export WORKER_CPU_IMAGE WORKER_GPU_IMAGE
ENV_FILE ?= .env
STACK_ROLE ?= server
ifneq (,$(wildcard $(ENV_FILE)))
include $(ENV_FILE)
export
endif
ifeq ($(STACK_ROLE),workers)
COMPOSE_FILE := compose/workers.yml
ifeq ($(WORKER_AUTODEPLOY),1)
COMPOSE_FILE := compose/workers.yml:compose/workers.autodeploy.yml
endif
else
COMPOSE_FILE := compose/server.yml
endif
export COMPOSE_FILE STACK_ROLE WORKER_AUTODEPLOY COMPOSE_PROFILES
DOCKER_COMPOSE += --project-directory "$(CURDIR)" --env-file $(ENV_FILE)

SERVICE = clip-embedder

.PHONY: install
install:
	uv sync --locked --dev --extra cpu

# Install the pre-commit git hook (requires `make install` first).
.PHONY: install-hooks hooks
install-hooks:
	@test -z "$$(git config --get core.hooksPath)" || { echo "core.hooksPath is already configured" >&2; exit 1; }
	@test ! -e "$$(git rev-parse --git-path hooks/pre-commit)" && test ! -L "$$(git rev-parse --git-path hooks/pre-commit)" && test ! -e "$$(git rev-parse --git-path hooks/pre-commit).legacy" && test ! -L "$$(git rev-parse --git-path hooks/pre-commit).legacy" || { echo "pre-commit hook already exists" >&2; exit 1; }
	uv run --locked --extra cpu pre-commit install

hooks: install-hooks

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
	uv run --locked --extra cpu python src/coordinator.py

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
up-selected: config-check
	$(DOCKER_COMPOSE) up -d --build
ifeq ($(STACK_ROLE),server)
	$(MAKE) monitoring-install
endif

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

.PHONY: format
format:
	uv run --locked --extra cpu ruff check --fix --no-unsafe-fixes --force-exclude .
	uv run --locked --extra cpu ruff format --force-exclude .

.PHONY: test
test: proto
	uv run --locked --extra cpu python -m unittest discover -s tests -v

.PHONY: clean
clean:
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -exec rm -rf {} +
	rm -f src/embed_pb2.py src/embed_pb2_grpc.py src/embed_pb2.pyi

.PHONY: check config-check ps versions compose-build
check: test
	uv run --locked --extra cpu ruff check --force-exclude .
	uv run --locked --extra cpu ruff format --check --force-exclude .
config-check:
	python3 scripts/check_config.py $(DOCKER_COMPOSE)
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

.PHONY: monitoring-check monitoring-install
monitoring-install:
	@test '$(STACK_ROLE)' = server
	python3 scripts/install_monitoring.py
monitoring-check:
	python3 -c 'import json; from pathlib import Path; doc=json.loads(Path("monitoring/prometheus/clip-embedder.yml").read_text()); assert doc == [{"targets":["clip-metrics:9110"],"labels":{"service":"clip-embedder"}}]'
check: monitoring-check

.PHONY: config-check-examples
config-check-examples:
	$(MAKE) ENV_FILE=.env.server.example config-check
	$(MAKE) ENV_FILE=.env.mac.example config-check
	$(MAKE) ENV_FILE=.env.wsl.example config-check

check: config-check-examples

# CI deploys the already built amd64 artifact by immutable digest.
.PHONY: server-image deploy-image
server-image:
	python3 scripts/server_image.py
deploy-image: config-check
	@python3 -c 'import os,re; assert re.fullmatch(re.escape(os.environ["WORKER_REGISTRY"]+"-worker-cpu")+r"@sha256:[a-f0-9]{64}",os.environ.get("SERVER_IMAGE","")), "SERVER_IMAGE must be the published immutable digest"'
	$(DOCKER_COMPOSE) up -d --no-build --pull always --wait --wait-timeout 300
	$(MAKE) monitoring-install
	python3 scripts/server_image.py record
export SERVER_IMAGE
