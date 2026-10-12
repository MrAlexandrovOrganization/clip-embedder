# clip-embedder distributed workers

## Architecture

The existing EmbedImage/EmbedText gRPC API enqueues an idempotent job into a
persistent SQLite WAL volume. Repeated input/model/checkpoint resolves the same
job. A caller deadline does not cancel queued work; retrying the same input can
receive the stored result. Queue responses wait at most 120 seconds. Stash's
missing-vector backfill retries independently, so worker absence does not lose
indexing work. Interactive searches may time out when no worker is available;
they do not produce delayed Telegram messages.

Workers load the exact ViT-B/32 / laion2b_s34b_b79k space (512 dimensions) and
execute one inference at a time. CPU and CUDA images use the same model,
checkpoint and pinned torch version. Floating point results may differ slightly
between devices. Changing embedding space requires a coordinated reindex and
consumer update; worker reports with a different space cannot claim jobs or
suppress the fallback. Responses include the pretrained checkpoint identity.

SQLite is a single-coordinator design, not replicated HA. The queue-data volume
must be included in backups; use sqlite3 backup API or stop the coordinator while
copying the database/WAL together. Never run multiple coordinators on the same
volume or delete the volume on deployment. This queue does not make server
storage or Caddy fault tolerant.

## Worker lifecycle and security

Workers poll outbound gRPC/TLS via Caddy every five seconds. Readiness is reported
every 20 seconds, expires after 90 seconds, and is scoped to a token's worker ID.
Claimed tasks use random lease IDs, renew every 20 seconds, and become claimable
again after expiry. Completion validates owner/instance/lease and rejects stale
results. Failed attempts back off, with at most five attempts. Queued jobs have
no TTL; terminal results are retained for 30 days since last access.

Mac/WSL ready workers suppress the server fallback, including while busy. The
fallback takes jobs only when all configured preferred worker IDs are unavailable
with the required models. Models must be present before a worker reports ready.
The server stores SHA-256 token hashes in a protected credential registry;
workers mount their own raw tokens as read-only secrets. Never commit real .env,
tokens or registry files. WORKER_UID/WORKER_GID must match the owner of token
files so non-root workers can read mode-600 secrets.

Only WorkerService is public through Caddy. Consumer APIs/database/cache ports
are private. The shared workers hostname routes distinct protobuf services;
no SSH tunnels or public inbound ports on Mac/WSL are needed.

## Running

Copy the matching .env.server.example, .env.mac.example or .env.wsl.example to
.env and fill machine paths/endpoint. The same make up/down/logs/restart/ps
commands select the server or worker Compose stack. .env sets models, IDs,
profiles, resource limits, tokens and autodeploy. make config-check validates it.

Mac enables COMPOSE_PROFILES=cpu; WSL enables cpu,gpu. CUDA requires Docker Desktop
WSL GPU integration. A 2 GB MX550 also running Whisper has little spare VRAM;
keep concurrency one and disable the gpu profile if allocation fails. Model
weights download to the named model-cache volume on the first worker startup.

## CI and autodeploy

CI uses Make to check, build and publish immutable CPU (amd64/arm64) and CUDA (amd64)
images to public GHCR packages. Only after the server deployment succeeds is the
stable release marker promoted. WORKER_AUTODEPLOY=1 enables the container updater:
it downloads the approved digest, drains a busy worker, starts the replacement,
waits for Docker health, and rolls back on startup failure. Offline devices catch
up after returning. Machine .env and token mounts are preserved. The updater has
Docker socket access and manages only explicitly labelled workers in its own
Compose project. Its bootstrap source is vendored from transcriber's approved
updater at cd4dcb2393fdc159f76cb98570f99b724edc2384, adapted for these images.

make install installs pinned tools/dependencies; make format applies formatting;
make check is read-only analysis. Models, inference, production queues and Telegram
are not used by ordinary checks. make versions shows tool/image pins. Version
sources are versions.mk, go.mod or pyproject.toml/uv.lock. Standalone worker
health reflects readiness reports; docker logs includes structured lifecycle,
attempt outcome and duration without media, descriptions or bearer credentials.
