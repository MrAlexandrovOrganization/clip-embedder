"""Single-coordinator durable queue. SQLite WAL lives on a persistent local volume."""

import hashlib
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import embed_pb2 as pb

QUERIES = {path.stem: path.read_text() for path in Path(__file__).with_name("sql").glob("*.sql")}


def sql(name):
    return QUERIES[name]


class QueueFull(Exception):
    pass


class Store:
    def __init__(self, path, model, pretrained, preferred):
        self.model, self.pretrained = model, pretrained
        self.preferred = tuple(preferred)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with sqlite3.connect(path) as initial:
            initial.execute("PRAGMA journal_mode=WAL")
        with self.db() as db:
            db.executescript(Path(__file__).with_name("schema.sql").read_text())
        # Ordered additive migrations share the queue's writer lock. Execute
        # complete statements individually: executescript would commit the lock.
        with self.db() as db:
            for migration in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
                if db.execute(sql("migration_applied"), (migration.name,)).fetchone():
                    continue
                statement = ""
                for line in migration.read_text().splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        db.execute(statement)
                        statement = ""
                if statement.strip():
                    raise ValueError("incomplete queue migration")
                db.execute(sql("migration_record"), (migration.name,))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute(sql("query_01"))
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def submit(self, kind, payload):
        key = hashlib.sha256(
            (kind + "\0" + self.model + "\0" + self.pretrained + "\0").encode() + payload
        ).hexdigest()
        now = time.time()
        with self.db() as db:
            row = db.execute(sql("query_10"), (key,)).fetchone()
            if row:
                db.execute(sql("query_08"), (now, key))
                return key
            count = db.execute(sql("query_18")).fetchone()[0]
            if count >= 512:
                raise QueueFull()
            db.execute(
                sql("query_02"),
                (key, kind, payload, now, now, now),
            )
        return key

    def get(self, key):
        with self.db() as db:
            return db.execute(sql("query_11"), (key,)).fetchone()

    def report(self, worker, instance, ready, model, pretrained):
        now = time.time()
        with self.db() as db:
            db.execute(sql("query_03"), (now - 86400,))
            db.execute(
                sql("query_04"),
                (now - 30 * 86400,),
            )
            db.execute(
                sql("query_05"),
                (worker, instance, int(ready), model, pretrained, now),
            )

    def claim(self, worker, instance):
        now = time.time()
        with self.db() as db:
            ready = db.execute(
                sql("query_12"),
                (worker, instance, now - 90, self.model, self.pretrained),
            ).fetchone()
            if not ready:
                return None
            if worker not in self.preferred:
                for preferred in self.preferred:
                    if db.execute(
                        sql("query_19"),
                        (preferred, now - 90, self.model, self.pretrained),
                    ).fetchone():
                        return None
            if db.execute(
                sql("query_13"),
                (worker, instance, now),
            ).fetchone():
                return None
            db.execute(
                sql("query_06"),
                (now,),
            )
            row = db.execute(
                sql("query_14"),
                (now, now),
            ).fetchone()
            if not row:
                return None
            lease = str(uuid.uuid4())
            db.execute(
                sql("query_07"),
                (worker, instance, lease, now + 90, row["id"]),
            )
            return pb.ClaimResponse(
                job_id=row["id"],
                lease_id=lease,
                kind=row["kind"],
                image=row["payload"] if row["kind"] == "image" else b"",
                text=row["payload"].decode() if row["kind"] == "text" else "",
                model=self.model,
                pretrained=self.pretrained,
            )

    def finish(self, lease, action, result=None, permanent=False):
        now = time.time()
        with self.db() as db:
            row = db.execute(
                sql("query_15"),
                (lease.job_id, lease.worker_id, lease.instance_id, lease.lease_id),
            ).fetchone()
            if not row:
                return False
            if action == "complete" and row["status"] == "done":
                return bytes(row["result"]) == result.SerializeToString()
            if row["status"] != "running" or row["expires"] <= now:
                return False
            if action == "heartbeat":
                db.execute(sql("query_09"), (now + 90, lease.job_id))
            elif action == "complete":
                if (
                    result.model != self.model
                    or result.pretrained != self.pretrained
                    or result.dim != 512
                    or len(result.vector) != 512
                ):
                    raise ValueError("incompatible embedding result")
                if not all(math.isfinite(value) for value in result.vector):
                    raise ValueError("nonfinite embedding result")
                db.execute(
                    sql("query_16"),
                    (result.SerializeToString(), now, lease.job_id),
                )
            else:
                terminal = permanent or row["attempts"] >= 5
                db.execute(
                    sql("query_17"),
                    (
                        "failed" if terminal else "queued",
                        now + min(300, 5 * 2 ** row["attempts"]),
                        terminal,
                        lease.job_id,
                    ),
                )
            return True
