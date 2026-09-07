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


# Identity rules are pinned to apptainer/boltz2.def, not an installed-version guess.
_BOLTZ_REVISION = "7ebf1be087d4d61a02234c878402838bf3712d8b"


def validate_native_options(revision: int | None, method: str, native_root: Path | None) -> None:
    if revision is not None and (type(revision) is not int or revision != 1):
        raise ValueError("unsupported protein science contract revision")
    if revision == 1 and (method != "boltz" or native_root is None):
        raise ValueError("marked native publication requires Boltz and native results root")
    if revision is None and native_root is not None:
        raise ValueError("native results root requires explicit revision 1")


def boltz_native_identity(*, native_root: Path, predictions_root: Path,
                          structure: Path, source: bytes, candidate_id: str,
                          document_id: str) -> dict[str, Any]:
    """Capture task-owned snapshots, derive native proof, then transport bytes."""
    from write_sequence_producer_manifest import _regular_file_bytes
    from lib.boltz_native_identity import derive_boltz_native_identity

    match = re.fullmatch(r"(.+)_model_(\d+)\.pdb", structure.name)
    if match is None:
        raise ValueError("non-native Boltz structure filename")
    record_id = match.group(1)
    native_root = native_root.resolve(strict=True)
    native_dir = native_root / "predictions" / record_id
    original = _regular_file_bytes(native_dir / structure.name, root=native_root)
    if source != original:
        raise ValueError("transported structure differs from native written structure")
    ledger_bytes = _regular_file_bytes(native_root / "processed" / "structures" / f"{record_id}.npz", root=native_root)
    names = {"pae": f"pae_{structure.stem}.npz", "plddt": f"plddt_{structure.stem}.npz",
             "confidence": f"confidence_{structure.stem}.json"}
    snapshots = {key: _regular_file_bytes(native_dir / name, root=native_root) for key, name in names.items()}
    evidence = derive_boltz_native_identity(
        source=source, structure_name=structure.name, ledger_bytes=ledger_bytes,
        pae_bytes=snapshots["pae"], plddt_bytes=snapshots["plddt"], confidence_bytes=snapshots["confidence"],
        candidate_id=candidate_id, document_id=document_id)
    transported = [(name, snapshots[key]) for key, name in names.items()]
    transported.append((evidence["processed_structure"]["artifact_key"], ledger_bytes))
    # Validate every destination before transporting any native bytes.
    for name, data in transported:
        target = predictions_root / name
        if target.exists() and _regular_file_bytes(target, root=predictions_root) != data:
            raise ValueError("foreign existing native artifact at transport destination")
        if target.is_symlink():
            raise ValueError("symlink native artifact destination")
    for name, data in transported:
        target = predictions_root / name
        if not target.exists():
            with target.open("xb") as handle:
                handle.write(data)
    return evidence


def build_manifest(
    *,
    predictions_root: Path,
    producer_method: str,
    producer_sample: str | None,
    formats: Iterable[str],
    protein_science_contract_revision: int | None = None,
    boltz_native_root: Path | None = None,
) -> dict[str, Any]:
    """Build the exact candidate inventory owned by one predictor invocation."""

    validate_native_options(protein_science_contract_revision, producer_method, boltz_native_root)
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
        source = None
        if protein_science_contract_revision == 1:
            from write_sequence_producer_manifest import _regular_file_bytes
            source = _regular_file_bytes(path, root=root)
        candidates.append(
            {
                "producer_method": producer_method,
                "producer_sample": producer_sample,
                "producer_rank": _producer_rank(producer_method, path.name),
                "producer_output_key": relative,
                "producer_artifact_sha256": hashlib.sha256(source).hexdigest() if source is not None else _sha256(path),
                "source_format": source_format,
            }
        )
        if protein_science_contract_revision == 1:
            candidates[-1]["protein_science_contract_revision"] = 1
            candidates[-1]["boltz_native_identity"] = boltz_native_identity(
                native_root=boltz_native_root, predictions_root=root, structure=path,
                source=source, candidate_id=relative, document_id=relative)
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
    parser.add_argument("--protein-science-contract-revision", type=int, choices=[1])
    parser.add_argument("--boltz-native-root", type=Path)
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
        protein_science_contract_revision=args.protein_science_contract_revision,
        boltz_native_root=args.boltz_native_root,
    )
    args.output.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
