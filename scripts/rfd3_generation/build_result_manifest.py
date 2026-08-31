#!/usr/bin/env python3
"""Build the hash-bound typed result manifest for general RFD3 generation."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import shlex
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from contract import ContractError, RESULT_MANIFEST_SCHEMA, canonical_sha256, validate_request


def _candidate_id(path: Path) -> str:
    if path.name.endswith(".cif.gz"):
        return path.name[:-7]
    if path.name.endswith(".json"):
        return path.name[:-5]
    return path.stem


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _descriptor(path: Path, role: str, storage_prefix: str) -> dict[str, Any]:
    media_type = "chemical/x-mmcif+gzip" if path.name.endswith(".cif.gz") else "application/json"
    return {
        "role": role,
        "relative_path": f"{storage_prefix.rstrip('/')}/{path.name}",
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "media_type": media_type,
    }


def _read_cif(path: Path) -> tuple[dict[str, str], list[tuple[list[str], list[str]]]]:
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        lines = [line.strip() for line in handle if line.strip()]
    singles: dict[str, str] = {}
    loops: list[tuple[list[str], list[str]]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line == "loop_":
            index += 1
            tags: list[str] = []
            while index < len(lines) and lines[index].startswith("_"):
                tags.append(lines[index].split()[0])
                index += 1
            tokens: list[str] = []
            while index < len(lines):
                row = lines[index]
                if row == "#":
                    index += 1
                    break
                if row == "loop_" or row.startswith("data_") or row.startswith("_"):
                    break
                tokens.extend(shlex.split(row, comments=False, posix=True))
                index += 1
            if tags:
                if len(tokens) % len(tags):
                    raise ContractError(f"malformed mmCIF loop in {path.name}")
                loops.append((tags, tokens))
            continue
        if line.startswith("_"):
            values = shlex.split(line, comments=False, posix=True)
            if len(values) >= 2:
                singles[values[0]] = values[1]
        index += 1
    return singles, loops


def _rows(tags: list[str], tokens: list[str]) -> Iterable[dict[str, str]]:
    width = len(tags)
    for start in range(0, len(tokens), width):
        yield dict(zip(tags, tokens[start : start + width]))


def _structure_metrics(path: Path) -> dict[str, Any]:
    singles, loops = _read_cif(path)
    residues: set[tuple[str, str]] = set()
    chains: set[str] = set()
    ca_coordinates: list[tuple[float, float, float]] = []
    helix_count = 0
    strand_count = 0
    helix_available = "_struct_conf.conf_type_id" in singles
    strand_available = "_struct_sheet_range.beg_label_asym_id" in singles

    if singles.get("_struct_conf.conf_type_id", "").upper().startswith("HELX"):
        helix_count += 1
    if "_struct_sheet_range.beg_label_asym_id" in singles:
        strand_count += 1

    for tags, tokens in loops:
        tag_set = set(tags)
        if any(tag.startswith("_atom_site.") for tag in tags):
            for row in _rows(tags, tokens):
                group = row.get("_atom_site.group_PDB", "ATOM").upper()
                if group != "ATOM":
                    continue
                chain = row.get("_atom_site.label_asym_id") or row.get("_atom_site.auth_asym_id")
                residue = row.get("_atom_site.label_seq_id") or row.get("_atom_site.auth_seq_id")
                atom = row.get("_atom_site.label_atom_id") or row.get("_atom_site.auth_atom_id")
                if not chain or not residue or chain in {".", "?"} or residue in {".", "?"}:
                    continue
                chains.add(chain)
                residues.add((chain, residue))
                if atom == "CA":
                    try:
                        ca_coordinates.append((
                            float(row["_atom_site.Cartn_x"]),
                            float(row["_atom_site.Cartn_y"]),
                            float(row["_atom_site.Cartn_z"]),
                        ))
                    except (KeyError, ValueError) as exc:
                        raise ContractError(f"invalid CA coordinates in {path.name}") from exc
        if "_struct_conf.conf_type_id" in tag_set:
            helix_available = True
            helix_count += sum(
                1 for row in _rows(tags, tokens)
                if row.get("_struct_conf.conf_type_id", "").upper().startswith("HELX")
            )
        if any(tag.startswith("_struct_sheet_range.") for tag in tags):
            strand_available = True
            strand_count += sum(1 for _ in _rows(tags, tokens))

    if not residues or not ca_coordinates:
        raise ContractError(f"candidate structure has no protein CA residue metrics: {path.name}")
    center = tuple(sum(point[axis] for point in ca_coordinates) / len(ca_coordinates) for axis in range(3))
    radius = math.sqrt(sum(
        sum((point[axis] - center[axis]) ** 2 for axis in range(3)) for point in ca_coordinates
    ) / len(ca_coordinates))
    return {
        "residue_count": len(residues),
        "chain_count": len(chains),
        "radius_of_gyration": round(radius, 6),
        "helix_count": helix_count if helix_available else None,
        "strand_count": strand_count if strand_available else None,
    }


def _summary(values: list[float | int]) -> dict[str, float | int]:
    return {"min": min(values), "mean": round(mean(values), 6), "max": max(values)}


def build_result_manifest(
    *,
    request_path: Path,
    structure_paths: list[Path],
    metadata_paths: list[Path],
    accepted_candidate_ids: set[str] | None,
    storage_prefix: str,
) -> dict[str, Any]:
    request = validate_request(json.loads(request_path.read_text(encoding="utf-8")))
    structures = {_candidate_id(path): path.resolve() for path in structure_paths}
    metadata = {_candidate_id(path): path.resolve() for path in metadata_paths}
    if len(structures) != len(structure_paths) or len(metadata) != len(metadata_paths):
        raise ContractError("candidate artifact identities must be unique")
    if set(structures) != set(metadata):
        raise ContractError("native RFD3 metadata must match candidate structures exactly")
    requested = request["generation"]["num_designs"]
    if len(structures) != requested:
        raise ContractError(f"candidate count mismatch: requested {requested}, generated {len(structures)}")
    for path in [*structures.values(), *metadata.values()]:
        if not path.is_file():
            raise ContractError(f"declared artifact does not exist: {path}")

    candidates = []
    for candidate_id in sorted(structures):
        metrics = _structure_metrics(structures[candidate_id])
        if accepted_candidate_ids is None:
            accepted = request["generation"]["min_length"] <= metrics["residue_count"] <= request["generation"]["max_length"]
        else:
            accepted = candidate_id in accepted_candidate_ids
        artifacts = [
            _descriptor(structures[candidate_id], "candidate_structure", storage_prefix),
            _descriptor(metadata[candidate_id], "candidate_metadata", storage_prefix),
        ]
        candidates.append({
            "candidate_id": candidate_id,
            "accepted": accepted,
            "metrics": metrics,
            "artifact_manifest_sha256": canonical_sha256(artifacts),
            "artifacts": artifacts,
        })
    if accepted_candidate_ids is not None and not accepted_candidate_ids <= set(structures):
        raise ContractError("accepted candidate IDs must reference generated candidates")

    lengths = [item["metrics"]["residue_count"] for item in candidates]
    radii = [item["metrics"]["radius_of_gyration"] for item in candidates]
    return {
        "schema": RESULT_MANIFEST_SCHEMA,
        "job_id": request["job_id"],
        "request_id": request["request_id"],
        "request_sha256": canonical_sha256(request),
        "aggregate": {
            "requested": requested,
            "generated": len(candidates),
            "accepted": sum(item["accepted"] for item in candidates),
            "length": _summary(lengths),
            "radius_of_gyration": _summary(radii),
        },
        "candidates": candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--cif-file", action="append", default=[])
    parser.add_argument("--json-file", action="append", default=[])
    parser.add_argument("--accepted-candidate-id", action="append")
    parser.add_argument("--storage-prefix", default="run/rfd3")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    manifest = build_result_manifest(
        request_path=Path(args.request),
        structure_paths=[Path(value) for value in args.cif_file],
        metadata_paths=[Path(value) for value in args.json_file],
        accepted_candidate_ids=set(args.accepted_candidate_id) if args.accepted_candidate_id is not None else None,
        storage_prefix=args.storage_prefix,
    )
    Path(args.output).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
