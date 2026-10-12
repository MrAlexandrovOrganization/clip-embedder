"""Legacy consumer RPCs enqueue; workers connect to a separate authenticated listener."""

import logging
import os
import re
import signal
import sqlite3
import threading
import time
from concurrent import futures

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

import embed_pb2 as pb
import embed_pb2_grpc as rpc
from auth import WorkerCredentials
from logging_setup import setup
from metrics import serve as serve_metrics
from store import QueueFull, Store

LOG = logging.getLogger("clip-coordinator")
LIMIT = 16 * 1024 * 1024


class Embedder(rpc.EmbedderServiceServicer):
    def __init__(self, store):
        self.store = store

    def wait(self, kind, payload, context):
        if (
            not payload
            or len(payload) > 12 * 1024 * 1024
            or (kind == "text" and len(payload) > 65536)
        ):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid input size")
        try:
            job = self.store.submit(kind, payload)
        except QueueFull:
            context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "embedding queue full")
        except sqlite3.Error:
            context.abort(grpc.StatusCode.UNAVAILABLE, "queue unavailable")
        # The job survives RPC cancellation. Replays address the same model/input hash.
        deadline = time.monotonic() + min(context.time_remaining() or 120, 120)
        while context.is_active() and time.monotonic() < deadline:
            row = self.store.get(job)
            if row["status"] == "done":
                return pb.EmbedResponse.FromString(row["result"])
            if row["status"] == "failed":
                context.abort(grpc.StatusCode.FAILED_PRECONDITION, "embedding failed after retries")
            time.sleep(0.25)
        context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "embedding queued; retry the same input")

    def EmbedImage(self, request, context):
        return self.wait("image", request.image, context)

    def EmbedText(self, request, context):
        return self.wait("text", request.text.encode(), context)


class Workers(rpc.WorkerServiceServicer):
    def __init__(self, store, credentials):
        self.store, self.credentials = store, credentials

    def authorize(self, request, context):
        auth = [value for key, value in context.invocation_metadata() if key == "authorization"]
        identity = self.credentials.authenticate(auth[0]) if len(auth) == 1 else None
        if identity is None:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid worker credential")
        if request.worker_id != identity:
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "worker identity mismatch")
        if not re.fullmatch(r"[a-f0-9-]{36}", request.instance_id):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid instance identity")

    def Report(self, request, context):
        self.authorize(request, context)
        self.store.report(
            request.worker_id, request.instance_id, request.ready, request.model, request.pretrained
        )
        return pb.WorkerReply(accepted=True)

    def Claim(self, request, context):
        self.authorize(request, context)
        return self.store.claim(request.worker_id, request.instance_id) or pb.ClaimResponse()

    def update(self, request, context, action, result=None, permanent=False):
        self.authorize(request, context)
        try:
            accepted = self.store.finish(request, action, result, permanent)
        except ValueError:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "incompatible result")
        return pb.WorkerReply(accepted=accepted)

    def Heartbeat(self, request, context):
        return self.update(request, context, "heartbeat")

    def Complete(self, request, context):
        return self.update(request.lease, context, "complete", result=request.result)

    def Fail(self, request, context):
        return self.update(request.lease, context, "fail", permanent=request.permanent)


def main():
    setup()
    model = os.getenv("CLIP_MODEL", "ViT-B/32")
    pretrained = os.getenv("CLIP_PRETRAIN", "laion2b_s34b_b79k")
    if model != "ViT-B/32":
        raise ValueError("current consumers require 512-dimensional ViT-B/32 embeddings")
    store = Store(
        os.getenv("QUEUE_PATH", "/data/queue.sqlite3"),
        model,
        pretrained,
        os.getenv("PREFERRED_WORKER_IDS", "clip-mac-cpu,clip-wsl-cpu,clip-wsl-gpu").split(","),
    )
    credentials = WorkerCredentials.from_file(os.environ["WORKER_CREDENTIALS_FILE"])
    options = [("grpc.max_receive_message_length", LIMIT), ("grpc.max_send_message_length", LIMIT)]
    internal = grpc.server(
        futures.ThreadPoolExecutor(max_workers=32), maximum_concurrent_rpcs=32, options=options
    )
    rpc.add_EmbedderServiceServicer_to_server(Embedder(store), internal)
    hs = health.HealthServicer()
    hs.set("embed.EmbedderService", health_pb2.HealthCheckResponse.SERVING)
    health_pb2_grpc.add_HealthServicer_to_server(hs, internal)
    if not internal.add_insecure_port(os.getenv("GRPC_ADDR", "[::]:50051")):
        raise RuntimeError("consumer listener bind failed")
    workers = grpc.server(
        futures.ThreadPoolExecutor(max_workers=16), maximum_concurrent_rpcs=32, options=options
    )
    rpc.add_WorkerServiceServicer_to_server(Workers(store, credentials), workers)
    if not workers.add_insecure_port(os.getenv("WORKER_ADDR", "[::]:50057")):
        raise RuntimeError("worker listener bind failed")
    metrics = serve_metrics(store, credentials.worker_ids)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    internal.start()
    workers.start()
    LOG.info("coordinator ready")
    stop.wait()
    internal.stop(10).wait()
    workers.stop(10).wait()
    metrics.shutdown()
    metrics.server_close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Startup errors may carry signed download URLs or transport details.
        LOG.error("startup failed; check configuration, model cache and device availability")
        raise SystemExit(1) from None
