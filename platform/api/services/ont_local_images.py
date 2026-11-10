"""Find the ONT signal tool images that exist on this machine.

An image built locally gets its own id, so nothing here compares an id with a
value recorded in the repository. The id that is found is what jobs run and what
their records keep.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

from services.container_cli_environment import container_cli_environment

DEFAULT_TAGS = {
    "BMS_ONT_SLOW5TOOLS_IMAGE": "biomodstack/ont-raw-signal:blue-crab-0.5.0-slow5tools-1.4.0",
    "BMS_ONT_SQUIGUALISER_IMAGE": "biomodstack/ont-squigualiser:0.7.0",
    "BMS_ONT_SQUIGUALISER_COMPARISON_IMAGE": "biomodstack/ont-squigualiser-comparison:0.7.0",
    "BMS_ONT_SQUIGULATOR_IMAGE": "biomodstack/ont-squigulator:0.5.0",
}


def inspect_image_id(runtime: str, reference: str) -> str | None:
    """Return the immutable id of a local image, or None when it is absent."""
    try:
        result = subprocess.run(
            [runtime, "image", "inspect", "--format", "{{.Id}}", reference],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=30,
            env=container_cli_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    observed = result.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{64}", observed):
        observed = f"sha256:{observed}"
    if result.returncode != 0 or re.fullmatch(r"sha256:[0-9a-f]{64}", observed) is None:
        return None
    return observed


def local_image_identity(env_name: str, runtime: str) -> tuple[str, str]:
    """Resolve one ONT tool image to (``sha256:<id>``, ``<id>``).

    The image named by the environment variable is used when it is present.
    Otherwise the image under the tool's build tag is used (the tag the Tools
    screen and the build scripts produce; ``<env_name>_TAG`` overrides it).
    """
    if runtime not in {"docker", "podman"} or shutil.which(runtime) is None:
        raise RuntimeError("container runtime is unavailable")
    configured = os.environ.get(env_name, "").strip()
    tag = os.environ.get(env_name + "_TAG", "").strip() or DEFAULT_TAGS[env_name]
    for reference in (configured, tag):
        if not reference:
            continue
        image = inspect_image_id(runtime, reference)
        if image is not None:
            return image, image.removeprefix("sha256:")
    raise RuntimeError(f"no local image for {env_name}: build {tag} from the Tools screen")
