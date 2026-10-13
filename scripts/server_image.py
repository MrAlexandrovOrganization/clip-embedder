"""Resolve and record the immutable server artifact for the checked Git revision."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def validate(registry, image):
    if not re.fullmatch(re.escape(registry + "-worker-cpu") + r"@sha256:[a-f0-9]{64}", image):
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
    manifest = json.loads(
        subprocess.check_output(
            [
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                f"{registry}-worker-cpu:sha-{revision}-amd64",
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
