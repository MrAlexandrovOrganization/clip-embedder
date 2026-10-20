"""Validate resolved Compose configuration without printing secret-bearing values."""

import ipaddress
import json
import os
import subprocess
import sys


def validate(config, role, profiles):
    services = config["services"]
    if role not in ("server", "workers", "forwarder"):
        raise ValueError("STACK_ROLE must be server, workers or forwarder")
    tokens, identities = set(), set()
    if role == "workers":
        if "stash" in services or "clip-embedder" in services:
            raise ValueError("worker stack must not include a coordinator")
        if not profiles <= {"cpu", "gpu"}:
            raise ValueError("unsupported worker profile")
        enabled = [
            name
            for name, spec in services.items()
            if name.startswith("worker-")
            and name != "worker-updater"
            and (not spec.get("profiles") or profiles.intersection(spec["profiles"]))
        ]
        if not enabled:
            raise ValueError("enable at least one worker")
        for name in enabled:
            spec = services[name]
            env = spec["environment"]
            endpoint = env.get("WORKER_ENDPOINT", "")
            if env.get("WORKER_TRANSPORT") != "tls" or ":" not in endpoint or "://" in endpoint:
                raise ValueError("remote workers require a gRPC TLS host:port endpoint")
            identity = env.get("WORKER_ID", "")
            if not identity or identity in identities:
                raise ValueError("enabled workers need distinct identities")
            identities.add(identity)
            secret = next(
                (
                    s
                    for s in spec.get("secrets", [])
                    if s.get("target") == "worker-token" or s.get("source") == "worker-token"
                ),
                None,
            )
            if not secret:
                raise ValueError("each worker requires a token secret mount")
            source = config["secrets"][secret["source"]].get("file", "")
            if not source or source.startswith("/missing/") or source in tokens:
                raise ValueError("enabled workers need distinct token files")
            tokens.add(source)
            if name == "worker-gpu" and not spec.get("deploy", {}).get("resources", {}).get(
                "reservations", {}
            ).get("devices"):
                raise ValueError("GPU worker needs a GPU device reservation")
    elif role == "server":
        coordinator = "stash" if "stash" in services else "clip-embedder"
        if any("worker" in name for name in services):
            raise ValueError("coordinator stack must not include inference workers")
        if not services[coordinator].get("secrets"):
            raise ValueError("coordinator requires scoped credentials")
    else:
        if set(services) != {"clip-embedder"} or services["clip-embedder"].get("build"):
            raise ValueError("forwarder must contain only the proxy image")
    for spec in services.values():
        for port in spec.get("ports", []):
            address = port.get("host_ip", "")
            if (
                role != "server"
                or not address
                or ipaddress.ip_address(address) not in ipaddress.ip_network("100.64.0.0/10")
            ):
                raise ValueError("published endpoints must bind to the server's tailnet address")


def main():
    try:
        command = subprocess.run(
            [*sys.argv[1:], "config", "--format", "json"],
            text=True,
            capture_output=True,
            check=True,
        )
        validate(
            json.loads(command.stdout),
            os.getenv("STACK_ROLE", "server"),
            set(filter(None, os.getenv("COMPOSE_PROFILES", "").split(","))),
        )
    except subprocess.CalledProcessError:
        raise SystemExit("Compose validation failed; check required configuration") from None
    except (ValueError, KeyError, TypeError) as exc:
        raise SystemExit(str(exc)) from None
    print("Compose role, transport, identities and mounts validated")


if __name__ == "__main__":
    main()
