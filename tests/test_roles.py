"""Synthetic role and network boundary checks; no Docker or model inference."""

import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "config", Path(__file__).resolve().parents[1] / "scripts/check_config.py"
)
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class RolesTests(unittest.TestCase):
    def test_server_has_no_worker(self):
        services = {"clip-embedder": {"secrets": ["registry"]}, "worker-cpu": {}}
        with self.assertRaisesRegex(ValueError, "must not include"):
            config.validate({"services": services}, "server", set())

    def test_tailnet_only_consumer_publication(self):
        service = {"secrets": ["registry"], "ports": [{"host_ip": "100.64.0.5", "target": 50051}]}
        config.validate({"services": {"clip-embedder": service}}, "server", set())
        for address in ["", "0.0.0.0", "192.0.2.1", "::"]:
            service["ports"][0]["host_ip"] = address
            with self.assertRaises(ValueError):
                config.validate({"services": {"clip-embedder": service}}, "server", set())

    def test_forwarder_has_only_proxy(self):
        config.validate({"services": {"clip-embedder": {"image": "proxy"}}}, "forwarder", set())
        with self.assertRaises(ValueError):
            config.validate(
                {"services": {"clip-embedder": {}, "worker-cpu": {}}}, "forwarder", set()
            )
