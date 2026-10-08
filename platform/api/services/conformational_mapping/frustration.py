"""Authoritative residue-map join for complete FrustraMPNN landscapes."""

from __future__ import annotations

import csv
import hashlib
import io
import math
from pathlib import Path
from typing import Any, Mapping

from .contracts import AA_ORDER, canonical_sha256, validate_schema


class FrustrationLandscapeError(ValueError):
    """Raw scoring output cannot produce an unambiguous exact-slot landscape."""


_THRESHOLD_POLICY = {
    "id": "frustrampnn_class_v1",
    "high_max": -1.0,
    "minimal_min": 0.58,
}
_WT3_TO_1 = {
    "ALA": "A", "CYS": "C", "ASP": "D", "GLU": "E", "PHE": "F",
    "GLY": "G", "HIS": "H", "ILE": "I", "LYS": "K", "LEU": "L",
    "MET": "M", "ASN": "N", "PRO": "P", "GLN": "Q", "ARG": "R",
    "SER": "S", "THR": "T", "VAL": "V", "TRP": "W", "TYR": "Y",
}


def score_class(score: float) -> str:
    if score <= -1.0:
        return "high"
    if score >= 0.58:
        return "minimally_frustrated"
    return "neutral"


def _value(row: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def finalize_landscape(
    raw_csv: Path | str,
    structure_map: Mapping[str, Any],
    *,
    checkpoint_id: str,
    checkpoint_sha256: str,
    tool_id: str,
    tool_sha256: str,
    container_sha256: str,
) -> dict[str, Any]:
    raw_path = Path(raw_csv)
    raw_bytes = raw_path.read_bytes()
    map_rows = structure_map.get("rows")
    if not isinstance(map_rows, list):
        raise FrustrationLandscapeError("structure map has no residue rows")
    mapped: dict[tuple[str, int, str], Mapping[str, Any]] = {}
    indexed_mapped: dict[tuple[str, int], tuple[str, int, str]] = {}
    chain_counts: dict[str, int] = {}
    scoreable_keys: list[tuple[str, int, str]] = []
    for row in map_rows:
        chain = str(row.get("pdb_chain_id") or "")
        chain_index = chain_counts.get(chain, 0)
        chain_counts[chain] = chain_index + 1
        if row.get("status") != "mapped":
            continue
        backbone = row.get("backbone_atoms")
        if not isinstance(backbone, dict) or set(backbone) != {"N", "CA", "C", "O"}:
            continue
        key = (
            chain,
            int(row["pdb_residue_id"]),
            str(row.get("pdb_insertion_code") or ""),
        )
        if key in mapped:
            raise FrustrationLandscapeError("structure map has duplicate normalized residue identity")
        mapped[key] = row
        indexed_mapped[(chain, chain_index)] = key
        scoreable_keys.append(key)

    observed: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    input_issues: list[dict[str, Any]] = []
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FrustrationLandscapeError("raw landscape CSV is not valid UTF-8") from exc
    with io.StringIO(raw_text, newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise FrustrationLandscapeError("raw landscape CSV has no header")
        for line_number, row in enumerate(reader, start=2):
            chain = _value(row, "chain", "chain_id")
            position_text = _value(row, "position", "residue", "residue_number")
            insertion_code = _value(row, "insertion_code", "icode")
            wt = _value(row, "wildtype", "wt").upper()
            mutation = _value(row, "mutation", "mutation_aa", "mutant").upper()
            try:
                position = int(position_text)
            except (TypeError, ValueError):
                input_issues.append({
                    "line_number": line_number, "status": "malformed_row",
                    "reason": "raw residue position is not an integer",
                    "raw_identity": {"chain": chain, "position": position_text, "insertion_code": insertion_code, "mutation_aa": mutation},
                })
                continue
            if len(mutation) != 1 or mutation not in AA_ORDER:
                input_issues.append({
                    "line_number": line_number, "status": "malformed_row",
                    "reason": "raw substitution identity is not canonical",
                    "raw_identity": {"chain": chain, "position": position, "insertion_code": insertion_code, "mutation_aa": mutation},
                })
                continue
            indexed_key = (chain, position)
            residue_key = indexed_mapped.get(indexed_key)
            if insertion_code or residue_key is None:
                input_issues.append({
                    "line_number": line_number, "status": "mapping_failed",
                    "reason": "zero-based FrustraMPNN chain position has no authoritative structure-map row",
                    "raw_identity": {"chain": chain, "position": position, "insertion_code": insertion_code, "mutation_aa": mutation},
                })
                continue
            key = (*residue_key, mutation)
            if key in observed:
                observed[key] = {
                    "status": "duplicate_row", "wt": wt, "score": None,
                    "reason": f"multiple raw rows exist for this substitution; latest line {line_number}",
                }
                continue
            if len(wt) != 1 or wt not in AA_ORDER:
                observed[key] = {"status": "malformed_row", "wt": wt, "score": None, "reason": "raw WT identity is not canonical"}
                continue
            try:
                score = float(_value(row, "frustration_pred", "score", "frustration"))
            except (TypeError, ValueError):
                observed[key] = {"status": "malformed_row", "wt": wt, "score": None, "reason": "raw score is not numeric"}
                continue
            if not math.isfinite(score):
                observed[key] = {"status": "nonfinite_score", "wt": wt, "score": None, "reason": "raw score is nonfinite"}
                continue
            observed[key] = {"status": "ok", "wt": wt, "score": score, "reason": None}

    residues: list[dict[str, Any]] = []
    for residue_key in scoreable_keys:
        mapping = mapped[residue_key]
        wt = _WT3_TO_1.get(str(mapping["residue_name"]).upper())
        if wt is None:
            continue
        slots: list[dict[str, Any]] = []
        for mutation in AA_ORDER:
            raw = observed.get((*residue_key, mutation))
            if raw is None:
                slots.append(
                    {
                        "wt": wt,
                        "mutation_aa": mutation,
                        "score": None,
                        "class": None,
                        "scoreable": False,
                        "status": "missing_row",
                        "reason": "FrustraMPNN emitted no row for this mapped substitution",
                        "native": mutation == wt,
                    }
                )
                continue
            raw_wt, score = raw["wt"], raw["score"]
            if raw_wt != wt:
                raw = {"status": "mapping_failed", "score": None, "reason": "raw WT identity disagrees with authoritative mapping"}
            if raw["status"] != "ok":
                slots.append(
                    {
                        "wt": wt, "mutation_aa": mutation, "score": None, "class": None,
                        "scoreable": False, "status": raw["status"], "reason": raw["reason"],
                        "native": mutation == wt,
                    }
                )
                continue
            slots.append(
                {
                    "wt": wt,
                    "mutation_aa": mutation,
                    "score": score,
                    "class": score_class(score),
                    "scoreable": True,
                    "status": "ok",
                    "reason": None,
                    "native": mutation == wt,
                }
            )
        residues.append(
            {
                "entity_instance_id": mapping["entity_instance_id"],
                "auth_asym_id": mapping["auth_asym_id"],
                "auth_seq_id": mapping["auth_seq_id"],
                "insertion_code": mapping.get("insertion_code") or "",
                "sequence_index": mapping["sequence_index"],
                "wt": wt,
                "slots": slots,
            }
        )
    if not residues:
        raise FrustrationLandscapeError("no scoreable mapped protein residues")
    landscape = {
        "schema_name": "cm_frustration_landscape",
        "schema_version": 1,
        "target_id": structure_map["target_id"],
        "candidate_id": structure_map["candidate_id"],
        "raw_csv_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "checkpoint_id": checkpoint_id,
        "checkpoint_sha256": checkpoint_sha256,
        "tool_id": tool_id,
        "tool_sha256": tool_sha256,
        "container_sha256": container_sha256,
        "threshold_policy_id": _THRESHOLD_POLICY["id"],
        "threshold_policy_sha256": canonical_sha256(_THRESHOLD_POLICY),
        "input_issues": input_issues,
        "residues": residues,
    }
    validate_schema("cm_frustration_landscape_v1", landscape)
    return landscape
