"""One inference at a time, independent readiness reports and renewable fenced leases."""

import io
import logging
import os
import signal
import threading
import time
import uuid
from pathlib import Path

import grpc
import open_clip
import torch
from PIL import Image, UnidentifiedImageError

import embed_pb2 as pb
import embed_pb2_grpc as rpc
from logging_setup import setup

LOG = logging.getLogger("clip-worker")


def main():
    setup()
    worker = os.environ["WORKER_ID"]
    instance = str(uuid.uuid4())
    token = Path(os.environ["WORKER_TOKEN_FILE"]).read_text().strip()
    if not 32 <= len(token) <= 512 or any(not 33 <= ord(char) <= 126 for char in token):
        raise ValueError("invalid worker token")
    endpoint = os.environ["WORKER_ENDPOINT"]
    model_name = os.getenv("CLIP_MODEL", "ViT-B/32")
    pretrained = os.getenv("CLIP_PRETRAIN", "laion2b_s34b_b79k")
    device = os.getenv("DEVICE", "cpu")
    if device not in ("cpu", "cuda") or (device == "cuda" and not torch.cuda.is_available()):
        raise ValueError("configured device unavailable")
    torch.set_num_threads(int(os.getenv("CPU_THREADS", "2")))
    model, _, preprocess = open_clip.create_model_and_transforms(model_name, pretrained=pretrained)
    model = model.to(device).eval()
    tokenizer = open_clip.get_tokenizer(model_name)
    options = [
        ("grpc.max_receive_message_length", 16 * 1024 * 1024),
        ("grpc.max_send_message_length", 16 * 1024 * 1024),
    ]
    if os.getenv("WORKER_TRANSPORT", "tls") == "tls":
        channel = grpc.secure_channel(endpoint, grpc.ssl_channel_credentials(), options=options)
    elif endpoint == "clip-embedder:50057":
        channel = grpc.insecure_channel(endpoint, options=options)
    else:
        raise ValueError("plaintext allowed only inside server compose network")
    stub = rpc.WorkerServiceStub(channel)
    metadata = (("authorization", "Bearer " + token),)
    stop = threading.Event()
    draining = threading.Event()
    busy = threading.Event()
    ready_path = Path("/tmp/worker-ready")
    drained_path = Path("/tmp/worker-drained")
    ready_path.unlink(missing_ok=True)
    drained_path.unlink(missing_ok=True)

    def drain(*_):
        draining.set()
        ready_path.unlink(missing_ok=True)
        if not busy.is_set():
            drained_path.touch()

    def resume(*_):
        drained_path.unlink(missing_ok=True)
        draining.clear()

    signal.signal(signal.SIGUSR1, drain)
    signal.signal(signal.SIGUSR2, resume)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    def report():
        while not stop.is_set():
            try:
                stub.Report(
                    pb.ReportRequest(
                        worker_id=worker,
                        instance_id=instance,
                        ready=not draining.is_set(),
                        model=model_name,
                        pretrained=pretrained,
                    ),
                    timeout=10,
                    metadata=metadata,
                )
                if not draining.is_set():
                    ready_path.touch()
                else:
                    ready_path.unlink(missing_ok=True)
            except grpc.RpcError as exc:
                ready_path.unlink(missing_ok=True)
                LOG.warning("readiness report failed: %s", exc.code().name)
            stop.wait(20)

    reporter = threading.Thread(target=report, daemon=True)
    reporter.start()
    LOG.info(
        "worker ready id=%s model=%s pretrained=%s device=%s",
        worker,
        model_name,
        pretrained,
        device,
    )
    try:
        while not stop.is_set():
            if draining.is_set():
                drained_path.touch()
                stop.wait(1)
                continue
            busy.set()
            try:
                job = stub.Claim(
                    pb.ClaimRequest(worker_id=worker, instance_id=instance),
                    timeout=10,
                    metadata=metadata,
                )
                if not job.job_id:
                    stop.wait(5)
                    continue
                lease = pb.LeaseRequest(
                    worker_id=worker, instance_id=instance, job_id=job.job_id, lease_id=job.lease_id
                )
                done = threading.Event()
                lost = threading.Event()

                def heartbeat(current=lease):
                    while not done.wait(20):
                        try:
                            if not stub.Heartbeat(current, timeout=10, metadata=metadata).accepted:
                                lost.set()
                                return
                        except grpc.RpcError:
                            # A lost response is retried; completion still enforces expiry.
                            pass

                renewal = threading.Thread(target=heartbeat, daemon=True)
                renewal.start()
                started = time.monotonic()
                try:
                    if (job.model, job.pretrained) != (model_name, pretrained):
                        raise ValueError("incompatible model")
                    with torch.inference_mode():
                        if job.kind == "image":
                            with Image.open(io.BytesIO(job.image)) as source:
                                image = source.convert("RGB")
                            tensor = preprocess(image).unsqueeze(0).to(device)
                            vector = model.encode_image(tensor)
                        elif job.kind == "text":
                            vector = model.encode_text(tokenizer([job.text]).to(device))
                        else:
                            raise ValueError("unsupported task kind")
                        vector = (
                            (vector / vector.norm(dim=-1, keepdim=True)).cpu().flatten().tolist()
                        )
                    result = pb.EmbedResponse(
                        vector=vector, dim=len(vector), model=model_name, pretrained=pretrained
                    )
                    if not lost.is_set():
                        # Retry the same fenced completion while heartbeat continues.
                        for _ in range(6):
                            try:
                                reply = stub.Complete(
                                    pb.CompleteRequest(lease=lease, result=result),
                                    timeout=10,
                                    metadata=metadata,
                                )
                                LOG.info(
                                    "attempt complete accepted=%s duration_seconds=%.3f",
                                    reply.accepted,
                                    time.monotonic() - started,
                                )
                                break
                            except grpc.RpcError:
                                if stop.wait(5):
                                    break
                except (ValueError, UnidentifiedImageError, Image.DecompressionBombError):
                    stub.Fail(
                        pb.FailRequest(lease=lease, permanent=True), timeout=10, metadata=metadata
                    )
                except Exception:
                    # Do not log input, model output, remote error details or bearer metadata.
                    LOG.warning("inference attempt failed")
                    stub.Fail(
                        pb.FailRequest(lease=lease, permanent=False), timeout=10, metadata=metadata
                    )
                finally:
                    done.set()
                    renewal.join(11)
            except grpc.RpcError as exc:
                LOG.warning("coordinator request failed: %s", exc.code().name)
                stop.wait(5)
            finally:
                busy.clear()
                if draining.is_set():
                    drained_path.touch()
    finally:
        stop.set()
        ready_path.unlink(missing_ok=True)
        reporter.join(11)
        try:
            stub.Report(
                pb.ReportRequest(
                    worker_id=worker,
                    instance_id=instance,
                    ready=False,
                    model=model_name,
                    pretrained=pretrained,
                ),
                timeout=5,
                metadata=metadata,
            )
        except grpc.RpcError:
            pass
        channel.close()


if __name__ == "__main__":
    main()
