#!/usr/bin/env python3
"""Emit producer-owned metadata for structure files before they leave a predictor task."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

_METHOD = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
_FORMAT_SUFFIXES = {"pdb": (".pdb", ".ent"), "mmcif": (".cif", ".mmcif")}
_RANK_PATTERNS = {
    "boltz": re.compile(r"(?:^|_)model_?(\d+)(?:\.[^.]+)?\Z", re.IGNORECASE),
    "protenix": re.compile(r"(?:^|_)sample_?(\d+)(?:\.[^.]+)?\Z", re.IGNORECASE),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _format_for(path: Path, allowed: tuple[str, ...]) -> str | None:
    suffix = path.suffix.lower()
    for source_format in allowed:
        if suffix in _FORMAT_SUFFIXES[source_format]:
            return source_format
    return None


def _producer_rank(method: str, name: str) -> int | None:
    pattern = _RANK_PATTERNS.get(method)
    if pattern is None:
        return None
    match = pattern.search(name)
    return int(match.group(1)) if match is not None else None


def build_manifest(
    *,
    predictions_root: Path,
    producer_method: str,
    producer_sample: str | None,
    formats: Iterable[str],
) -> dict[str, Any]:
    """Build the exact candidate inventory owned by one predictor invocation."""

    if not _METHOD.fullmatch(producer_method):
        raise ValueError("producer_method is invalid")
    if producer_sample is not None and (not isinstance(producer_sample, str) or not producer_sample):
        raise ValueError("producer_sample must be a non-empty string or null")
    allowed = tuple(dict.fromkeys(formats))
    if not allowed or any(value not in _FORMAT_SUFFIXES for value in allowed):
        raise ValueError("formats must contain only pdb or mmcif")
    root = predictions_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("predictions_root must be a directory")

    candidates: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        source_format = _format_for(path, allowed)
        if source_format is None:
            continue
        relative = path.relative_to(root).as_posix()
        if PurePosixPath(relative).as_posix() != relative:
            raise ValueError("producer output key is noncanonical")
        candidates.append(
            {
                "producer_method": producer_method,
                "producer_sample": producer_sample,
                "producer_rank": _producer_rank(producer_method, path.name),
                "producer_output_key": relative,
                "producer_artifact_sha256": _sha256(path),
                "source_format": source_format,
            }
        )
    if not candidates:
        raise ValueError("producer emitted no structure candidates")
    return {
        "schema_name": "structure_producer_candidates",
        "schema_version": 1,
        "candidates": candidates,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions-root", required=True, type=Path)
    parser.add_argument("--producer-method", required=True)
    sample_group = parser.add_mutually_exclusive_group()
    sample_group.add_argument("--producer-sample")
    sample_group.add_argument("--producer-sample-base64")
    parser.add_argument("--format", dest="formats", action="append", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    producer_sample = args.producer_sample
    if args.producer_sample_base64 is not None:
        producer_sample = base64.b64decode(
            args.producer_sample_base64, validate=True
        ).decode("utf-8")
    manifest = build_manifest(
        predictions_root=args.predictions_root,
        producer_method=args.producer_method,
        producer_sample=producer_sample,
        formats=args.formats,
    )
    args.output.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
