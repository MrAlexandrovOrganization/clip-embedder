DOCKER_COMPOSE ?= docker compose
SERVICE = clip-embedder

.PHONY: install
install:
	uv sync --dev

# Regenerate Python gRPC stubs from proto/embed.proto into src/.
# proto/embed.proto is the canonical source — edit it here, then run make proto.
.PHONY: proto
proto:
	uv run python -m grpc_tools.protoc \
		-I proto \
		--python_out=src \
		--grpc_python_out=src \
		--pyi_out=src \
		proto/embed.proto

.PHONY: run
run: proto
	uv run python src/server.py

.PHONY: build
build:
	$(DOCKER_COMPOSE) build $(SERVICE)

.PHONY: up
up:
	$(DOCKER_COMPOSE) up -d --build $(SERVICE)

.PHONY: down
down:
	$(DOCKER_COMPOSE) down

.PHONY: logs
logs:
	$(DOCKER_COMPOSE) logs -f $(SERVICE)

.PHONY: deploy
deploy:
	$(DOCKER_COMPOSE) up -d --build --no-cache $(SERVICE)

.PHONY: restart
restart:
	$(DOCKER_COMPOSE) restart $(SERVICE)

# Check formatting (fails if not formatted); `make fmt` to apply.
.PHONY: format
format:
	uv run ruff format --check .

.PHONY: fmt
fmt:
	uv run ruff format .

.PHONY: lint
lint:
	uv run ruff check .

.PHONY: test
test: proto
	uv run python -c "import sys; sys.path.insert(0, 'src'); import embed_pb2, embed_pb2_grpc; print('proto stubs ok')"

.PHONY: clean
clean:
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -exec rm -rf {} +
	rm -f src/embed_pb2.py src/embed_pb2_grpc.py src/embed_pb2.pyi
