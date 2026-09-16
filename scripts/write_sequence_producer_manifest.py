#!/usr/bin/env python3
"""Write producer-owned metadata for sequence-prediction structures."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


_SEQUENCE_FIELDS = {
    "producer_artifact_id",
    "producer_artifact_key",
    "producer_sample",
    "producer_sequence",
    "producer_fold",
    "producer_rank",
    "producer_submission_id",
    "producer_submission_name",
    "original_submission_identity",
}
_MODEL_RANK = re.compile(r"(?:^|[_-])(?:model|rank)[_-]?(\d+)(?:$|[_-])", re.IGNORECASE)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON value: {value}")


def _canonical_key(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{field} is invalid")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or parsed.as_posix() != value or any(
        part in {"", ".", ".."} for part in parsed.parts
    ):
        raise ValueError(f"{field} is noncanonical")
    return value


def _nonempty(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _stable_id(value: Any, *, field: str) -> str:
    normalized = _nonempty(value, field=field)
    if not _ID.fullmatch(normalized):
        raise ValueError(f"{field} is not a safe stable ID")
    return normalized


def _optional_coordinate(value: Any, *, field: str) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} is invalid")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{field} is invalid")
        return value
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f"{field} is invalid")


def _load_metadata(encoded: str) -> dict[str, Any]:
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8", errors="strict")
        payload = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("sequence producer metadata is not strict base64 JSON") from exc
    if not isinstance(payload, dict) or set(payload) != _SEQUENCE_FIELDS:
        raise ValueError("sequence producer metadata fields are not exact")
    payload["producer_artifact_id"] = _stable_id(
        payload["producer_artifact_id"], field="producer_artifact_id"
    )
    payload["producer_artifact_key"] = _canonical_key(
        payload["producer_artifact_key"], field="producer_artifact_key"
    )
    payload["producer_sample"] = _stable_id(
        payload["producer_sample"], field="producer_sample"
    )
    payload["producer_sequence"] = _nonempty(
        payload["producer_sequence"], field="producer_sequence"
    )
    payload["producer_submission_id"] = _stable_id(
        payload["producer_submission_id"], field="producer_submission_id"
    )
    payload["producer_submission_name"] = _nonempty(
        payload["producer_submission_name"], field="producer_submission_name"
    )
    payload["producer_fold"] = _optional_coordinate(
        payload["producer_fold"], field="producer_fold"
    )
    rank = payload["producer_rank"]
    if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int) or rank < 0):
        raise ValueError("producer_rank is invalid")
    original = payload["original_submission_identity"]
    if not isinstance(original, dict) or set(original) != {"id", "name"}:
        raise ValueError("original_submission_identity is invalid")
    if original["id"] != payload["producer_submission_id"]:
        raise ValueError("original submission ID disagrees with producer submission ID")
    if original["name"] != payload["producer_submission_name"]:
        raise ValueError("original submission name disagrees with producer submission name")
    if payload["producer_artifact_id"] != payload["producer_artifact_key"]:
        raise ValueError("sequence producer artifact ID and stable key disagree")
    if payload["producer_sample"] != payload["producer_artifact_id"]:
        raise ValueError("sequence producer sample and artifact ID disagree")
    return payload


def _rank_for(path: Path, inherited_rank: int | None) -> int | None:
    if inherited_rank is not None:
        return inherited_rank
    match = _MODEL_RANK.search(path.stem)
    return int(match.group(1)) if match else None


def _regular_file_bytes(path: Path, *, root: Path) -> bytes:
    if path.is_symlink():
        raise ValueError(f"sequence producer structure is a symlink: {path.name}")
    root_resolved = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root_resolved):
        raise ValueError("sequence producer structure escaped predictions root")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"sequence producer structure is not regular: {path.name}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("sequence producer manifest parent must be a regular directory")
    if path.exists() and path.is_symlink():
        raise ValueError("sequence producer manifest output must not be a symlink")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_manifest(
    *, metadata: dict[str, Any], predictions_dir: Path, producer_method: str,
    protein_science_contract_revision: int | None = None, boltz_native_root: Path | None = None,
) -> dict[str, Any]:
    if protein_science_contract_revision is not None or boltz_native_root is not None:
        from write_structure_producer_manifest import validate_native_options
        validate_native_options(protein_science_contract_revision, producer_method, boltz_native_root)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", producer_method):
        raise ValueError("producer_method is invalid")
    if not predictions_dir.is_dir() or predictions_dir.is_symlink():
        raise ValueError("predictions directory must be a regular non-symlink directory")
    structures = sorted(
        path
        for path in predictions_dir.rglob("*")
        if path.suffix.lower() in {".pdb", ".cif", ".mmcif"}
    )
    if not structures:
        raise ValueError("sequence predictor emitted no structures")
    candidates: list[dict[str, Any]] = []
    for structure in structures:
        physical_bytes = _regular_file_bytes(structure, root=predictions_dir)
        relative = structure.relative_to(predictions_dir).as_posix()
        output_key = _canonical_key(
            f"{metadata['producer_artifact_key']}/{relative}",
            field="producer_output_key",
        )
        source_format = "pdb" if structure.suffix.lower() == ".pdb" else "mmcif"
        artifact_digest = hashlib.sha256(physical_bytes).hexdigest()
        if output_key == artifact_digest:
            raise ValueError("producer output key must be distinct from the artifact digest")
        candidates.append(
            {
                **metadata,
                "producer_method": producer_method,
                "producer_rank": _rank_for(structure, metadata["producer_rank"]),
                "producer_output_key": output_key,
                "producer_artifact_sha256": artifact_digest,
                "source_format": source_format,
            }
        )
        if protein_science_contract_revision == 1:
            from write_structure_producer_manifest import boltz_native_identity
            candidates[-1]["protein_science_contract_revision"] = 1
            candidates[-1]["boltz_native_identity"] = boltz_native_identity(
                native_root=boltz_native_root, predictions_root=predictions_dir, structure=structure,
                source=physical_bytes, candidate_id=metadata["producer_artifact_id"], document_id=output_key)
    return {
        "schema_name": "sequence_structure_producer_candidates",
        "schema_version": 1,
        "candidates": candidates,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-base64", required=True)
    parser.add_argument("--predictions-dir", required=True, type=Path)
    parser.add_argument("--producer-method", required=True)
    parser.add_argument("--protein-science-contract-revision", type=int, choices=[1])
    parser.add_argument("--boltz-native-root", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        metadata = _load_metadata(args.metadata_base64)
        manifest = build_manifest(
            metadata=metadata,
            predictions_dir=args.predictions_dir,
            producer_method=args.producer_method,
            protein_science_contract_revision=args.protein_science_contract_revision,
            boltz_native_root=args.boltz_native_root,
        )
        _atomic_json_write(args.output, manifest)
    except (OSError, ValueError) as exc:
        print(f"sequence_producer_manifest_error:{exc}", file=__import__("sys").stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
