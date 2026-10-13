"""Validate resolved Compose configuration without printing secret-bearing values."""

import json
import os
import subprocess
import sys


def validate(config, role, profiles):
    services = config["services"]
    if role not in ("server", "workers"):
        raise ValueError("STACK_ROLE must be server or workers")
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
    else:
        coordinator = "stash" if "stash" in services else "clip-embedder"
        expected = "stash:50058" if coordinator == "stash" else "clip-embedder:50057"
        fallback = services["stash-worker" if coordinator == "stash" else "worker-cpu"]
        env = fallback["environment"]
        if env.get("WORKER_TRANSPORT") != "internal" or env.get("WORKER_ENDPOINT") != expected:
            raise ValueError("plaintext fallback must use its private Compose endpoint")
        if not services[coordinator].get("secrets"):
            raise ValueError("coordinator requires scoped credentials")
    for spec in services.values():
        if spec.get("ports"):
            raise ValueError("these stacks use private networks; published ports are unsupported")


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
