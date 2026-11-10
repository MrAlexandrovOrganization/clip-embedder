"""Queue contention and disconnected scrapes must keep useful, safe diagnostics."""

import io
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import grpc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from coordinator import queue_call  # noqa: E402
from metrics import serve  # noqa: E402
from store import Store  # noqa: E402


class Aborted(Exception):
    pass


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(str(Path(self.directory.name) / "queue.sqlite3"), "ViT-B/32", "test", [])

    def test_status_reads_do_not_wait_for_a_writer_and_see_committed_state(self):
        key = self.store.submit("text", b"synthetic")
        with self.store.db() as writer:
            writer.execute("UPDATE jobs SET status='failed' WHERE id=?", (key,))
            started = time.monotonic()
            self.assertEqual(self.store.get(key)["status"], "queued")
            self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(self.store.get(key)["status"], "failed")

    def test_read_only_connection_cannot_write_or_create_a_missing_database(self):
        with self.store.db(write=False) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM jobs")
        Path(self.store.path).unlink()
        with self.assertRaises(sqlite3.OperationalError):
            self.store.get("missing")
        self.assertFalse(Path(self.store.path).exists())

    def test_busy_maps_to_retryable_rpc_and_omits_sensitive_exception_text(self):
        with self.store.db():
            with sqlite3.connect(self.store.path, timeout=0) as contender:
                try:
                    contender.execute("BEGIN IMMEDIATE")
                except sqlite3.OperationalError as error:
                    error.args = ("sensitive payload and database path",)
                    failure = error
        context = Mock()
        context.abort.side_effect = Aborted
        with (
            self.assertLogs("clip-coordinator", level="WARNING") as logs,
            self.assertRaises(Aborted),
        ):
            queue_call(context, "claim", Mock(side_effect=failure))
        self.assertEqual(context.abort.call_args.args[0], grpc.StatusCode.UNAVAILABLE)
        self.assertNotIn("sensitive", " ".join(logs.output))
        self.assertIn("retryable=True", " ".join(logs.output))

    def test_nontransient_database_error_is_not_automatically_retried(self):
        context = Mock()
        context.abort.side_effect = Aborted
        with self.assertLogs("clip-coordinator", level="ERROR"), self.assertRaises(Aborted):
            queue_call(context, "status", Mock(side_effect=sqlite3.DatabaseError("sensitive")))
        self.assertEqual(context.abort.call_args.args[0], grpc.StatusCode.INTERNAL)

    def test_disconnected_scrape_does_not_escape_as_a_stderr_traceback(self):
        class FixtureServer:
            serve_forever = Mock()

            def __init__(self, address, handler):
                self.RequestHandlerClass = handler

        with patch("metrics.ThreadingHTTPServer", FixtureServer), patch("metrics.threading.Thread"):
            server = serve(self.store, [])
        client = Mock()
        client.makefile.return_value = io.BytesIO(b"GET /metrics HTTP/1.0\r\n\r\n")
        client.sendall.side_effect = BrokenPipeError("synthetic disconnected probe")
        server.RequestHandlerClass(client, ("127.0.0.1", 1234), server)
