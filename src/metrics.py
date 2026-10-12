"""Private Prometheus snapshot; no media, text, task IDs or bearer credentials."""

import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from store import sql

COUNTERS = {
    "completed": "compute_jobs_completed_total",
    "failed": "compute_jobs_failed_total",
    "attempt_failed": "compute_attempt_failures_total",
    "retry": "compute_retry_attempts_total",
    "lease_expired": "compute_lease_expirations_total",
}


def quantile(values, fraction):
    values = sorted(values)
    position = (len(values) - 1) * fraction
    left = int(position)
    right = min(left + 1, len(values) - 1)
    return values[left] + (values[right] - values[left]) * (position - left)


def render(store, identities):
    now = time.time()
    lines = []

    def metric(name, kind="gauge"):
        lines.append(f"# TYPE {name} {kind}")

    def emit(name, value, **labels):
        labels = {"service": "clip-embedder", **labels}
        attributes = ",".join(key + "=" + json.dumps(str(value)) for key, value in labels.items())
        lines.append(name + "{" + attributes + "} " + str(value))

    # A scrape never takes the queue's writer lock and never modifies its data.
    with sqlite3.connect("file:" + store.path + "?mode=ro", uri=True, timeout=3) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        states = dict(db.execute(sql("metrics_jobs")))
        metric("compute_jobs")
        for state in ("queued", "running", "done", "failed"):
            emit("compute_jobs", states.get(state, 0), status=state)
        metric("compute_queue_oldest_wait_seconds")
        emit(
            "compute_queue_oldest_wait_seconds",
            db.execute(sql("metrics_wait"), (now, now)).fetchone()[0],
        )
        metric("compute_expired_jobs")
        emit("compute_expired_jobs", db.execute(sql("metrics_expired"), (now,)).fetchone()[0])
        workers = {
            row[0]: row[1:]
            for row in db.execute(
                sql("metrics_workers"),
                (
                    now - 90,
                    store.model,
                    store.pretrained,
                    now,
                    now - 90,
                    store.model,
                    store.pretrained,
                ),
            )
        }
        active = {row[0]: row[1:] for row in db.execute(sql("metrics_active"), (now, now))}
        for name in (
            "compute_worker_online",
            "compute_worker_state",
            "compute_worker_last_report_age_seconds",
            "compute_worker_instances",
            "compute_worker_active_jobs",
            "compute_worker_attempt_age_seconds",
            "compute_fallback_allowed",
            "compute_model_info",
        ):
            metric(name)
        fallback = 1
        for worker in sorted(identities):
            online, age, instances = workers.get(worker, (0, None, 0))
            count, attempt_age = active.get(worker, (0, 0))
            role = "preferred" if worker in store.preferred else "fallback"
            if online and role == "preferred":
                fallback = 0
            labels = {"worker_id": worker, "worker_role": role}
            emit("compute_worker_online", online, **labels)
            emit("compute_worker_state", 2 if online and count else int(bool(online)), **labels)
            emit("compute_worker_instances", instances, **labels)
            emit("compute_worker_active_jobs", count, **labels)
            emit("compute_worker_attempt_age_seconds", attempt_age, **labels)
            if age is not None:
                emit("compute_worker_last_report_age_seconds", max(0, age), **labels)
        emit("compute_fallback_allowed", fallback)
        emit("compute_model_info", 1, model=store.model, pretrained=store.pretrained)
        counters = {event: dict.fromkeys([*identities, "other"], 0) for event in COUNTERS}
        for worker, event, value in db.execute(sql("metrics_counters")):
            worker = worker if worker in identities else "other"
            counters[event][worker] += value
        for event, name in COUNTERS.items():
            metric(name, "counter")
            for worker, value in counters[event].items():
                emit(name, value, worker_id=worker)
        samples = {}
        for worker, processing, turnaround in db.execute(sql("metrics_duration"), (now - 86400,)):
            if worker in identities:
                samples.setdefault(worker, ([], []))[0].append(processing)
                samples[worker][1].append(turnaround)
        metric("compute_job_processing_seconds")
        metric("compute_job_turnaround_seconds")
        for worker, (processing, turnaround) in samples.items():
            for fraction in (0.5, 0.95):
                emit(
                    "compute_job_processing_seconds",
                    quantile(processing, fraction),
                    worker_id=worker,
                    quantile=fraction,
                )
                emit(
                    "compute_job_turnaround_seconds",
                    quantile(turnaround, fraction),
                    worker_id=worker,
                    quantile=fraction,
                )
    return "\n".join(lines) + "\n"


def serve(store, identities):
    host = os.getenv("METRICS_HOST", "127.0.0.1")
    port = 9110
    slots = threading.BoundedSemaphore(4)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.connection.settimeout(10)
            if self.path != "/metrics":
                self.send_error(404)
                return
            if not slots.acquire(blocking=False):
                self.send_error(503)
                return
            try:
                body = render(store, identities).encode()
            except Exception:
                self.send_error(503, "queue metrics unavailable")
                return
            finally:
                slots.release()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="metrics").start()
    return server
