#!/usr/bin/env python3
"""Validate a closed Shape input bundle and emit a provenance receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _verify(path: Path, expected_hash: str, expected_size: int) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{path.name} is not a regular input file")
    payload = path.read_bytes()
    if len(payload) != expected_size:
        raise ValueError(f"{path.name} size mismatch")
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected_hash:
        raise ValueError(f"{path.name} hash mismatch")
    return actual


def validate_bundle(
    *,
    request_path: Path,
    manifest_path: Path,
    vertices_path: Path,
    faces_path: Path,
    points_path: Path,
) -> dict:
    request = _load_json(request_path)
    manifest = _load_json(manifest_path)
    if request.get("schema") != "bms_shape_design_request_v1":
        raise ValueError("unsupported Shape request schema")
    request_hash = str(request.get("request_sha256") or "")
    unhashed = dict(request)
    unhashed.pop("request_sha256", None)
    if hashlib.sha256(_canonical(unhashed)).hexdigest() != request_hash:
        raise ValueError("Shape request hash mismatch")
    if request.get("geometry_sha256") != manifest.get("geometry_sha256"):
        raise ValueError("request and geometry manifest disagree")
    if request.get("point_pool_sha256") != manifest.get("point_pool_sha256"):
        raise ValueError("request and point-pool manifest disagree")

    vertex_count = int(manifest["vertex_count"])
    face_count = int(manifest["face_count"])
    point_count = int(manifest["point_count"])
    vertices_hash = _verify(vertices_path, str(manifest["vertices_sha256"]), vertex_count * 3 * 8)
    faces_hash = _verify(faces_path, str(manifest["faces_sha256"]), face_count * 3 * 4)
    points_hash = _verify(points_path, str(manifest["point_pool_sha256"]), point_count * 3 * 4)
    return {
        "schema": "bms_shape_input_receipt_v1",
        "status": "validated",
        "request_id": request["request_id"],
        "request_sha256": request_hash,
        "geometry_id": request["geometry_id"],
        "geometry_sha256": request["geometry_sha256"],
        "point_pool_sha256": points_hash,
        "vertices_sha256": vertices_hash,
        "faces_sha256": faces_hash,
        "vertex_count": vertex_count,
        "face_count": face_count,
        "point_count": point_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--vertices", type=Path, required=True)
    parser.add_argument("--faces", type=Path, required=True)
    parser.add_argument("--points", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = validate_bundle(
        request_path=args.request,
        manifest_path=args.manifest,
        vertices_path=args.vertices,
        faces_path=args.faces,
        points_path=args.points,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(_canonical(receipt) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
