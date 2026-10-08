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


def _relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (not value or "\\" in value or path.is_absolute() or
            any(part in ("", ".", "..") for part in value.split("/"))):
        raise ValueError("publication path must be a contained canonical relative path")
    return path


def sequence_manifest(manifest: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """Mirror canonicalProducerOutputs without replacing sequence-owned identity."""
    required = {
        "producer_artifact_id", "producer_artifact_key", "producer_sample",
        "producer_sequence", "producer_fold", "producer_rank",
        "producer_submission_id", "producer_submission_name", "original_submission_identity",
    }
    if (not isinstance(metadata, dict) or set(metadata) != required or
            metadata["producer_artifact_id"] != metadata["producer_artifact_key"] or
            metadata["producer_sample"] != metadata["producer_artifact_id"]):
        raise ValueError("typed sequence producer metadata is invalid")
    prefix = str(_relative_path(metadata["producer_artifact_key"]))
    return {
        "schema_name": "sequence_structure_producer_candidates", "schema_version": 1,
        "candidates": [dict(metadata, producer_method=row["producer_method"],
                            producer_output_key=f"{prefix}/{row['producer_output_key']}",
                            producer_artifact_sha256=row["producer_artifact_sha256"],
                            source_format=row["source_format"])
                       for row in manifest["candidates"]],
    }


def write_publication(*, manifest_bytes: bytes, native_candidates: list[dict[str, Any]],
                      manifest: dict[str, Any], predictions_root: Path,
                      publication_dir: Path, published_structure_root: str) -> None:
    """Bind native keys to the existing flat publishDir, without rewriting identity.

    This is task-native custody used identically by local and remote publication.
    The archived manifest is byte-for-byte the original downstream document.
    """
    target_root = _relative_path(published_structure_root)
    root = predictions_root.resolve(strict=True)
    bindings = []
    destinations: set[str] = set()
    for native, candidate in zip(native_candidates, manifest["candidates"], strict=True):
        relative = _relative_path(native["producer_output_key"])
        source = root.joinpath(*relative.parts)
        if source.is_symlink() or not source.resolve(strict=True).is_relative_to(root):
            raise ValueError("publication source escaped native predictions root")
        digest = _sha256(source)
        if digest != candidate["producer_artifact_sha256"]:
            raise ValueError("publication source changed after manifest creation")
        destination = (target_root / relative.name).as_posix()
        if destination in destinations:
            raise ValueError("flat publication has duplicate native destinations")
        destinations.add(destination)
        bindings.append({"producer_output_key": candidate["producer_output_key"],
                         "published_relative_path": destination, "sha256": digest,
                         "size_bytes": source.stat().st_size,
                         "source_format": candidate["source_format"]})
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    descriptor = {
        "schema_name": "structure_producer_publication", "schema_version": 1,
        "producer_manifest": {
            "relative_path": f"run/protenix/producer/{manifest_digest}/producer_candidates.json",
            "sha256": manifest_digest,
        },
        "bindings": bindings,
    }
    destination_dir = publication_dir / manifest_digest
    destination_dir.mkdir(parents=True, exist_ok=True)
    (destination_dir / "producer_candidates.json").write_bytes(manifest_bytes)
    (destination_dir / "publication.json").write_text(
        json.dumps(descriptor, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


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
    parser.add_argument("--sequence-metadata-base64")
    parser.add_argument("--publication-dir", type=Path)
    parser.add_argument("--published-structure-root")
    args = parser.parse_args(argv)
    if bool(args.publication_dir) != bool(args.published_structure_root):
        parser.error("publication-dir and published-structure-root must be supplied together")
    if (args.publication_dir or args.sequence_metadata_base64) and args.producer_method != "protenix":
        parser.error("publication and sequence metadata options currently apply only to Protenix")
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
    native_candidates = manifest["candidates"]
    if args.sequence_metadata_base64 is not None:
        metadata = json.loads(base64.b64decode(args.sequence_metadata_base64, validate=True).decode("utf-8"))
        manifest = sequence_manifest(manifest, metadata)
    manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if args.publication_dir is not None:
        write_publication(manifest_bytes=manifest_bytes, native_candidates=native_candidates,
                          manifest=manifest, predictions_root=args.predictions_root,
                          publication_dir=args.publication_dir,
                          published_structure_root=args.published_structure_root)
    args.output.write_bytes(manifest_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
