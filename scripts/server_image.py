"""Resolve and record the immutable server artifact for the checked Git revision."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def validate(registry, image):
    if not re.fullmatch(
        re.escape(registry + "-worker-cpu") + r"@sha256:[a-f0-9]{64}", image
    ):
        raise ValueError("Invalid server image digest")
    return image


def main():
    registry = os.environ["WORKER_REGISTRY"]
    if sys.argv[1:] == ["record"]:
        image = validate(registry, os.environ["SERVER_IMAGE"])
        path = Path(".server-release.env")
        previous = Path(".server-release.previous.env")
        if path.exists():
            previous.write_bytes(path.read_bytes())
        temp = path.with_suffix(".tmp")
        temp.write_text("SERVER_IMAGE ?= " + image + "\n")
        temp.replace(path)
        return
    revision = os.environ["SOURCE_REVISION"]
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("An exact Git revision is required")
    tag = f"{registry}-worker-cpu:coordinator-sha-{revision}-amd64"
    command = sys.argv[1:]
    if command == ["build"]:
        subprocess.run(
            [
                "docker",
                "buildx",
                "build",
                "--platform",
                "linux/amd64",
                "--file",
                "src/Dockerfile",
                "--target",
                "coordinator",
                "--load",
                "--build-arg",
                "PYTHON_VERSION=" + os.environ["PYTHON_VERSION"],
                "--build-arg",
                "UV_VERSION=" + os.environ["UV_VERSION"],
                "--label",
                "org.opencontainers.image.revision=" + revision,
                "--label",
                "io.compute.role=coordinator",
                "--tag",
                tag,
                ".",
            ],
            check=True,
        )
        return
    if command == ["smoke"]:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--platform",
                "linux/amd64",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "-e",
                "PYTHONDONTWRITEBYTECODE=1",
                tag,
                "python",
                "-c",
                "import importlib.util; import coordinator; "
                "assert all(importlib.util.find_spec(x) is None "
                "for x in ('torch', 'open_clip', 'numpy', 'PIL')); "
                "print('Coordinator imports verified; inference dependencies are absent')",
            ],
            check=True,
        )
        return
    if command == ["publish"]:
        subprocess.run(["docker", "push", tag], check=True)
        return
    if command:
        raise ValueError("Unknown server image command")
    manifest = json.loads(
        subprocess.check_output(
            [
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                tag,
                "--format",
                "{{json .Manifest}}",
            ],
            text=True,
        )
    )
    image = validate(registry, registry + "-worker-cpu@" + manifest["digest"])
    print("server_image=" + image)


if __name__ == "__main__":
    main()
