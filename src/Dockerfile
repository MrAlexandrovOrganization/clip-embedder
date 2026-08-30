# ---- builder: install deps + generate gRPC stubs ----
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

RUN pip install --no-cache-dir uv

# Resolve runtime deps into a venv (no dev tools). UV_LINK_MODE=copy so the
# venv is made of regular files and can be copied to the next stage.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen

COPY proto ./proto
COPY src ./src

# grpcio-tools is a runtime dependency here; generate stubs into src/.
RUN uv run python -m grpc_tools.protoc -I proto \
      --python_out=src --grpc_python_out=src --pyi_out=src proto/embed.proto

# ---- runtime: only the venv + application code ----
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

EXPOSE 50051

CMD ["python", "src/server.py"]
