#!/usr/bin/env python3
"""Publish verified shared SIFs and an explicit lane reference file.

Input JSON maps supported environment keys to {"source": ..., "sha256": ...}.
Publication never deletes source images or restarts services. New references
become effective when the managed lane is restarted. Each invocation supplies
that lane's complete reference set, not an incremental patch.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from lib.shared_runtime_images import publish_image

ALLOWED_KEYS = frozenset({
    "BMS_NGS_RUNTIME_SIF", "BMS_CM_CONFORNETS_CONTAINER_PATH",
    "BMS_PROTENIX_CONTAINER_PATH",
})


def publish_references(store_root: Path, lane: str, images: dict) -> Path:
    if lane not in {"development", "production"}:
        raise ValueError("unknown runtime lane")
    if not isinstance(images, dict) or not images or not set(images) <= ALLOWED_KEYS:
        raise ValueError("references require supported runtime environment keys")
    store_root = Path(os.path.abspath(store_root))
    # No shell interpolation or ambiguous systemd EnvironmentFile tokens.
    if any(c in str(store_root) for c in '\n\r\x00"\\$`') or any(c.isspace() for c in str(store_root)):
        raise ValueError("runtime store path must not contain whitespace or quoting characters")
    for value in images.values():
        if not isinstance(value, dict) or set(value) != {"source", "sha256"}:
            raise ValueError("each image requires exactly source and sha256")
        if not all(isinstance(value[k], str) and value[k] for k in value):
            raise ValueError("image source and sha256 must be nonempty strings")
    lines = ["# Managed shared runtime references; image digest is encoded in each path.",
             f"BMS_RUNTIME_IMAGE_STORE={store_root}"]
    for key, value in sorted(images.items()):
        image = publish_image(Path(value["source"]), store_root, value["sha256"])
        lines.append(f"{key}={image}")
    directory = store_root / "references"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("runtime references directory must not be a symlink")
    target = directory / f"{lane}.env"
    fd, temporary = tempfile.mkstemp(prefix=f".{lane}-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write("\n".join(lines) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--lane", choices=("development", "production"), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    print(publish_references(args.store_root, args.lane, json.loads(args.manifest.read_text())))


if __name__ == "__main__":
    main()
