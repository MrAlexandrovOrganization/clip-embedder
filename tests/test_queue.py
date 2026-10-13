"""Synthetic checks: no model downloads, network calls or production storage."""

import hashlib
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import embed_pb2 as pb  # noqa: E402
from auth import WorkerCredentials  # noqa: E402
from store import Store  # noqa: E402
from transport import worker_channel  # noqa: E402

MODEL, PRETRAIN = "ViT-B/32", "laion2b_s34b_b79k"


class CredentialsTests(unittest.TestCase):
    def test_scoped_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            token = "synthetic-token-for-one-worker-only"
            path.write_text(
                json.dumps(
                    {"version": 1, "workers": {"one": hashlib.sha256(token.encode()).hexdigest()}}
                )
            )
            credentials = WorkerCredentials.from_file(path)
            self.assertEqual(credentials.authenticate("Bearer " + token), "one")
            self.assertIsNone(credentials.authenticate("Bearer " + "x" * 32))
            path.write_text('{"version":1,"workers":{"":"' + "0" * 64 + '"}}')
            with self.assertRaises(ValueError):
                WorkerCredentials.from_file(path)

    def test_transport_fails_closed(self):
        for transport, endpoint in (
            ("ssh", "clip-embedder:50057"),
            ("internal", "example.com:443"),
            ("typo", "clip-embedder:50057"),
        ):
            with self.assertRaises(ValueError):
                worker_channel(endpoint, transport)
        with patch("transport.grpc.secure_channel") as secure:
            worker_channel("example.com:443")
            secure.assert_called_once()


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "queue.sqlite3")
        self.store = Store(self.path, MODEL, PRETRAIN, ["preferred"])
        self.instance = str(uuid.uuid4())

    def report(self, worker="preferred", ready=True):
        self.store.report(worker, self.instance, ready, MODEL, PRETRAIN)

    def lease(self, claim, worker="preferred"):
        return pb.LeaseRequest(
            job_id=claim.job_id,
            lease_id=claim.lease_id,
            worker_id=worker,
            instance_id=self.instance,
        )

    def test_persistence_and_idempotent_replay(self):
        job = self.store.submit("text", b"synthetic input")
        self.store = Store(self.path, MODEL, PRETRAIN, ["preferred"])
        self.assertEqual(job, self.store.submit("text", b"synthetic input"))
        self.report()
        claim = self.store.claim("preferred", self.instance)
        result = pb.EmbedResponse(vector=[1.0] * 512, dim=512, model=MODEL, pretrained=PRETRAIN)
        lease = self.lease(claim)
        self.assertTrue(self.store.finish(lease, "complete", result))
        self.assertTrue(self.store.finish(lease, "complete", result))
        self.store = Store(self.path, MODEL, PRETRAIN, ["preferred"])
        self.assertEqual(self.store.get(job)["status"], "done")
        self.assertEqual(job, self.store.submit("text", b"synthetic input"))

    def test_fallback_and_model_compatibility(self):
        self.store.submit("text", b"synthetic input")
        self.report()
        self.report("fallback")
        self.assertIsNone(self.store.claim("fallback", self.instance))
        self.report(ready=False)
        self.assertIsNotNone(self.store.claim("fallback", self.instance))

    def test_expired_lease_is_fenced(self):
        self.store.submit("text", b"synthetic input")
        self.report()
        with patch("store.time.time", return_value=2000000000):
            self.report()
            first = self.store.claim("preferred", self.instance)
        with patch("store.time.time", return_value=2000000091):
            self.report()
            second = self.store.claim("preferred", self.instance)
            self.assertNotEqual(first.lease_id, second.lease_id)
            self.assertFalse(self.store.finish(self.lease(first), "heartbeat"))
            self.assertTrue(self.store.finish(self.lease(second), "heartbeat"))

    def test_invalid_result_does_not_complete(self):
        self.store.submit("text", b"synthetic input")
        self.report()
        claim = self.store.claim("preferred", self.instance)
        with self.assertRaises(ValueError):
            self.store.finish(self.lease(claim), "complete", pb.EmbedResponse())
        self.assertTrue(self.store.finish(self.lease(claim), "fail", permanent=True))


if __name__ == "__main__":
    unittest.main()
