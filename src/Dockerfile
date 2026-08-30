FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev

COPY proto ./proto
COPY src ./src

# Generate gRPC stubs into src/ (grpcio-tools is a runtime dependency here).
RUN uv run python -m grpc_tools.protoc -I proto \
      --python_out=src --grpc_python_out=src --pyi_out=src proto/embed.proto

EXPOSE 50051

CMD ["uv", "run", "python", "src/server.py"]
