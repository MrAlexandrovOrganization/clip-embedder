# check=skip=InvalidDefaultArgInFrom
ARG PYTHON_VERSION
FROM python:${PYTHON_VERSION}-slim AS builder
ARG UV_VERSION
ARG TORCH_EXTRA=cpu
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /app
RUN pip install --no-cache-dir uv==${UV_VERSION}
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --extra ${TORCH_EXTRA}
COPY proto ./proto
COPY src ./src
RUN .venv/bin/python -m grpc_tools.protoc -I proto --python_out=src --grpc_python_out=src --pyi_out=src proto/embed.proto
FROM python:${PYTHON_VERSION}-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src PATH="/app/.venv/bin:$PATH" HF_HOME=/cache
WORKDIR /app
RUN useradd --uid 10001 --create-home app && mkdir /data /cache && chown app:app /data /cache && chmod 1777 /data /cache
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
USER app
CMD ["python", "src/coordinator.py"]

FROM runtime AS remote-cpu
CMD ["python", "src/worker.py"]
FROM runtime AS remote-gpu
CMD ["python", "src/worker.py"]

# The queue/router uses the same dependency lock, without inference packages.
FROM python:${PYTHON_VERSION}-slim AS coordinator-builder
ARG UV_VERSION
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /app
RUN pip install --no-cache-dir uv==${UV_VERSION}
COPY pyproject.toml uv.lock ./
RUN uv export --locked --no-dev --prune open-clip-torch --prune pillow --prune numpy --format requirements-txt --output-file /tmp/coordinator.txt \
    && uv venv /app/.venv \
    && uv pip sync --python /app/.venv/bin/python --require-hashes /tmp/coordinator.txt
COPY proto ./proto
COPY src ./src
RUN .venv/bin/python -m grpc_tools.protoc -I proto --python_out=src --grpc_python_out=src --pyi_out=src proto/embed.proto
FROM python:${PYTHON_VERSION}-slim AS coordinator
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data && chmod 1777 /data
COPY --from=coordinator-builder /app/.venv /app/.venv
COPY --from=coordinator-builder /app/src /app/src
USER app
CMD ["python", "src/coordinator.py"]
