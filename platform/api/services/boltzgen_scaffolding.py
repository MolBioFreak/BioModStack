from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

from services.cdr_annotator import annotate_pdb, extract_sequence_from_pdb, identify_binder_chains
from services.sabdab_client import CACHE_DIR, convert_sabdab_to_hlt, download_pdb
from services.sabdab_db import get_sabdab_db


logger = logging.getLogger(__name__)

DEFAULT_NANOBODY_ENSEMBLE = (
    "3DWT",  # Cablys-3
    "5U64",  # VHH-28 / VH3-like
    "7EOW",  # Caplacizumab-like scaffold example used upstream
    "8Z8M",  # Ozoralizumab
)


def _coerce_nonempty_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_chain(value: Any) -> Optional[str]:
    text = _coerce_nonempty_text(value)
    if not text:
        return None
    for delimiter in (",", ";", "|", " "):
        if delimiter in text:
            token = text.split(delimiter)[0].strip()
            return token[:1] if token else None
    return text[:1]


def _parse_loop_length_spec(raw: Any, fallback_length: int) -> str:
    text = _coerce_nonempty_text(raw)
    if not text:
        return f"{fallback_length}"
    normalized = text.replace("-", "..")
    if ".." in normalized:
        left, right = normalized.split("..", 1)
        try:
            minimum = max(1, int(left))
            maximum = max(minimum, int(right))
            return f"{minimum}..{maximum}"
        except (TypeError, ValueError):
            return f"{fallback_length}"
    try:
        value = max(1, int(normalized))
        return f"{value}"
    except (TypeError, ValueError):
        return f"{fallback_length}"


def _loop_specs_from_annotation(annotation: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
    loop_entries: list[dict[str, Any]] = []
    loop_mapping = (
        ("H1", getattr(annotation, "cdr_h1_seq_range", None), params.get("boltzgen_cdr_h1_length")),
        ("H2", getattr(annotation, "cdr_h2_seq_range", None), params.get("boltzgen_cdr_h2_length")),
        ("H3", getattr(annotation, "cdr_h3_seq_range", None), params.get("boltzgen_cdr_h3_length")),
    )
    for loop_id, seq_range, requested_length in loop_mapping:
        if not seq_range or len(seq_range) != 2:
            continue
        start = int(seq_range[0]) + 1
        end = int(seq_range[1]) + 1
        original_length = max(1, end - start + 1)
        loop_entries.append(
            {
                "loop_id": loop_id,
                "start": start,
                "end": end,
                "length": _parse_loop_length_spec(requested_length, original_length),
            }
        )
    return loop_entries


def _build_scaffold_spec(path: Path, chain_id: str, loop_entries: list[dict[str, Any]], name: str) -> dict[str, Any]:
    design_ranges = ",".join(f"{entry['start']}..{entry['end']}" for entry in loop_entries)
    return {
        "name": name,
        "path": str(path),
        "chain_id": chain_id,
        "design_ranges": design_ranges,
        "spec": {
            "path": str(path),
            "include": [{"chain": {"id": chain_id}}],
            "design": [{"chain": {"id": chain_id, "res_index": design_ranges}}],
            "structure_groups": [
                {"group": {"id": chain_id, "visibility": 2}},
                {"group": {"id": chain_id, "visibility": 0, "res_index": design_ranges}},
            ],
            "exclude": [
                {"chain": {"id": chain_id, "res_index": f"{entry['start']}..{entry['end']}"}} for entry in loop_entries
            ],
            "design_insertions": [
                {"insertion": {"id": chain_id, "res_index": entry["start"], "num_residues": entry["length"]}}
                for entry in loop_entries
            ],
            "reset_res_index": [{"chain": {"id": chain_id}}],
        },
    }


def _resolve_binder_chain_id(path: Path) -> Optional[str]:
    try:
        sequences = extract_sequence_from_pdb(str(path))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to read framework sequences from %s: %s", path, exc)
        return None

    if not sequences:
        return None

    chains = identify_binder_chains(sequences, str(path))
    return chains.get("H") or chains.get("L") or next(iter(sequences.keys()), None)


def _scaffold_spec_from_local_path(path: Path, params: dict[str, Any], *, display_name: str) -> Optional[dict[str, Any]]:
    if not path.exists():
        logger.warning("Skipping BoltzGen scaffold %s because it does not exist", path)
        return None

    chain_id = _resolve_binder_chain_id(path)
    preferred = {"H": chain_id} if chain_id else None
    annotation = annotate_pdb(str(path), preferred_chains=preferred)
    if not annotation:
        logger.warning("Failed to annotate BoltzGen scaffold %s", path)
        return None

    loop_entries = _loop_specs_from_annotation(annotation, params)
    if not loop_entries:
        logger.warning("Annotated scaffold %s has no heavy-chain CDR ranges", path)
        return None

    resolved_chain_id = chain_id or "H"
    return _build_scaffold_spec(path, resolved_chain_id, loop_entries, display_name)


async def _ensure_sabdab_scaffold(
    pdb_code: str,
    params: dict[str, Any],
    *,
    scheme: str = "imgt",
) -> Optional[dict[str, Any]]:
    pdb_code = str(pdb_code).strip().upper()
    if not pdb_code:
        return None

    cache_path = CACHE_DIR / f"{pdb_code.lower()}_{scheme}_hlt.pdb"
    if not cache_path.exists():
        pdb_content = await download_pdb(pdb_code, scheme=scheme, cache=True)
        if not pdb_content:
            logger.warning("Failed to download SAbDab framework %s for BoltzGen", pdb_code)
            return None

        heavy_chain = light_chain = antigen_chain = None
        try:
            db = get_sabdab_db()
            entries = db.get_by_pdb(pdb_code)
            if entries:
                entry = entries[0]
                heavy_chain = entry.h_chain
                light_chain = entry.l_chain
                antigen_chain = entry.antigen_chain
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to load SAbDab metadata for %s: %s", pdb_code, exc)

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            convert_sabdab_to_hlt(
                pdb_content,
                heavy_chain=heavy_chain,
                light_chain=light_chain,
                antigen_chain=antigen_chain,
            ),
            encoding="utf-8",
        )

    return _scaffold_spec_from_local_path(cache_path, params, display_name=pdb_code)


async def resolve_nanobody_scaffold_specs(params: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(params, dict):
        return [], []

    if params.get("boltzgen_nanobody_scaffold_specs"):
        try:
            existing = params["boltzgen_nanobody_scaffold_specs"]
            if isinstance(existing, str):
                return json.loads(existing), []
            if isinstance(existing, list):
                return deepcopy(existing), []
        except Exception as exc:
            logger.warning("Failed to parse existing BoltzGen scaffold specs: %s", exc)

    if not params.get("boltzgen_use_framework_template", False):
        return [], []

    source = _coerce_nonempty_text(params.get("boltzgen_scaffold_source")) or "sequence_template"
    notes: list[str] = []
    specs: list[dict[str, Any]] = []

    if source == "sequence_template":
        return [], notes

    if source == "default_ensemble":
        for pdb_code in DEFAULT_NANOBODY_ENSEMBLE:
            spec = await _ensure_sabdab_scaffold(pdb_code, params)
            if spec:
                specs.append(spec)
        if specs:
            notes.append(f"BoltzGen scaffold ensemble resolved to {len(specs)} curated nanobody scaffolds")
        else:
            notes.append("BoltzGen default scaffold ensemble could not be resolved; falling back to sequence template mode")
        return specs, notes

    if source == "selected_scaffold":
        sabdab_framework = params.get("sabdab_framework") if isinstance(params.get("sabdab_framework"), dict) else None
        framework_path = _coerce_nonempty_text(params.get("custom_framework_path"))
        if sabdab_framework:
            framework_path = _coerce_nonempty_text(sabdab_framework.get("filePath")) or framework_path

        if framework_path:
            spec = _scaffold_spec_from_local_path(Path(framework_path).expanduser().resolve(), params, display_name=Path(framework_path).stem)
            if spec:
                notes.append(f"BoltzGen scaffold resolved from local framework {Path(framework_path).name}")
                return [spec], notes

        pdb_code = _coerce_nonempty_text((sabdab_framework or {}).get("pdbCode"))
        if pdb_code:
            spec = await _ensure_sabdab_scaffold(pdb_code, params)
            if spec:
                notes.append(f"BoltzGen scaffold resolved from SAbDab framework {pdb_code}")
                return [spec], notes

        notes.append("BoltzGen selected-scaffold mode requested without a resolvable scaffold; falling back to sequence template mode")
        return [], notes

    notes.append(f"Unknown BoltzGen scaffold source '{source}'; falling back to sequence template mode")
    return [], notes


async def prepare_boltzgen_params_for_launch(params: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    normalized = dict(params or {})
    notes: list[str] = []

    uses_boltzgen = (
        str(normalized.get("diffusion_method", "")).strip().lower() == "boltzgen"
        or str(normalized.get("boltzgen_mode", "")).strip().lower() == "nanobody_binder"
    )
    if not uses_boltzgen:
        return normalized, notes

    scaffold_specs, scaffold_notes = await resolve_nanobody_scaffold_specs(normalized)
    notes.extend(scaffold_notes)
    if scaffold_specs:
        normalized["boltzgen_nanobody_scaffold_specs"] = json.dumps(scaffold_specs)
        normalized["boltzgen_scaffold_source_resolved"] = _coerce_nonempty_text(normalized.get("boltzgen_scaffold_source")) or "selected_scaffold"

    return normalized, notes
