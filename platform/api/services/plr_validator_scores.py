"""Validator scores for Resequence designs, read from the validators' own output files.

A Resequence design row is made from a structure file whose B-factors carry no confidence, so its score columns stayed
empty (pLDDT 0.0, no pTM) even though every selected validator had written real scores beside it:

    validation/boltz2/<name>_model_0_boltzpred.json
    validation/protenix_v2/<name>/<hash>/<name>/seed_*/predictions/*_summary_confidence_sample_*.json
    validation/esmfold2/<name>/esmfold2_results/<name>_000.metrics.json

This module only reads those files into the existing columns with the same field mapping the other ingest paths use.
It never decides anything: a missing or unreadable file leaves the column as it was, and nothing here is an admission
condition. The first validator the job lists fills the scalar columns; every validator's scores are kept whole in
``confidence_metrics``. Protenix writes several samples per design; its own ``ranking_score`` picks the one reported.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

DEFAULT_ORDER = ("protenix_v2", "boltz2", "esmfold2")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def _percent(value: Any) -> float | None:
    number = _number(value)
    return number * 100.0 if number is not None and 0.0 <= number <= 1.0 else number


def _read(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _drop_none(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _boltz2(root: Path, name: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    folder = root / "validation" / "boltz2"
    for candidate in (folder / f"{name}_model_0_boltzpred.json", folder / f"{name}_boltzpred.json"):
        payload = _read(candidate)
        if payload is None:
            continue
        columns = _drop_none({
            "plddt_overall": _percent(payload.get("complex_plddt") if payload.get("complex_plddt") is not None else payload.get("plddt")),
            "conf_score": _number(payload.get("confidence_score")),
            "ptm": _number(payload.get("ptm")),
            "iptm": _number(payload.get("iptm")),
            "protein_iptm": _number(payload.get("protein_iptm")),
            "ligand_iptm": _number(payload.get("ligand_iptm")),
            "complex_iplddt": _number(payload.get("complex_iplddt")),
            "complex_ipde": _number(payload.get("complex_ipde")),
        })
        for key in ("chains_ptm", "pair_chains_iptm"):
            if isinstance(payload.get(key), (dict, list)):
                columns[key] = payload[key]
        return columns, {"source": candidate.relative_to(root).as_posix(), **{
            key: value for key, value in payload.items() if _number(value) is not None}}
    return None


def _protenix_v2(root: Path, name: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    folder = root / "validation" / "protenix_v2" / name
    if not folder.is_dir():
        return None
    samples = []
    for path in sorted(folder.rglob("*_summary_confidence_sample_*.json")):
        payload = _read(path)
        if payload is not None:
            samples.append((path, payload))
    if not samples:
        return None
    # The producer's own ranking picks the reported sample; ties keep file order.
    def rank(item: tuple[Path, dict[str, Any]]) -> float:
        ranking = _number(item[1].get("ranking_score"))
        return ranking if ranking is not None else float("-inf")

    path, payload = max(samples, key=rank)
    clash = payload.get("full_has_clash") if payload.get("full_has_clash") is not None else payload.get("has_clash")
    columns = _drop_none({
        "plddt_overall": _percent(payload.get("plddt")),
        "conf_score": _number(payload.get("ranking_score")),
        "ptm": _number(payload.get("ptm")),
        "iptm": _number(payload.get("iptm")),
        "complex_ipde": _number(payload.get("gpde")),
        "disorder": _number(payload.get("disorder")),
        "has_clash": bool(clash) if isinstance(clash, (bool, int, float)) else None,
    })
    recycles = payload.get("num_recycles")
    if isinstance(recycles, (int, float)) and not isinstance(recycles, bool):
        columns["num_recycles"] = int(recycles)
    return columns, {"source": path.relative_to(root).as_posix(), "samples": len(samples), **{
        key: value for key, value in payload.items() if _number(value) is not None}}


def _esmfold2(root: Path, name: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    folder = root / "validation" / "esmfold2" / name
    if not folder.is_dir():
        return None
    for path in sorted(folder.rglob("*.metrics.json")):
        payload = _read(path)
        if payload is None:
            continue
        columns = _drop_none({
            "plddt_overall": _percent(payload.get("plddt_mean")),
            "ptm": _number(payload.get("ptm")),
            "iptm": _number(payload.get("iptm")),
        })
        return columns, {"source": path.relative_to(root).as_posix(), **{
            key: value for key, value in payload.items() if _number(value) is not None}}
    return None


_READERS = {"boltz2": _boltz2, "protenix_v2": _protenix_v2, "esmfold2": _esmfold2}


def validator_scores(root: Path, name: str, order: Iterable[str] = ()) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Return (scalar columns, per-validator scores) for one design name under one job result root."""
    wanted = [item for item in dict.fromkeys([*order, *DEFAULT_ORDER]) if item in _READERS]
    per_validator: dict[str, dict[str, Any]] = {}
    columns: dict[str, Any] = {}
    for validator in wanted:
        found = _READERS[validator](root, name)
        if found is None:
            continue
        values, whole = found
        per_validator[validator] = whole
        if not columns:
            columns = dict(values)
    return columns, per_validator


def apply_validator_scores(design: Any, root: Path | str | None, name: str, validators: Iterable[str] = ()) -> bool:
    """Fill a Design's score columns and confidence_metrics from the validators' output. True when anything was found."""
    if not root:
        return False
    try:
        columns, per_validator = validator_scores(Path(root), name, validators)
    except (OSError, ValueError):
        return False
    if not per_validator:
        return False
    for field, value in columns.items():
        setattr(design, field, value)
    merged = dict(design.confidence_metrics) if isinstance(design.confidence_metrics, dict) else {}
    merged.update(per_validator)
    design.confidence_metrics = merged
    return True
