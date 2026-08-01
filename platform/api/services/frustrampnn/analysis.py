"""Strict raw-output join, N×20 landscape, and policy-neutral summary."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import os
import stat
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from .contracts import AA_ORDER, canonical_sha256, validate_schema

THRESHOLD_POLICY = {
    "id": "frustrampnn_threshold_v1",
    "high_max": -1.0,
    "minimal_min": 0.58,
}


class LandscapeValidationError(ValueError):
    """Raw scores cannot produce a complete authoritative landscape."""


def _read_regular_no_follow(path: Path | str) -> bytes:
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise LandscapeValidationError(f"unsafe lexical raw CSV path: {raw!r}")
    absolute = raw.startswith("/")
    body = raw[1:] if absolute else raw
    parts = body.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise LandscapeValidationError(f"unsafe lexical raw CSV path component: {raw!r}")
    parent = os.open("/" if absolute else ".", os.O_RDONLY | os.O_DIRECTORY)
    fd = -1
    try:
        for part in parts[:-1]:
            next_parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
            os.close(parent); parent = next_parent
        fd = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise LandscapeValidationError("raw CSV must be regular")
        with os.fdopen(fd, "rb", closefd=True) as handle:
            fd = -1
            return handle.read()
    except OSError as exc:
        raise LandscapeValidationError(f"cannot read raw CSV without following symlink path: {exc}") from exc
    finally:
        if fd >= 0: os.close(fd)
        os.close(parent)


def score_class(score: float) -> str:
    if not math.isfinite(score):
        raise LandscapeValidationError("score must be finite")
    if score <= THRESHOLD_POLICY["high_max"]:
        return "high"
    if score >= THRESHOLD_POLICY["minimal_min"]:
        return "minimal"
    return "neutral"


def _raw_rows(raw_bytes: bytes) -> list[dict[str, str]]:
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LandscapeValidationError("raw CSV is not UTF-8") from exc
    with io.StringIO(text, newline="") as handle:
        reader = csv.DictReader(handle)
        expected = ["frustration_pred", "position", "wildtype", "mutation", "chain", "pdb"]
        if reader.fieldnames != expected:
            raise LandscapeValidationError(
                f"raw CSV header must be exact {expected}; observed={reader.fieldnames}"
            )
        rows = list(reader)
    expected_keys = set(expected)
    for line_number, row in enumerate(rows, start=2):
        if set(row) != expected_keys or None in row:
            raise LandscapeValidationError(
                f"raw CSV row {line_number} has extra/trailing fields or wrong width"
            )
    if not rows:
        raise LandscapeValidationError("raw CSV has no data rows")
    return rows


def finalize_landscape(
    raw_csv: Path | str,
    structure_map: Mapping[str, Any],
    *,
    expected_normalized_pdb_sha256: str,
    expected_model_ready_sequence_sha256: str,
) -> dict[str, Any]:
    """Validate a complete raw matrix and join positions to exact structure identity."""

    try:
        validate_schema("frustrampnn_structure_map_v1", structure_map)
    except Exception as exc:
        raise LandscapeValidationError(f"invalid structure map: {exc}") from exc
    if structure_map["normalized_pdb_sha256"] != expected_normalized_pdb_sha256:
        raise LandscapeValidationError("stale normalized PDB hash")
    if structure_map["model_ready_sequence_sha256"] != expected_model_ready_sequence_sha256:
        raise LandscapeValidationError("stale model-ready sequence hash")
    expected_sequence_hash = hashlib.sha256(
        structure_map["model_ready_sequence"].encode("ascii")
    ).hexdigest()
    if expected_sequence_hash != expected_model_ready_sequence_sha256:
        raise LandscapeValidationError("model-ready sequence content/hash mismatch")

    mapped: dict[tuple[str, int], Mapping[str, Any]] = {}
    for row in structure_map["rows"]:
        if row["status"] != "mapped":
            continue
        key = (row["pdb_chain_id"], row["model_position"])
        if key in mapped:
            raise LandscapeValidationError("duplicate model position in structure map")
        mapped[key] = row
    if not mapped:
        raise LandscapeValidationError("structure map has no scoreable positions")

    raw_bytes = _read_regular_no_follow(raw_csv)
    observed: dict[tuple[str, int, str], tuple[str, float]] = {}
    for line_number, row in enumerate(_raw_rows(raw_bytes), start=2):
        if not row["pdb"].strip():
            raise LandscapeValidationError(f"raw PDB metadata is empty on line {line_number}")
        chain = row["chain"].strip()
        try:
            position = int(row["position"])
        except ValueError as exc:
            raise LandscapeValidationError(
                f"malformed raw position on line {line_number}"
            ) from exc
        if position < 0:
            raise LandscapeValidationError("raw position must be zero-based and nonnegative")
        wt = row["wildtype"].strip().upper()
        mutation = row["mutation"].strip().upper()
        if len(wt) != 1 or wt not in AA_ORDER:
            raise LandscapeValidationError(f"malformed WT on line {line_number}")
        if len(mutation) != 1 or mutation not in AA_ORDER:
            raise LandscapeValidationError(f"malformed substitution on line {line_number}")
        mapping = mapped.get((chain, position))
        if mapping is None:
            raise LandscapeValidationError(
                f"raw position has no authoritative structure-map row: {(chain, position)}"
            )
        if wt != mapping["wt"]:
            raise LandscapeValidationError(
                f"raw WT disagreement at {(chain, position)}: {wt} != {mapping['wt']}"
            )
        try:
            score = float(row["frustration_pred"])
        except ValueError as exc:
            raise LandscapeValidationError(
                f"malformed raw score on line {line_number}"
            ) from exc
        if not math.isfinite(score):
            raise LandscapeValidationError(f"nonfinite raw score on line {line_number}")
        key = (chain, position, mutation)
        if key in observed:
            raise LandscapeValidationError(f"duplicate raw substitution row: {key}")
        observed[key] = (wt, score)

    expected_keys = {
        (chain, position, mutation)
        for chain, position in mapped
        for mutation in AA_ORDER
    }
    observed_keys = set(observed)
    if observed_keys != expected_keys:
        missing = sorted(expected_keys - observed_keys)
        extra = sorted(observed_keys - expected_keys)
        raise LandscapeValidationError(
            f"landscape incomplete: missing substitutions={missing[:10]}, extra={extra[:10]}"
        )

    residues: list[dict[str, Any]] = []
    identity_fields = (
        "entity_instance_id", "source_entity_id", "label_asym_id", "auth_asym_id",
        "label_seq_id", "auth_seq_id", "insertion_code", "sequence_index",
        "pdb_chain_id", "pdb_residue_id", "pdb_insertion_code", "model_position",
        "residue_name", "wt",
    )
    ordered_mappings = sorted(
        mapped.values(),
        key=lambda row: (row["pdb_chain_id"], row["model_position"]),
    )
    for mapping in ordered_mappings:
        chain = mapping["pdb_chain_id"]
        position = mapping["model_position"]
        slots = []
        for mutation in AA_ORDER:
            _, score = observed[(chain, position, mutation)]
            slots.append({
                "mutation_aa": mutation,
                "score": score,
                "class": score_class(score),
                "scoreable": True,
                "status": "ok",
                "reason": None,
                "native": mutation == mapping["wt"],
            })
        residues.append(
            {**{field: mapping[field] for field in identity_fields}, "slots": slots}
        )
    landscape = {
        "schema_name": "frustrampnn_landscape",
        "schema_version": 1,
        "target_id": structure_map["target_id"],
        "parent_job_id": structure_map["parent_job_id"],
        "candidate_id": structure_map["candidate_id"],
        "structure_map_sha256": canonical_sha256(dict(structure_map)),
        "normalized_pdb_sha256": structure_map["normalized_pdb_sha256"],
        "model_ready_sequence_sha256": structure_map["model_ready_sequence_sha256"],
        "raw_csv_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "threshold_policy": dict(THRESHOLD_POLICY),
        "threshold_policy_sha256": canonical_sha256(THRESHOLD_POLICY),
        "residues": residues,
    }
    try:
        validate_schema("frustrampnn_landscape_v1", landscape)
    except Exception as exc:
        raise LandscapeValidationError(f"landscape contract failed: {exc}") from exc
    return landscape


def _class_counts(slots: list[Mapping[str, Any]]) -> dict[str, int]:
    return {
        name: sum(slot["class"] == name for slot in slots)
        for name in ("high", "neutral", "minimal")
    }


def _fractions(counts: Mapping[str, int], denominator: int) -> dict[str, float]:
    return {
        name: counts[name] / denominator if denominator else 0.0
        for name in ("high", "neutral", "minimal")
    }


def summarize_landscape(
    landscape: Mapping[str, Any], structure_map: Mapping[str, Any]
) -> dict[str, Any]:
    """Derive a reconstructable summary without ranking or workflow decisions."""

    try:
        validate_schema("frustrampnn_structure_map_v1", structure_map)
        validate_schema("frustrampnn_landscape_v1", landscape)
    except Exception as exc:
        raise LandscapeValidationError(f"invalid summary input: {exc}") from exc
    if landscape["structure_map_sha256"] != canonical_sha256(dict(structure_map)):
        raise LandscapeValidationError("landscape does not bind the supplied structure map")
    for field in ("target_id", "parent_job_id", "candidate_id"):
        if landscape[field] != structure_map[field]:
            raise LandscapeValidationError(f"landscape/structure-map {field} mismatch")

    rows = structure_map["rows"]
    residues = landscape["residues"]
    all_slots = [slot for residue in residues for slot in residue["slots"]]
    scoreable = [slot for slot in all_slots if slot["scoreable"]]
    observed = [slot for slot in all_slots if slot["status"] != "missing"]
    native = [slot for slot in scoreable if slot["native"]]
    missingness = Counter(
        str(slot["reason"] or slot["status"])
        for slot in all_slots
        if not slot["scoreable"]
    )
    mapped_rows = [row for row in rows if row["status"] == "mapped"]
    ambiguous_rows = [row for row in rows if row["status"] == "ambiguous"]
    excluded_rows = [row for row in rows if row["status"] not in {"mapped", "ambiguous"}]
    represented_exclusions: set[tuple[str, str]] = set()
    for row in excluded_rows:
        reason_code = str(row["status"])
        source_identity = (
            f"{row['auth_asym_id']}:{row['auth_seq_id']}"
            f"{row['insertion_code']}:{row['residue_name']}"
        )
        represented_exclusions.add((source_identity, reason_code))
        missingness[reason_code] += 1
    for record_key in sorted({
        (str(record["source_identity"]), str(record["reason_code"]))
        for record in structure_map["excluded_records"]
    }):
        if record_key not in represented_exclusions:
            missingness[record_key[1]] += 1

    support_rows: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    map_support: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for residue in residues:
        support_rows[(residue["entity_instance_id"], residue["auth_asym_id"])].append(residue)
    for row in rows:
        map_support[(row["entity_instance_id"], row["auth_asym_id"])].append(row)
    support = []
    for key in sorted(map_support):
        entity_rows = map_support[key]
        entity_residues = support_rows.get(key, [])
        entity_slots = [slot for residue in entity_residues for slot in residue["slots"]]
        support.append({
            "entity_instance_id": key[0],
            "auth_asym_id": key[1],
            "expected_residues": len(entity_rows),
            "mapped_residues": sum(row["status"] == "mapped" for row in entity_rows),
            "scoreable_residues": len(entity_residues),
            "expected_slots": len(entity_residues) * len(AA_ORDER),
            "observed_slots": sum(slot["status"] != "missing" for slot in entity_slots),
            "scoreable_slots": sum(bool(slot["scoreable"]) for slot in entity_slots),
        })
    native_counts = _class_counts(native)
    landscape_counts = _class_counts(scoreable)
    summary = {
        "schema_name": "frustrampnn_summary",
        "schema_version": 1,
        "target_id": landscape["target_id"],
        "parent_job_id": landscape["parent_job_id"],
        "candidate_id": landscape["candidate_id"],
        "landscape_sha256": canonical_sha256(dict(landscape)),
        "residue_support": {
            "expected": len(rows),
            "mapped": len(mapped_rows),
            "scoreable": len(residues),
            "excluded": len(excluded_rows),
            "ambiguous": len(ambiguous_rows),
        },
        "slot_support": {
            "expected": len(residues) * len(AA_ORDER),
            "observed": len(observed),
            "scoreable": len(scoreable),
        },
        "missingness_by_reason": dict(sorted(missingness.items())),
        "native_slot_counts": native_counts,
        "native_slot_fractions": _fractions(native_counts, len(native)),
        "complete_landscape_counts": landscape_counts,
        "complete_landscape_fractions": _fractions(landscape_counts, len(scoreable)),
        "support_by_entity_chain": support,
        "threshold_policy": dict(landscape["threshold_policy"]),
        "threshold_policy_sha256": landscape["threshold_policy_sha256"],
    }
    try:
        validate_schema("frustrampnn_summary_v1", summary)
    except Exception as exc:
        raise LandscapeValidationError(f"summary contract failed: {exc}") from exc
    return summary


__all__ = [
    "LandscapeValidationError", "THRESHOLD_POLICY", "canonical_sha256",
    "finalize_landscape", "score_class", "summarize_landscape",
]
