#!/usr/bin/env python3
"""Read-only image selection for the workflow adapter's jsonschema probe.

Use the existing retained release authority and strict CAS verifier. This is
only a provisioning probe: scientific runtime attestations remain unchanged.
Unmigrated installations keep their conventional image until a normal rebuild.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

from lib.runtime_image_lifecycle import load_state, object_path
from lib.shared_runtime_images import SharedRuntimeImageError, verify_image


def select_probe_image(*, containers: Path | None = None) -> Path:
    selector = "BMS_PROTENIX_CONTAINER_PATH"
    containers = containers if containers is not None else Path(os.environ.get("BMS_CONTAINER_DIR") or
                      str(Path(os.environ.get("BMS_DATA") or "/mnt/BioModStack") / "apptainer"))
    root = Path(os.environ.get("BMS_RUNTIME_IMAGE_STORE", "").strip() or containers / ".image-store")
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("runtime image store must be an absolute path without traversal")
    state = load_state(root)
    configured = os.environ.get(selector, "").strip()
    if configured:
        candidates = [release["images"][selector]
                      for release in state["releases"].values()
                      if selector in release["images"]
                      and release["images"][selector]["path"] == configured]
        if not candidates:
            raise ValueError("configured Protenix image is not a retained managed reference")
        image = candidates[0]
    else:
        lane = os.environ.get("BMS_RUNTIME_IMAGE_LANE") or os.environ["BMS_WORKFLOW_ADAPTER_LANE"]
        if lane not in {"development", "production"}:
            raise ValueError("invalid runtime image lane")
        release = state["releases"].get(state["current"].get(lane), {})
        image = release.get("images", {}).get(selector)
        if image is None:
            # No selected release for this model/lane: preserve the existing
            # production provisioning probe, not a scientific digest override.
            return containers / "protenix.sif"
    path = object_path(root, image["sha256"])
    if str(path) != image["path"]:
        raise ValueError("managed image path differs from shared store")
    verify_image(path, image["sha256"])
    return path


if __name__ == "__main__":
    try:
        print(select_probe_image())
    except (OSError, SharedRuntimeImageError, ValueError, KeyError, TypeError) as exc:
        print(f"workflow adapter Protenix probe image is unavailable: {exc}", file=sys.stderr)
        raise SystemExit(78)
