"""Publish immutable worker images and resolve the single approved release marker."""

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

LABEL = "io.compute.release."


def run(*args, capture=False):
    return subprocess.run(args, check=True, text=True, capture_output=capture).stdout


def release(labels, registry):
    revision = labels.get(LABEL + "revision", "")
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Invalid release revision")
    images = {}
    for role in ("cpu", "gpu"):
        image = labels.get(LABEL + role, "")
        if not re.fullmatch(
            re.escape(registry + "-worker-" + role) + r"@sha256:[a-f0-9]{64}", image
        ):
            raise ValueError("Invalid release image digest")
        images[role] = image
    return revision, images


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
        temp = Path(file.name)
        try:
            json.dump(value, file)
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    temp.replace(path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def resolve(registry):
    marker = registry + "-release:stable"
    run("docker", "pull", "--platform", "linux/amd64", marker)
    labels = json.loads(
        run(
            "docker",
            "image",
            "inspect",
            marker,
            "--format",
            "{{json .Config.Labels}}",
            capture=True,
        )
    )
    revision, images = release(labels, registry)
    target = Path(".worker-release.env")
    temp = target.with_suffix(".tmp")
    temp.write_text(
        "WORKER_RELEASE_REVISION="
        + revision
        + "\n"
        + "".join(
            "WORKER_" + role.upper() + "_IMAGE=" + image + "\n" for role, image in images.items()
        )
    )
    temp.replace(target)
    print("Approved worker release:", revision)


def publish(registry, revision, arch):
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Publish requires an exact Git SHA")
    for role in ("cpu", "gpu") if arch == "amd64" else ("cpu",):
        run(
            "docker",
            "buildx",
            "build",
            "--platform",
            "linux/" + arch,
            "--file",
            "src/Dockerfile",
            "--target",
            "remote-" + role,
            "--build-arg",
            "PYTHON_VERSION=" + os.environ["PYTHON_VERSION"],
            "--build-arg",
            "UV_VERSION=" + os.environ["UV_VERSION"],
            "--build-arg",
            "TORCH_EXTRA=" + ("cuda" if role == "gpu" else "cpu"),
            "--tag",
            f"{registry}-worker-{role}:sha-{revision}-{arch}",
            "--label",
            "org.opencontainers.image.revision=" + revision,
            "--cache-from",
            f"type=registry,ref={registry}-worker-{role}:buildcache-{arch}",
            "--cache-to",
            f"type=registry,ref={registry}-worker-{role}:buildcache-{arch},mode=max",
            "--push",
            ".",
        )


def promote(registry, revision):
    images = {}
    for role in ("cpu", "gpu"):
        tag = f"{registry}-worker-{role}:sha-{revision}"
        sources = [tag + "-amd64"] + ([tag + "-arm64"] if role == "cpu" else [])
        run("docker", "buildx", "imagetools", "create", "--tag", tag, *sources)
        manifest = json.loads(
            run(
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                tag,
                "--format",
                "{{json .Manifest}}",
                capture=True,
            )
        )
        images[role] = f"{registry}-worker-{role}@{manifest['digest']}"
    labels = {
        LABEL + "revision": revision,
        **{LABEL + role: image for role, image in images.items()},
    }
    release(labels, registry)
    with tempfile.TemporaryDirectory() as temp:
        dockerfile = Path(temp) / "Dockerfile"
        dockerfile.write_text(
            "FROM scratch\nLABEL "
            + " \\\n  ".join(key + "=" + json.dumps(value) for key, value in labels.items())
            + "\n"
        )
        run(
            "docker",
            "buildx",
            "build",
            "--platform",
            "linux/amd64",
            "--provenance=false",
            "--tag",
            registry + "-release:stable",
            "--push",
            temp,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("resolve", "publish", "promote"))
    parser.add_argument("--arch", choices=("amd64", "arm64"))
    args = parser.parse_args()
    registry = os.environ["WORKER_REGISTRY"]
    revision = os.getenv("SOURCE_REVISION", "")
    if args.command == "resolve":
        resolve(registry)
    elif args.command == "publish":
        publish(registry, revision, args.arch)
    else:
        promote(registry, revision)


if __name__ == "__main__":
    main()
