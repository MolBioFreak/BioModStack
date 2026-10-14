#!/usr/bin/env python3
"""Materialize one typed terminal structure for shared scheduler fan-out."""
from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import os
import errno
import stat
from pathlib import Path

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def stage_source(source: Path, destination: Path) -> None:
    """Retain a completed input, privately acquire mutable legacy sources."""
    source = source.resolve(strict=True)
    before = source.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("terminal source must be regular")
    if not before.st_mode & 0o222:
        try:
            os.link(source, destination)
            return
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
    shutil.copyfile(source, destination)
    destination.chmod(0o444)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--metadata-base64", required=True)
    args = parser.parse_args()
    metadata = json.loads(base64.b64decode(args.metadata_base64, validate=True))
    required = {
        "candidate_id", "parent_job_id", "parent_workflow_id", "producer_stage",
        "producer_candidate_key", "requiredness",
    }
    projected = {key: metadata.get(key) for key in required}
    candidate_id = projected["candidate_id"]
    suffix = args.source.suffix.lower()
    if (
        not isinstance(metadata, dict)
        or any(projected[key] in (None, "") for key in required)
        or _SAFE_ID.fullmatch(str(candidate_id)) is None
        or projected["requiredness"] != "required"
        or suffix not in {".pdb", ".cif", ".mmcif"}
    ):
        raise ValueError("terminal candidate authority is invalid")
    destination = Path(f"candidate_{candidate_id}")
    destination.mkdir()
    (destination / "metadata.json").write_text(
        json.dumps(projected, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    stage_source(args.source, destination / f"source{suffix}")


if __name__ == "__main__":
    main()
