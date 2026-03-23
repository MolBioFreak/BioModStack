"""
Result Ingester Service - Parse pipeline outputs into database.

Reads all_designs.csv and success_metrics.json from completed jobs
and populates the Design table in SQLite.
"""

import csv
import copy
import json
import re
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from database import Design, Job
from paths import get_data_root
from services.rfantibody_metadata import load_rfantibody_trb_summary
from .aligned_error_utils import detect_aligned_error_artifact, load_aligned_error_artifact
from .ipsae import compute_ipsae_interface
from .structure_utils import calculate_epitope_contacts, compute_contact_geometry_metrics


def _is_native_frustration_row(row: Dict[str, Any]) -> bool:
    wildtype = row.get("wildtype")
    mutation = row.get("mutation")
    if wildtype is None or mutation is None:
        return True
    return str(wildtype).strip() == str(mutation).strip()


def _summarize_frustration_rows(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rows:
        return None

    pos_values: Dict[tuple[int, str], List[float]] = {}
    for row in rows:
        if not _is_native_frustration_row(row):
            continue
        try:
            position = int(row["position"])
            chain = str(row["chain"])
            value = float(row["frustration_pred"])
        except (KeyError, TypeError, ValueError):
            continue
        key = (position, chain)
        pos_values.setdefault(key, []).append(value)

    if not pos_values:
        return None

    residues = []
    high_count = 0
    min_count = 0
    for (pos, chain), values in sorted(pos_values.items(), key=lambda item: (item[0][1], item[0][0])):
        frust = sum(values) / len(values)
        if frust <= -1.0:
            frust_class = "high"
            high_count += 1
        elif frust >= 0.58:
            frust_class = "min"
            min_count += 1
        else:
            frust_class = "neutral"
        residues.append({
            "pos": pos,
            "chain": chain,
            "frust": round(float(frust), 3),
            "frustClass": frust_class,
        })

    total = len(pos_values)
    pct_high = round(high_count / total * 100, 1) if total > 0 else 0.0
    return {
        "high_count": high_count,
        "min_count": min_count,
        "pct_high": pct_high,
        "residues": residues,
    }


def _normalize_frustration_target_name(value: str) -> str:
    raw = str(value).strip()
    return Path(raw).stem if raw else raw


def extract_frustration_targets(csv_path: Path) -> List[str]:
    """
    Return distinct design/PDB identifiers embedded in a frustration CSV.

    FrustraMPNN can emit one CSV per structure or a batch CSV with a `pdb` column.
    """
    try:
        import pandas as pd

        df = pd.read_csv(csv_path, usecols=lambda c: c in {"pdb"})
        if "pdb" not in df.columns:
            return []
        seen: List[str] = []
        for value in df["pdb"].dropna().astype(str).tolist():
            normalized = _normalize_frustration_target_name(value)
            if normalized and normalized not in seen:
                seen.append(normalized)
        return seen
    except ImportError:
        pass
    except Exception as e:
        print(f"[Ingester] Error extracting frustration targets from {csv_path}: {e}")
        return []

    try:
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            if "pdb" not in (reader.fieldnames or []):
                return []
            seen: List[str] = []
            for row in reader:
                value = row.get("pdb")
                if not value:
                    continue
                normalized = _normalize_frustration_target_name(value)
                if normalized and normalized not in seen:
                    seen.append(normalized)
            return seen
    except Exception as e:
        print(f"[Ingester] Error extracting frustration targets without pandas from {csv_path}: {e}")
        return []


def parse_frustration_csv(csv_path: Path, pdb_name_filter: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Parse FrustraMPNN output CSV into structured frustration data.
    
    FrustraMPNN CSVs contain one row per position/mutation. For structural QC we want
    the native profile only, i.e. rows where mutation == wildtype.
    
    Returns:
        dict with keys:
            - high_count: int (residues with frust <= -1.0)
            - min_count: int (residues with frust >= 0.58)
            - pct_high: float (percent highly frustrated)
            - residues: list of dicts with {pos, chain, frust, frustClass}
    """
    try:
        import pandas as pd
        df = pd.read_csv(csv_path)

        if pdb_name_filter and "pdb" in df.columns:
            target = _normalize_frustration_target_name(pdb_name_filter)
            df = df[df["pdb"].astype(str).map(_normalize_frustration_target_name) == target]
            if df.empty:
                return None

        cols = [col for col in ["position", "chain", "frustration_pred", "wildtype", "mutation"] if col in df.columns]
        rows = df[cols].to_dict("records")
        return _summarize_frustration_rows(rows)
    except ImportError:
        # Fallback without pandas
        try:
            with open(csv_path, 'r') as f:
                reader = csv.DictReader(f)
                rows = []
                target = _normalize_frustration_target_name(pdb_name_filter) if pdb_name_filter else None
                for row in reader:
                    if target and row.get("pdb"):
                        if _normalize_frustration_target_name(row["pdb"]) != target:
                            continue
                    rows.append(row)

                return _summarize_frustration_rows(rows)
        except Exception as e:
            print(f"[Ingester] Error parsing frustration CSV without pandas: {e}")
            return None
    except Exception as e:
        print(f"[Ingester] Error parsing frustration CSV: {e}")
        return None


def parse_backbone_id(design_name: str) -> Optional[int]:
    """
    Extract backbone ID from design name.
    
    Formats:
    - antibody_job_2_seq_15_model_0 -> 2
    - boltzgen_input_5 -> 5
    - rfd_design_3 -> 3
    """
    import re
    
    normalized = str(design_name or "").strip()
    while re.match(r'^\d+_', normalized):
        normalized = normalized.split('_', 1)[1]

    patterns = (
        r"(?:^|[_-])rfantibody[_-]?child[_-]?(\d+)(?=[_-]|$)",
        r"(?:^|[_-])child[_-]?(\d+)(?=[_-]|$)",
        r"(?:^|[_-])(?:job|input|design)[_-]?(\d+)(?=[_-]|$)",
    )
    for pattern in patterns:
        matches = re.findall(pattern, normalized)
        if matches:
            return int(matches[-1])
    return None


def _parse_job_params(raw_params: Any) -> Dict[str, Any]:
    if isinstance(raw_params, dict):
        return raw_params
    if isinstance(raw_params, str) and raw_params:
        try:
            parsed = json.loads(raw_params)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _job_has_explicit_binder_target_roles(job: Optional[Job]) -> bool:
    if not job:
        return False
    params = _parse_job_params(job.params)
    model_id = str(job.model_id or "").strip().lower()
    mode = str(job.mode or "").strip().lower()
    rfd_mode = str(params.get("rfd_mode") or "").strip().lower()

    if rfd_mode == "antibody_denovo_pipeline":
        return True
    if "antibody" in model_id or "antibody" in mode:
        return True
    if params.get("antibody_chains"):
        return True
    return False


def _parse_epitope_residues(raw_value: Any) -> Optional[List[str]]:
    if not raw_value:
        return None
    if isinstance(raw_value, list):
        cleaned = [str(item).strip() for item in raw_value if str(item).strip()]
        return cleaned or None
    if isinstance(raw_value, str):
        cleaned = [item.strip() for item in raw_value.split(",") if item.strip()]
        return cleaned or None
    return None


def _parse_chain_ids(raw_value: Any) -> List[str]:
    import re

    if not raw_value:
        return []
    if isinstance(raw_value, (list, tuple, set)):
        values = [str(item).strip() for item in raw_value if str(item).strip()]
    else:
        values = [token.strip() for token in re.split(r"[,;|\s]+", str(raw_value)) if token.strip()]

    ordered: List[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _validation_role_fields(job: Optional[Job], job_params: Dict[str, Any]) -> Dict[str, Optional[str]]:
    if not _job_has_explicit_binder_target_roles(job):
        return {
            "detected_antibody_chains": None,
            "detected_target_chain": None,
        }

    binder_chains = _parse_chain_ids(job_params.get("antibody_chains") or job_params.get("binder_chains"))
    target_chains = _parse_chain_ids(job_params.get("antigen_chains") or job_params.get("target_chains"))
    return {
        "detected_antibody_chains": ",".join(binder_chains) or None,
        "detected_target_chain": ",".join(target_chains) or None,
    }


def _strict_aligned_error_fields(
    *,
    structure_path: Path,
    summary_json_path: Optional[Path],
    detected_antibody_chains: Optional[str],
    detected_target_chain: Optional[str],
) -> Dict[str, Any]:
    detected = detect_aligned_error_artifact(
        structure_path=structure_path,
        summary_json_path=summary_json_path,
    )
    if not detected:
        return {}

    artifact_path, artifact_format, artifact_key = detected
    fields: Dict[str, Any] = {
        "aligned_error_path": str(artifact_path),
        "aligned_error_format": artifact_format,
        "aligned_error_key": artifact_key,
    }
    try:
        artifact = load_aligned_error_artifact(
            aligned_error_path=artifact_path,
            aligned_error_format=artifact_format,
            matrix_key=artifact_key,
            structure_path=structure_path,
        )
        ipsae_result = compute_ipsae_interface(
            artifact,
            binder_chains=_parse_chain_ids(detected_antibody_chains),
            target_chains=_parse_chain_ids(detected_target_chain),
        )
        fields.update(
            {
                "ipsae": safe_float(ipsae_result.get("ipsae")),
                "ipsae_binder_to_target": safe_float(ipsae_result.get("ipsae_binder_to_target")),
                "ipsae_target_to_binder": safe_float(ipsae_result.get("ipsae_target_to_binder")),
                "ipsae_d0chn": safe_float(ipsae_result.get("ipsae_d0chn")),
                "ipsae_d0dom": safe_float(ipsae_result.get("ipsae_d0dom")),
                "ipsae_chain_pair": str(ipsae_result.get("ipsae_chain_pair")).strip() if ipsae_result.get("ipsae_chain_pair") else None,
                "ipsae_pae_cutoff": safe_float(ipsae_result.get("pae_cutoff")),
                "ipsae_dist_cutoff": safe_float(ipsae_result.get("dist_cutoff")),
            }
        )
    except Exception as exc:
        print(f"[Ingester] Failed strict aligned-error processing for {structure_path}: {exc}")
    return fields


def _load_json_payload(json_path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if not json_path or not json_path.exists():
        return None
    try:
        payload = json.loads(json_path.read_text())
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_fampnn_metrics(fam_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(fam_payload, dict):
        return {
            "avg_psce": None,
            "max_residue_psce": None,
            "min_residue_psce": None,
            "chain_avg_psce": None,
            "sequence": None,
            "binder_sequence": None,
            "binder_length": None,
            "mpnn_score": None,
        }

    chain_avg_raw = fam_payload.get("chain_avg_psce")
    chain_avg_psce: Optional[Dict[str, float]] = None
    if isinstance(chain_avg_raw, dict):
        normalized_chain_avg: Dict[str, float] = {}
        for chain_id, raw_value in chain_avg_raw.items():
            numeric = safe_float(raw_value)
            if numeric is None:
                continue
            normalized_chain_avg[str(chain_id)] = numeric
        chain_avg_psce = normalized_chain_avg or None

    avg_psce = safe_float(fam_payload.get("fampnn_avg_psce"))
    if avg_psce is None and chain_avg_psce:
        avg_psce = sum(chain_avg_psce.values()) / len(chain_avg_psce)

    sequence_text = fam_payload.get("sequence")
    sequence = str(sequence_text).strip() if isinstance(sequence_text, str) and sequence_text.strip() else None
    binder_sequence: Optional[str] = None
    binder_length: Optional[int] = None
    if sequence:
        first_chain = sequence.split("|", 1)[0].strip()
        if ":" in first_chain:
            _, chain_sequence = first_chain.split(":", 1)
            first_chain = chain_sequence.strip()
        if first_chain:
            binder_sequence = first_chain
            binder_length = len(first_chain)

    return {
        "avg_psce": avg_psce,
        "max_residue_psce": safe_float(fam_payload.get("fampnn_max_residue_psce")),
        "min_residue_psce": safe_float(fam_payload.get("fampnn_min_residue_psce")),
        "chain_avg_psce": chain_avg_psce,
        "sequence": sequence,
        "binder_sequence": binder_sequence,
        "binder_length": binder_length,
        "mpnn_score": safe_float(fam_payload.get("mpnn_score") or fam_payload.get("seq_mpnn_score")),
    }


def _ordered_unique(values: List[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _candidate_source_design_names(design_name: str) -> List[str]:
    stem = Path(str(design_name or "")).stem.strip()
    if not stem:
        return []

    candidates = [stem]

    if "_ppiflow_" in stem:
        upstream = stem.split("_ppiflow_", 1)[0].strip()
        if upstream:
            candidates.append(upstream)

    if "_seq_" in stem:
        prefix, suffix = stem.rsplit("_seq_", 1)
        candidates.append(prefix)
        if suffix.isdigit():
            candidates.append(f"{prefix}_seq_{suffix}")

    return _ordered_unique(candidates)


def _parse_source_pdb_paths(raw_value: Any) -> List[Path]:
    if raw_value in (None, "", [], (), {}):
        return []
    if isinstance(raw_value, (list, tuple, set)):
        values = list(raw_value)
    else:
        values = [part.strip() for part in str(raw_value).split(",") if part.strip()]
    paths: List[Path] = []
    for value in values:
        try:
            paths.append(Path(str(value)).expanduser())
        except Exception:
            continue
    return paths


def _build_source_design_index(params: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for pdb_path in _parse_source_pdb_paths(params.get("pdb_paths")):
        payload = {
            "pdb_path": str(pdb_path),
            "design_name": pdb_path.stem,
        }
        for key in _candidate_source_design_names(pdb_path.stem):
            index.setdefault(key, payload)
    return index


def _extract_stage_settings(params: Dict[str, Any], stage_family: Optional[str], stage_mode: Optional[str]) -> Optional[Dict[str, Any]]:
    family = str(stage_family or "").strip().lower()
    if family == "maturation":
        family = "ppiflow"

    key_groups = {
        "fampnn": (
            "fampnn_checkpoint",
            "fampnn_checkpoint_path",
            "fampnn_constraint_mode",
            "fampnn_temperature",
            "fampnn_num_steps",
            "fampnn_psce_threshold",
            "fampnn_exclude_cys",
            "fampnn_repack_last",
            "fampnn_seq_only",
            "fampnn_extra_config",
            "seqs_per_design",
        ),
        "ppiflow": (
            "ppiflow_mode",
            "ppiflow_stage_mode",
            "ppiflow_start_t",
            "ppiflow_samples_per_target",
            "ppiflow_retry_limit",
            "ppiflow_config",
            "ppiflow_checkpoint",
            "ppiflow_checkpoint_path",
            "ppiflow_weights_dir",
            "ppiflow_antigen_chain",
            "ppiflow_heavy_chain",
            "ppiflow_light_chain",
            "ppiflow_selected_loops",
            "maturation_design_mode",
            "maturation_redesign_enabled",
            "maturation_redesign_temp",
            "maturation_redesign_steps",
            "maturation_redesign_top_n",
            "maturation_anchor_threshold",
            "maturation_anchor_distance_cutoff",
            "maturation_min_improvement",
            "maturation_filter_percentile",
        ),
    }

    settings: Dict[str, Any] = {}
    for key in key_groups.get(family, ()):
        value = params.get(key)
        if value in (None, "", [], {}, ()):
            continue
        settings[key] = value

    if stage_mode and "stage_mode" not in settings:
        settings["stage_mode"] = stage_mode
    return settings or None


def _find_first_existing_path(candidates: List[Path]) -> Optional[Path]:
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except Exception:
            continue
    return None


def _find_fampnn_sidecar_path(structure_path: Path, output_path: Optional[Path] = None) -> Optional[Path]:
    candidates = [structure_path.with_suffix(".json")]
    if output_path is not None:
        candidates.extend([
            output_path / "run" / "ppiflow" / "redesign_debug" / f"{structure_path.stem}.json",
            output_path / "ppiflow" / "redesign_debug" / f"{structure_path.stem}.json",
            output_path / "run" / "fampnn" / "results" / f"{structure_path.stem}.json",
            output_path / "fampnn" / "results" / f"{structure_path.stem}.json",
        ])
    return _find_first_existing_path(candidates)


def _normalize_scope_value(raw_value: Any) -> Optional[Dict[str, Any]]:
    if raw_value is None:
        return None
    if isinstance(raw_value, dict):
        cleaned = {key: value for key, value in raw_value.items() if value not in (None, "", [], {}, ())}
        return cleaned or None
    if isinstance(raw_value, (list, tuple, set)):
        cleaned = [str(item).strip().upper() for item in raw_value if str(item).strip()]
        return {"selected_loops": sorted(set(cleaned))} if cleaned else None
    if isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            return None
        tokens = [token.strip().upper() for token in text.replace(";", ",").replace("|", ",").split(",") if token.strip()]
        if len(tokens) > 1:
            return {"selected_loops": sorted(set(tokens))}
        return {"value": text}
    return {"value": raw_value}


def _job_stage_context(job: Optional[Job]) -> Dict[str, Any]:
    params = _parse_job_params(job.params if job else None)
    model_id = str(job.model_id or "").strip().lower() if job else ""
    mode = str(job.mode or "").strip().lower() if job else ""
    stage_family = str(params.get("stage_family") or params.get("ppiflow_stage_family") or "").strip().lower() or None
    stage_mode = str(params.get("stage_mode") or params.get("ppiflow_stage_mode") or "").strip().lower() or None
    if not stage_family:
        if mode == "maturation_child":
            stage_family = "ppiflow"
            stage_mode = stage_mode or "maturation"
        elif "fampnn" in model_id:
            stage_family = "fampnn"
        elif "antibody" in model_id or "antibody" in mode:
            stage_family = "antibody"

    selected_loop_scope = _normalize_scope_value(params.get("selected_loop_scope"))
    if selected_loop_scope is None:
        for key in (
            "ppiflow_selected_loops",
            "selected_loops",
            "loop_ids",
            "selected_residues",
            "antibody_design_loops",
        ):
            selected_loop_scope = _normalize_scope_value(params.get(key))
            if selected_loop_scope:
                break

    lineage_root_job_id = (
        params.get("lineage_root_job_id")
        or params.get("iteration_source_root_job_id")
        or params.get("resume_root_job_id")
        or getattr(job, "parent_job_id", None)
        or getattr(job, "id", None)
    )
    origin_job_id = (
        params.get("selection_source_job_id")
        or params.get("iteration_source_job_id")
        or getattr(job, "parent_job_id", None)
        or getattr(job, "id", None)
    )

    selection_manifest = None
    selection_dir = params.get("iteration_selection_dir")
    if selection_dir:
        try:
            manifest_path = Path(str(selection_dir)).expanduser() / "selection_manifest.json"
            selection_manifest = _load_json_payload(manifest_path)
        except Exception:
            selection_manifest = None

    selection_index: Dict[str, Dict[str, Any]] = {}
    if selection_manifest and isinstance(selection_manifest.get("designs"), list):
        for item in selection_manifest["designs"]:
            if not isinstance(item, dict):
                continue
            for key in (
                str(item.get("selection_pdb_path") or "").strip(),
                Path(str(item.get("selection_pdb_path") or "")).stem if item.get("selection_pdb_path") else "",
                str(item.get("design_id") or "").strip(),
                str(item.get("design_name") or "").strip(),
            ):
                if key:
                    selection_index.setdefault(key, item)

    source_design_index = _build_source_design_index(params)
    stage_settings = _extract_stage_settings(params, stage_family, stage_mode)

    provenance = {
        "job_id": getattr(job, "id", None),
        "job_name": getattr(job, "name", None),
        "model_id": getattr(job, "model_id", None),
        "mode": getattr(job, "mode", None),
        "stage_family": stage_family,
        "stage_mode": stage_mode,
        "lineage_root_job_id": lineage_root_job_id,
        "origin_job_id": origin_job_id,
        "selection_source_type": params.get("selection_source_type"),
        "selection_source_job_id": params.get("selection_source_job_id") or params.get("iteration_source_job_id"),
        "selection_dataset_name": params.get("selection_dataset_name"),
        "selected_loop_scope": selected_loop_scope,
        "iteration_action": params.get("iteration_action"),
        "ppiflow_stage_target": params.get("ppiflow_stage_target"),
        "ppiflow_stage_mode": params.get("ppiflow_stage_mode"),
        "stage_settings": stage_settings,
        "selection_manifest": selection_manifest,
    }
    return {
        "params": params,
        "stage_family": stage_family,
        "stage_mode": stage_mode,
        "lineage_root_job_id": lineage_root_job_id,
        "origin_job_id": origin_job_id,
        "selected_loop_scope": selected_loop_scope,
        "provenance": provenance,
        "selection_index": selection_index,
        "source_design_index": source_design_index,
    }


async def _resolve_job_param_from_lineage(
    session: AsyncSession,
    job: Optional[Job],
    params: Dict[str, Any],
    *keys: str,
) -> Any:
    for key in keys:
        value = params.get(key)
        if value not in (None, "", [], {}):
            return value

    related_job_ids: List[str] = []
    if job:
        for candidate in (
            getattr(job, "parent_job_id", None),
            params.get("selection_source_job_id"),
            params.get("iteration_source_job_id"),
            params.get("iteration_source_root_job_id"),
            params.get("resume_root_job_id"),
            params.get("lineage_root_job_id"),
        ):
            candidate_id = str(candidate).strip() if candidate else ""
            if candidate_id and candidate_id not in related_job_ids and candidate_id != getattr(job, "id", None):
                related_job_ids.append(candidate_id)

    for job_id in related_job_ids:
        result = await session.execute(select(Job.params).where(Job.id == job_id))
        row = result.one_or_none()
        lineage_params = _parse_job_params(row[0] if row else None)
        for key in keys:
            value = lineage_params.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


async def _resolve_parent_design_lineage(
    session: AsyncSession,
    context: Dict[str, Any],
    design_name: str,
    *,
    cache: Optional[Dict[str, Optional[Design]]] = None,
) -> Dict[str, Optional[str]]:
    cache = cache or {}
    selection_index = context.get("selection_index") or {}
    manifest_item = selection_index.get(design_name)
    if manifest_item is None:
        stem = Path(design_name).stem
        manifest_item = selection_index.get(stem)
    if manifest_item is None:
        for key in _candidate_source_design_names(design_name):
            match = (context.get("source_design_index") or {}).get(key)
            if match:
                manifest_item = match
                break

    parent_design_id = str(manifest_item.get("design_id")).strip() if manifest_item and manifest_item.get("design_id") else None
    source_pdb_path = str(manifest_item.get("pdb_path")).strip() if manifest_item and manifest_item.get("pdb_path") else None
    source_design_name = str(manifest_item.get("design_name")).strip() if manifest_item and manifest_item.get("design_name") else None
    parent_design = None
    if parent_design_id:
        parent_design = cache.get(parent_design_id)
        if parent_design is None and parent_design_id not in cache:
            result = await session.execute(
                select(Design).options(
                    load_only(
                        Design.id,
                        Design.job_id,
                        Design.origin_design_id,
                        Design.origin_backbone_design_id,
                        Design.stage_family,
                        Design.stage_mode,
                        Design.cdr_h1_length,
                        Design.cdr_h2_length,
                        Design.cdr_h3_length,
                        Design.cdr_l1_length,
                        Design.cdr_l2_length,
                        Design.cdr_l3_length,
                    )
                ).where(Design.id == parent_design_id)
            )
            parent_design = result.scalar_one_or_none()
            cache[parent_design_id] = parent_design
    elif source_pdb_path:
        cache_key = f"pdb::{source_pdb_path}"
        parent_design = cache.get(cache_key)
        if parent_design is None and cache_key not in cache:
            result = await session.execute(
                select(Design).options(
                    load_only(
                        Design.id,
                        Design.job_id,
                        Design.origin_design_id,
                        Design.origin_backbone_design_id,
                        Design.stage_family,
                        Design.stage_mode,
                        Design.pdb_path,
                        Design.name,
                        Design.cdr_h1_length,
                        Design.cdr_h2_length,
                        Design.cdr_h3_length,
                        Design.cdr_l1_length,
                        Design.cdr_l2_length,
                        Design.cdr_l3_length,
                    )
                ).where(Design.pdb_path == source_pdb_path)
            )
            parent_design = result.scalars().first()
            cache[cache_key] = parent_design
        if parent_design is None and source_design_name:
            cache_key = f"name::{source_design_name}"
            parent_design = cache.get(cache_key)
            if parent_design is None and cache_key not in cache:
                result = await session.execute(
                    select(Design).options(
                        load_only(
                            Design.id,
                            Design.job_id,
                            Design.origin_design_id,
                            Design.origin_backbone_design_id,
                            Design.stage_family,
                            Design.stage_mode,
                            Design.name,
                            Design.cdr_h1_length,
                            Design.cdr_h2_length,
                            Design.cdr_h3_length,
                            Design.cdr_l1_length,
                            Design.cdr_l2_length,
                            Design.cdr_l3_length,
                        )
                    ).where(Design.name == source_design_name)
                )
                parent_design = result.scalars().first()
                cache[cache_key] = parent_design
        if parent_design is not None:
            parent_design_id = parent_design.id

    origin_design_id = None
    origin_backbone_design_id = None
    origin_job_id = None
    if parent_design:
        origin_design_id = parent_design.origin_design_id or parent_design.id
        origin_backbone_design_id = parent_design.origin_backbone_design_id or parent_design.id
        origin_job_id = parent_design.job_id
    elif parent_design_id:
        origin_design_id = parent_design_id
        origin_backbone_design_id = parent_design_id
        origin_job_id = str(manifest_item.get("design_job_id")).strip() if manifest_item and manifest_item.get("design_job_id") else None

    return {
        "parent_design_id": parent_design_id,
        "origin_design_id": origin_design_id,
        "origin_backbone_design_id": origin_backbone_design_id,
        "origin_job_id": origin_job_id,
        "source_cdr_lengths": _extract_design_cdr_lengths(parent_design),
        "selection_manifest_item": manifest_item,
        "source_pdb_path": source_pdb_path,
        "source_design_name": source_design_name,
    }


def _parse_hlt_cdr_lengths(structure_path: Optional[Path]) -> Dict[str, int]:
    """
    Parse RFantibody-style HLT REMARK labels from a PDB and return actual
    per-structure loop lengths.

    These values are design-specific. They should not be substituted with the
    job-level configured loop spans, which only describe the intended search
    space and can differ across RFantibody outputs.
    """
    if not structure_path or not structure_path.exists() or structure_path.suffix.lower() != ".pdb":
        return {}

    counts: Dict[str, int] = {}
    try:
        with open(structure_path, "r") as handle:
            for line in handle:
                if not line.startswith("REMARK PDBinfo-LABEL:"):
                    continue
                parts = line.split()
                if len(parts) < 4:
                    continue
                loop_id = parts[3].upper()
                if loop_id in {"H1", "H2", "H3", "L1", "L2", "L3"}:
                    counts[loop_id] = counts.get(loop_id, 0) + 1
    except Exception as e:
        print(f"[Ingester] Failed to parse HLT CDR labels from {structure_path}: {e}")
        return {}

    return counts


def _extract_design_cdr_lengths(design: Optional[Design]) -> Dict[str, int]:
    if design is None:
        return {}
    return {
        "H1": getattr(design, "cdr_h1_length", None),
        "H2": getattr(design, "cdr_h2_length", None),
        "H3": getattr(design, "cdr_h3_length", None),
        "L1": getattr(design, "cdr_l1_length", None),
        "L2": getattr(design, "cdr_l2_length", None),
        "L3": getattr(design, "cdr_l3_length", None),
    }


def _coalesce_cdr_lengths(*sources: Optional[Dict[str, Any]]) -> Dict[str, int]:
    merged: Dict[str, int] = {}
    for loop_id in ("H1", "H2", "H3", "L1", "L2", "L3"):
        for source in sources:
            if not source:
                continue
            value = source.get(loop_id)
            if value in (None, "", 0):
                continue
            try:
                merged[loop_id] = int(value)
                break
            except (TypeError, ValueError):
                continue
    return merged


def _parse_source_cdr_lengths(source_pdb_path: Optional[str]) -> Dict[str, int]:
    raw_path = str(source_pdb_path or "").strip()
    if not raw_path:
        return {}
    try:
        return _parse_hlt_cdr_lengths(Path(raw_path).expanduser())
    except Exception:
        return {}


def _parse_ppiflow_sample_index(name: Optional[str]) -> Optional[int]:
    if not name:
        return None
    match = re.search(r"_ppiflow_sample(\d+)$", str(name), re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


async def ingest_job_results(
    job_id: str, 
    output_dir: str, 
    session: AsyncSession,
    epitope_residues: Optional[list] = None
) -> int:
    """
    Parse pipeline outputs and populate Design table.
    
    Args:
        job_id: The job ID to associate designs with
        output_dir: Path to the job's output directory (e.g., legacy bms_results/job_xxx)
        session: Async database session
        epitope_residues: Optional list of epitope residues (e.g., ["A111", "A112"])
            for calculating contact metrics
        
    Returns:
        Number of designs ingested
    """
    # Resolve relative paths to absolute using data root
    output_path = Path(output_dir)
    if not output_path.is_absolute():
        output_path = get_data_root() / output_dir

    if not output_path.exists():
        print(f"[Ingester] Output dir not found: {output_path}")
        return 0

    job_result = await session.execute(select(Job).where(Job.id == job_id))
    current_job = job_result.scalar_one_or_none()
    allow_binder_target_metrics = _job_has_explicit_binder_target_roles(current_job)
    job_context = _job_stage_context(current_job)
    lineage_cache: Dict[str, Optional[Design]] = {}
    
    csv_path = output_path / "results" / "all_designs.csv"
    
    if not csv_path.exists():
        print(f"[Ingester] No all_designs.csv found at {csv_path}")
        # Proceed to try other methods if CSV missing
    else:
        # Check if designs already ingested for this job (only if we have a CSV to potentially dupe against? 
        # Actually logic for dupe check is before extraction.
        pass
    
    # Extract PDB files from tar.gz archives
    pdb_dir = extract_pdb_files(output_path)
    
    designs_created = 0

    designs_created = 0

    # Stage-review rows are ephemeral parent-review artifacts. Remove them before
    # real final-stage ingestion so completed jobs don't double-count review rows.
    await session.execute(
        delete(Design).where(
            Design.job_id == job_id,
            Design.source_stage.is_not(None),
        )
    )
    
    # Only try to process CSV if it exists
    if csv_path.exists():
        # Check if designs already ingested for this job
        existing = await session.execute(
            select(Design).where(
                Design.job_id == job_id,
                Design.source_stage.is_(None),
            ).limit(1)
        )
        if existing.scalar_one_or_none():
            print(f"[Ingester] Designs already ingested for job {job_id}")
            return 0
        
        try:
            with open(csv_path, 'r') as f:
                reader = csv.DictReader(f)
                
                for row in reader:
                    # Map CSV columns to Design fields
                    design_name = row.get('description', f'design_{designs_created}')
                    structure_path_str = find_pdb_path(output_path, design_name)
                    structure_path = Path(structure_path_str) if structure_path_str else None
                    structure_cdr_lengths = _parse_hlt_cdr_lengths(structure_path)
                    fam_json_path = _find_fampnn_sidecar_path(structure_path, output_path) if structure_path else None
                    fam_payload = _load_json_payload(fam_json_path) if fam_json_path else None
                    fam_metrics = _extract_fampnn_metrics(fam_payload)
                    row_mpnn_score = safe_float(row.get('seq_mpnn_score'))
                    row_fampnn_psce = safe_float(row.get('seq_fampnn_psce'))
                    lineage = await _resolve_parent_design_lineage(
                        session,
                        job_context,
                        design_name,
                        cache=lineage_cache,
                    )
                    design_provenance = {
                        **job_context.get("provenance", {}),
                        "artifact_group": "rfantibody",
                        "structure_path": str(structure_path) if structure_path else None,
                    }
                    if lineage.get("source_design_name"):
                        design_provenance["source_design_name"] = lineage["source_design_name"]
                    if lineage.get("source_pdb_path"):
                        design_provenance["source_pdb_path"] = lineage["source_pdb_path"]
                    if fam_payload:
                        design_provenance["fampnn"] = fam_payload

                    design = Design(
                        id=str(uuid.uuid4()),
                        job_id=job_id,
                        name=design_name,
                        pdb_path=str(structure_path) if structure_path else None,
                        json_path=str(fam_json_path) if fam_json_path and fam_json_path.exists() else None,
                        
                        # Backbone grouping
                        backbone_id=parse_backbone_id(design_name),
                        
                        # Structural metrics (predicted structures)
                        num_helices=safe_int(row.get('pr_helices')),
                        num_strands=safe_int(row.get('pr_strands')),
                        rog=safe_float(row.get('pr_RoG')),
                        # RFdiffusion backbone metrics
                        rfd_rog=safe_float(row.get('rfd_RoG')),
                        
                        # Sequence design metrics
                        mpnn_score=row_mpnn_score if row_mpnn_score is not None else fam_metrics.get("mpnn_score"),
                        fampnn_psce=row_fampnn_psce if row_fampnn_psce is not None else fam_metrics.get("avg_psce"),
                        binder_length=fam_metrics.get("binder_length"),
                        
                        # Structure prediction metrics (AF2/Boltz)
                        plddt_overall=safe_float(row.get('pr_plddt') or row.get('plddt')),
                        plddt_binder=safe_float(row.get('pr_plddt_binder')),
                        plddt_target=safe_float(row.get('pr_plddt_target')),
                        pae_interaction=safe_float(row.get('pr_pae_interaction')),
                        pae_overall=safe_float(row.get('pr_pae') or row.get('pae')),
                        rmsd_overall=safe_float(row.get('pr_rmsd')),
                        rmsd_binder=safe_float(row.get('pr_rmsd_binder')),
                        cdr_h1_length=structure_cdr_lengths.get("H1"),
                        cdr_h2_length=structure_cdr_lengths.get("H2"),
                        cdr_h3_length=structure_cdr_lengths.get("H3"),
                        cdr_l1_length=structure_cdr_lengths.get("L1"),
                        cdr_l2_length=structure_cdr_lengths.get("L2"),
                        cdr_l3_length=structure_cdr_lengths.get("L3"),
                        
                        # Boltz-2 specific
                        conf_score=safe_float(row.get('conf_score')),
                        ptm=safe_float(row.get('ptm')),
                        
                        # User annotations (defaults)
                        is_favorite=False,
                        notes=None,
                        
                        created_at=datetime.utcnow()
                    )

                    rfa_trb = load_rfantibody_trb_summary(structure_path) if structure_path else {}
                    if rfa_trb:
                        design.plddt_overall = safe_float(rfa_trb.get("plddt_overall"))
                        design.residue_plddt = rfa_trb.get("residue_plddt")
                        design.rfa_hotspot_min_distance = safe_float(rfa_trb.get("rfa_hotspot_min_distance"))
                        design.rfa_hotspot_avg_min_distance = safe_float(rfa_trb.get("rfa_hotspot_avg_min_distance"))
                        design.rfa_runtime_seconds = safe_float(rfa_trb.get("rfa_runtime_seconds"))
                        design.rfa_device = rfa_trb.get("rfa_device")
                        design.rfa_diffusion_steps = safe_int(rfa_trb.get("rfa_diffusion_steps"))
                        design.rfa_noise_scale_ca = safe_float(rfa_trb.get("rfa_noise_scale_ca"))
                        design.rfa_noise_scale_frame = safe_float(rfa_trb.get("rfa_noise_scale_frame"))
                        design.rfa_guide_scale = safe_float(rfa_trb.get("rfa_guide_scale"))
                        design.rfa_plddt_initial = safe_float(rfa_trb.get("rfa_plddt_initial"))
                        design.rfa_plddt_final = safe_float(rfa_trb.get("rfa_plddt_final"))
                        design.rfa_plddt_delta = safe_float(rfa_trb.get("rfa_plddt_delta"))
                        design.rfa_plddt_selected = safe_float(rfa_trb.get("rfa_plddt_selected"))
                        design.rfa_plddt_nonselected = safe_float(rfa_trb.get("rfa_plddt_nonselected"))
                        design.rfa_design_loops = rfa_trb.get("rfa_design_loops")
                        design.rfa_hotspots = rfa_trb.get("rfa_hotspots")
                        design_provenance["rfantibody"] = rfa_trb
                    combined_confidence: Dict[str, Any] = {}
                    if fam_payload:
                        combined_confidence["fampnn"] = fam_payload
                    if rfa_trb:
                        combined_confidence["rfantibody"] = rfa_trb.get("rfa_metadata") or rfa_trb
                    if combined_confidence:
                        design.confidence_metrics = combined_confidence
                    lineage = await _resolve_parent_design_lineage(
                        session,
                        job_context,
                        design.name,
                        cache=lineage_cache,
                    )
                    design.lineage_root_job_id = job_context.get("lineage_root_job_id")
                    design.parent_design_id = lineage["parent_design_id"]
                    design.origin_design_id = lineage["origin_design_id"]
                    design.origin_job_id = lineage["origin_job_id"] or job_context.get("origin_job_id")
                    design.origin_backbone_design_id = lineage["origin_backbone_design_id"]
                    design.stage_family = job_context.get("stage_family")
                    design.stage_mode = job_context.get("stage_mode")
                    design.selected_loop_scope = job_context.get("selected_loop_scope")
                    design.provenance = design_provenance
                    
                    session.add(design)
                    designs_created += 1
            
            await session.commit()
            print(f"[Ingester] Ingested {designs_created} designs for job {job_id}")
            
        except Exception as e:
            print(f"[Ingester] Error ingesting results: {e}")
            await session.rollback()
            # Don't return 0 here, let it fall through to valid loose file check if CSV failed partial?
            # Or return 0? Standard flow usually returns if error.
            # But let's allow fallback if designs_created is still 0
            pass
    
    if designs_created == 0 and str(current_job.mode or "").strip().lower() == "maturation_child":
        print(f"[Ingester] No CSV designs for maturation child {job_id}. Trying published PPIFlow results...")
        designs_created = await ingest_published_maturation_structures(job_id, output_path, session, current_job=current_job)

    if designs_created == 0:
        print(f"[Ingester] No designs found in CSV or CSV missing. Trying loose files...")
        designs_created = await ingest_loose_files(job_id, output_path, session, current_job=current_job)

    # Post-ingestion: Attach supplementary metrics from pipeline stages
    if designs_created > 0:
        await ingest_screening_data(job_id, output_path, session)
        await ingest_frustration_data(job_id, output_path, session)
        await ingest_maturation_data(job_id, output_path, session)

    return designs_created


async def ingest_screening_data(
    job_id: str,
    output_path: Path,
    session: AsyncSession,
) -> int:
    """
    Backfill contact-distance metrics onto existing Design rows.

    Reads the ``rfantibody_screening_summary.csv`` produced by
    ``screen_rfantibody_backbones.py``.  Because the CSV design names
    correspond to the *original* RFA output filenames (e.g.
    ``001_<uuid>``) while the ingester stores designs under their
    *collected* names (e.g. ``job0_rfantibody_child_0``), exact name
    matching is tried first, then ordinal matching (sorted CSV rows →
    sorted designs that have no contact data yet).
    """
    from database import Job

    # ── Build lineage-aware job-ID set ───────────────────────────────────
    job_info = await session.execute(select(Job).where(Job.id == job_id))
    current_job = job_info.scalar_one_or_none()

    design_job_ids = [job_id]
    if current_job:
        if current_job.parent_job_id:
            design_job_ids.append(current_job.parent_job_id)
        if current_job.batch_id:
            batch_res = await session.execute(
                select(Job.id).where(Job.batch_id == current_job.batch_id)
            )
            design_job_ids.extend([row[0] for row in batch_res.all()])
        child_result = await session.execute(
            select(Job.id).where(Job.parent_job_id == job_id)
        )
        design_job_ids.extend([row[0] for row in child_result.all()])
        params_dict = _parse_job_params(current_job.params)
        if params_dict.get("iteration_source_job_id"):
            design_job_ids.append(params_dict["iteration_source_job_id"])
        if params_dict.get("iteration_source_root_job_id"):
            design_job_ids.append(params_dict["iteration_source_root_job_id"])
    design_job_ids = list(set(design_job_ids))

    # ── Locate screening CSV ─────────────────────────────────────────────
    search_dirs = [
        output_path / "collected" / "rfantibody_filtered",
        output_path / "run" / "rfantibody_screen",
        output_path,
    ]
    csv_path: Optional[Path] = None
    for d in search_dirs:
        candidate = d / "rfantibody_screening_summary.csv"
        if candidate.exists():
            csv_path = candidate
            break

    if csv_path is None:
        return 0

    print(f"[Ingester] Found RFA screening CSV at {csv_path}")

    # ── Load CSV rows ────────────────────────────────────────────────────
    csv_rows: list[dict] = []
    try:
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ecc = safe_int(row.get("epitope_contact_count"))
                emd = safe_float(row.get("epitope_min_distance"))
                if ecc is not None or emd is not None:
                    csv_rows.append(row)
    except Exception as e:
        print(f"[Ingester] Error reading screening CSV {csv_path}: {e}")
        return 0

    if not csv_rows:
        return 0

    # ── Phase 1: exact name match ────────────────────────────────────────
    updated_count = 0
    unmatched_rows: list[dict] = []
    for row in csv_rows:
        design_name = row.get("design_name", "").strip()
        if not design_name:
            unmatched_rows.append(row)
            continue
        result = await session.execute(
            select(Design).where(
                Design.job_id.in_(design_job_ids),
                Design.name == design_name,
            )
        )
        design = result.scalars().first()
        if design:
            changed = _apply_screening_row(design, row)
            if changed:
                updated_count += 1
        else:
            unmatched_rows.append(row)

    # ── Phase 2: ordinal fallback for unmatched rows ─────────────────────
    if unmatched_rows:
        # Fetch ALL designs without contact data, sorted by name for
        # deterministic ordinal alignment with the CSV row order.
        result = await session.execute(
            select(Design).where(
                Design.job_id.in_(design_job_ids),
                Design.epitope_min_distance.is_(None),
            ).order_by(Design.name)
        )
        orphan_designs = result.scalars().all()

        if orphan_designs:
            print(f"[Ingester] Phase-2 ordinal match: {len(unmatched_rows)} CSV rows → {len(orphan_designs)} designs without contacts")
            for idx, row in enumerate(unmatched_rows):
                if idx >= len(orphan_designs):
                    break
                design = orphan_designs[idx]
                changed = _apply_screening_row(design, row)
                if changed:
                    updated_count += 1
                    print(f"[Ingester]   ordinal [{idx}] CSV '{row.get('design_name','')}' → DB '{design.name}': "
                          f"contacts={safe_int(row.get('epitope_contact_count'))}, "
                          f"min_dist={safe_float(row.get('epitope_min_distance'))}")

    if updated_count > 0:
        await session.commit()
        print(f"[Ingester] Updated {updated_count} designs with screening contact metrics")

    return updated_count


def _apply_screening_row(design: "Design", row: dict) -> bool:
    """Apply screening CSV fields to a Design. Returns True if anything changed."""
    changed = False
    ecc = safe_int(row.get("epitope_contact_count"))
    emd = safe_float(row.get("epitope_min_distance"))
    emad = safe_float(row.get("epitope_min_atom_distance"))
    tcc = safe_int(row.get("target_contact_count"))
    tmd = safe_float(row.get("target_min_distance"))
    tmad = safe_float(row.get("target_min_atom_distance"))
    screening_reason = row.get("screening_reason")
    if ecc is not None and design.epitope_contact_count is None:
        design.epitope_contact_count = ecc
        changed = True
    if emd is not None and design.epitope_min_distance is None:
        design.epitope_min_distance = emd
        changed = True
    if emad is not None and getattr(design, "epitope_min_atom_distance", None) is None:
        design.epitope_min_atom_distance = emad
        changed = True
    if row.get("epitope_nearest_antibody_residue") and getattr(design, "epitope_nearest_antibody_residue", None) is None:
        design.epitope_nearest_antibody_residue = str(row.get("epitope_nearest_antibody_residue"))
        changed = True
    if row.get("epitope_nearest_target_residue") and getattr(design, "epitope_nearest_target_residue", None) is None:
        design.epitope_nearest_target_residue = str(row.get("epitope_nearest_target_residue"))
        changed = True
    if row.get("epitope_nearest_antibody_atom") and getattr(design, "epitope_nearest_antibody_atom", None) is None:
        design.epitope_nearest_antibody_atom = str(row.get("epitope_nearest_antibody_atom"))
        changed = True
    if row.get("epitope_nearest_target_atom") and getattr(design, "epitope_nearest_target_atom", None) is None:
        design.epitope_nearest_target_atom = str(row.get("epitope_nearest_target_atom"))
        changed = True
    if row.get("epitope_mapping_mode") and getattr(design, "epitope_mapping_mode", None) is None:
        design.epitope_mapping_mode = str(row.get("epitope_mapping_mode"))
        changed = True
    ecd = safe_float(row.get("epitope_centroid_distance"))
    if ecd is not None and getattr(design, "epitope_centroid_distance", None) is None:
        design.epitope_centroid_distance = ecd
        changed = True
    if tcc is not None and design.target_contact_count is None:
        design.target_contact_count = tcc
        changed = True
    if tmd is not None and getattr(design, "target_min_distance", None) is None:
        design.target_min_distance = tmd
        changed = True
    if tmad is not None and getattr(design, "target_min_atom_distance", None) is None:
        design.target_min_atom_distance = tmad
        changed = True
    if row.get("target_nearest_antibody_residue") and getattr(design, "target_nearest_antibody_residue", None) is None:
        design.target_nearest_antibody_residue = str(row.get("target_nearest_antibody_residue"))
        changed = True
    if row.get("target_nearest_target_residue") and getattr(design, "target_nearest_target_residue", None) is None:
        design.target_nearest_target_residue = str(row.get("target_nearest_target_residue"))
        changed = True
    if row.get("target_nearest_antibody_atom") and getattr(design, "target_nearest_antibody_atom", None) is None:
        design.target_nearest_antibody_atom = str(row.get("target_nearest_antibody_atom"))
        changed = True
    if row.get("target_nearest_target_atom") and getattr(design, "target_nearest_target_atom", None) is None:
        design.target_nearest_target_atom = str(row.get("target_nearest_target_atom"))
        changed = True
    tcd = safe_float(row.get("target_centroid_distance"))
    if tcd is not None and getattr(design, "target_centroid_distance", None) is None:
        design.target_centroid_distance = tcd
        changed = True
    if row.get("detected_antibody_chains") and getattr(design, "detected_antibody_chains", None) is None:
        design.detected_antibody_chains = str(row.get("detected_antibody_chains"))
        changed = True
    if row.get("detected_target_chain") and getattr(design, "detected_target_chain", None) is None:
        design.detected_target_chain = str(row.get("detected_target_chain"))
        changed = True
    arc = safe_int(row.get("antibody_residue_count"))
    if arc is not None and getattr(design, "antibody_residue_count", None) is None:
        design.antibody_residue_count = arc
        changed = True
    trc = safe_int(row.get("target_residue_count"))
    if trc is not None and getattr(design, "target_residue_count", None) is None:
        design.target_residue_count = trc
        changed = True
    erc = safe_int(row.get("epitope_residue_count"))
    if erc is not None and getattr(design, "epitope_residue_count", None) is None:
        design.epitope_residue_count = erc
        changed = True
    passed_screen = row.get("passed_screen")
    if passed_screen not in (None, "") and getattr(design, "passed_screen", None) is None:
        design.passed_screen = str(passed_screen).strip().lower() == "true"
        changed = True
    hotspot_covered = safe_int(row.get("rfa_hotspot_covered_count"))
    if hotspot_covered is not None and getattr(design, "rfa_hotspot_covered_count", None) is None:
        design.rfa_hotspot_covered_count = hotspot_covered
        changed = True
    for json_key in ("rfa_loop_metrics", "rfa_hotspot_metrics"):
        value = row.get(json_key)
        if value and getattr(design, json_key, None) is None:
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except Exception:
                    pass
            setattr(design, json_key, value)
            changed = True
    if screening_reason and not design.screening_reason:
        design.screening_reason = str(screening_reason)
        changed = True
    return changed


def _apply_geometry_metrics(design: "Design", metrics: Dict[str, Any], *, overwrite: bool = True) -> bool:
    changed = False
    metric_fields = (
        "epitope_contact_count",
        "epitope_min_distance",
        "epitope_min_atom_distance",
        "epitope_nearest_antibody_residue",
        "epitope_nearest_target_residue",
        "epitope_nearest_antibody_atom",
        "epitope_nearest_target_atom",
        "epitope_mapping_mode",
        "epitope_centroid_distance",
        "target_contact_count",
        "target_min_distance",
        "target_min_atom_distance",
        "target_nearest_antibody_residue",
        "target_nearest_target_residue",
        "target_nearest_antibody_atom",
        "target_nearest_target_atom",
        "target_centroid_distance",
        "detected_antibody_chains",
        "detected_target_chain",
        "antibody_residue_count",
        "target_residue_count",
        "epitope_residue_count",
    )
    for field_name in metric_fields:
        if field_name not in metrics:
            continue
        new_value = metrics.get(field_name)
        if not overwrite and getattr(design, field_name, None) is not None:
            continue
        if getattr(design, field_name, None) != new_value:
            setattr(design, field_name, new_value)
            changed = True
    return changed


async def ingest_maturation_data(
    job_id: str,
    output_path: Path,
    session: AsyncSession
) -> int:
    """
    Parse PPIFlow maturation score JSONs and update matching designs.
    
    Parse PPIFlow score/filter JSONs and update matching designs.

    Preferred inputs are *_maturation_score.json or *_partial_flow_score.json.
    If only *_maturation_filter.json exists, the ingester can still hydrate
    provenance and any embedded score payload from the filter report.
    """
    # Check multiple possible locations (maturation_child publishes to run/ppiflow/results/)
    maturation_dirs = [
        output_path / "run" / "ppiflow" / "results",
        output_path / "ppiflow" / "results",
        output_path,
    ]
    
    score_files = []
    for d in maturation_dirs:
        if d.exists():
            score_files.extend(d.glob("*_maturation_score.json"))
            score_files.extend(d.glob("*_partial_flow_score.json"))
            score_files.extend(d.glob("*_maturation_filter.json"))
    
    if not score_files:
        return 0
    
    # Deduplicate by filename (same file may appear in multiple search paths)
    seen = set()
    unique_files = []
    for f in score_files:
        if f.name not in seen:
            seen.add(f.name)
            unique_files.append(f)
    score_files = unique_files
    
    print(f"[Ingester] Found {len(score_files)} maturation score JSONs to process")
    
    updated_count = 0
    job_result = await session.execute(select(Job).where(Job.id == job_id))
    current_job = job_result.scalar_one_or_none()
    job_context = _job_stage_context(current_job)
    lineage_cache: Dict[str, Optional[Design]] = {}
    geometry_applied_design_ids: set[str] = set()
    context_params = job_context.get("params") or {}
    epitope_residues = _parse_epitope_residues(
        context_params.get("epitope_residues") or context_params.get("selected_residues")
    )
    antibody_chain_hint = context_params.get("antibody_chains") or context_params.get("binder_chains")
    target_chain_hint = (
        context_params.get("antigen_chains")
        or context_params.get("target_chains")
        or context_params.get("target_chain")
    )
    reference_target_pdb = await _resolve_job_param_from_lineage(
        session,
        current_job,
        context_params,
        "target_pdb",
    )
    resolved_epitope_contact_threshold = await _resolve_job_param_from_lineage(
        session,
        current_job,
        context_params,
        "rfantibody_contact_distance_threshold",
        "contact_distance_threshold",
    )
    resolved_target_contact_threshold = await _resolve_job_param_from_lineage(
        session,
        current_job,
        context_params,
        "rfantibody_target_contact_distance_threshold",
        "target_contact_distance_threshold",
    )
    epitope_contact_distance_threshold = float(resolved_epitope_contact_threshold or 8.0)
    target_contact_distance_threshold = float(resolved_target_contact_threshold or 12.0)
    
    for json_path in score_files:
        stem = json_path.stem
        if stem.endswith("_maturation_score"):
            design_name = stem.replace("_maturation_score", "")
            record_kind = "maturation_score"
        elif stem.endswith("_partial_flow_score"):
            design_name = stem.replace("_partial_flow_score", "")
            record_kind = "partial_flow_score"
        elif stem.endswith("_maturation_filter"):
            design_name = stem.replace("_maturation_filter", "")
            record_kind = "maturation_filter"
        else:
            design_name = stem
            record_kind = "unknown"

        try:
            import json as json_mod
            data = json_mod.loads(json_path.read_text())
        except Exception as e:
            print(f"[Ingester] Error parsing maturation JSON {json_path}: {e}")
            continue
        score_data = data.get("score_data") if isinstance(data, dict) and isinstance(data.get("score_data"), dict) else data
        if not isinstance(score_data, dict):
            score_data = {}
        
        # Find matching design in DB (try both with and without job_id for child jobs)
        result = await session.execute(
            select(Design).where(
                Design.job_id == job_id,
                Design.name == design_name
            )
        )
        design = result.scalar_one_or_none()
        
        if not design:
            import sqlalchemy as sa
            # 1. Broaden search to include batch family and iteration source lineage
            job_info = await session.execute(
                select(Job).where(Job.id == job_id)
            )
            current_job = job_info.scalar_one_or_none()
            
            design_job_ids = [job_id]
            if current_job:
                if current_job.parent_job_id:
                    design_job_ids.append(current_job.parent_job_id)
                if current_job.batch_id:
                    batch_res = await session.execute(
                        select(Job.id).where(Job.batch_id == current_job.batch_id)
                    )
                    design_job_ids.extend([row[0] for row in batch_res.all()])
                
                # Check for iteration Source ID stored in params
                params_dict = _parse_job_params(current_job.params)
                if params_dict.get("iteration_source_job_id"):
                    design_job_ids.append(params_dict["iteration_source_job_id"])
                if params_dict.get("iteration_source_root_job_id"):
                    design_job_ids.append(params_dict["iteration_source_root_job_id"])

            if len(design_job_ids) > 1:
                # Deduplicate and query
                unique_ids = list(set(design_job_ids))
                result = await session.execute(
                    select(Design).where(
                        Design.job_id.in_(unique_ids),
                        Design.name == design_name
                    ).order_by(sa.desc(Design.created_at))  # Get newest if duplicates
                )
                design = result.scalars().first()
        
        if not design:
            continue
        
        # Update design with maturation metrics
        delta = score_data.get("delta_interface_score")
        matured = score_data.get("interface_score_matured") or score_data.get("interface_score_refined")
        rmsd_bb = score_data.get("rmsd_backbone")
        selected_delta = score_data.get("selected_delta_interface_score")
        selected_matured = score_data.get("selected_interface_score_matured")
        selected_rmsd = score_data.get("selected_rmsd_backbone")
        nonselected_rmsd = score_data.get("nonselected_rmsd_backbone")
        
        if delta is not None:
            design.maturation_delta_interface = float(delta)
        if matured is not None:
            design.maturation_interface_score = float(matured)
        if rmsd_bb is not None:
            design.maturation_rmsd = float(rmsd_bb)
        if selected_delta is not None:
            design.maturation_selected_delta_interface = float(selected_delta)
        if selected_matured is not None:
            design.maturation_selected_interface_score = float(selected_matured)
        if selected_rmsd is not None:
            design.maturation_selected_rmsd = float(selected_rmsd)
        if nonselected_rmsd is not None:
            design.maturation_nonselected_rmsd = float(nonselected_rmsd)

        if epitope_residues and design.id not in geometry_applied_design_ids:
            try:
                geometry_metrics = compute_contact_geometry_metrics(
                    pdb_path=Path(design.pdb_path),
                    epitope_residues=epitope_residues,
                    antibody_chain=design.detected_antibody_chains or antibody_chain_hint,
                    target_chain=design.detected_target_chain or target_chain_hint,
                    epitope_contact_distance_threshold=epitope_contact_distance_threshold,
                    target_contact_distance_threshold=target_contact_distance_threshold,
                    reference_target_pdb=reference_target_pdb,
                )
                _apply_geometry_metrics(design, geometry_metrics, overwrite=True)
            except Exception as exc:
                print(f"[Ingester] Failed PPIFlow contact-geometry scoring for {design.name}: {exc}")
            geometry_applied_design_ids.add(design.id)

        fam_json_path = _find_fampnn_sidecar_path(Path(design.pdb_path), output_path)
        fam_payload = _load_json_payload(fam_json_path)
        fam_metrics = _extract_fampnn_metrics(fam_payload)
        if fam_json_path and not design.json_path:
            design.json_path = str(fam_json_path)
        if fam_metrics.get("avg_psce") is not None:
            design.fampnn_psce = fam_metrics["avg_psce"]
        if fam_metrics.get("binder_length") is not None:
            design.binder_length = fam_metrics["binder_length"]
        if fam_metrics.get("mpnn_score") is not None:
            design.mpnn_score = fam_metrics["mpnn_score"]

        confidence_metrics = dict(design.confidence_metrics or {})
        if fam_payload:
            confidence_metrics["fampnn"] = fam_payload
        design.confidence_metrics = confidence_metrics or None
        if confidence_metrics:
            flag_modified(design, "confidence_metrics")

        lineage = await _resolve_parent_design_lineage(
            session,
            job_context,
            design.name,
            cache=lineage_cache,
        )
        provenance_ppiflow = (
            (design.provenance or {}).get("ppiflow")
            if isinstance(design.provenance, dict)
            else None
        )
        structure_cdr_lengths = _coalesce_cdr_lengths(
            _parse_hlt_cdr_lengths(Path(design.pdb_path) if design.pdb_path else None),
            _parse_source_cdr_lengths(
                provenance_ppiflow.get("source_pdb_path") if isinstance(provenance_ppiflow, dict) else None
            ),
            _extract_design_cdr_lengths(design),
            _parse_source_cdr_lengths(lineage.get("source_pdb_path")),
            lineage.get("source_cdr_lengths"),
        )
        design.cdr_h1_length = structure_cdr_lengths.get("H1")
        design.cdr_h2_length = structure_cdr_lengths.get("H2")
        design.cdr_h3_length = structure_cdr_lengths.get("H3")
        design.cdr_l1_length = structure_cdr_lengths.get("L1")
        design.cdr_l2_length = structure_cdr_lengths.get("L2")
        design.cdr_l3_length = structure_cdr_lengths.get("L3")
        design.plddt_overall = None
        design.residue_plddt = None
        provenance = copy.deepcopy(design.provenance or {})
        provenance.setdefault("job", job_context.get("provenance"))
        provenance["ppiflow"] = copy.deepcopy(provenance.get("ppiflow") or {})
        provenance["ppiflow"].update({
            "record_kind": record_kind,
            "selected_loop_scope": design.selected_loop_scope or job_context.get("selected_loop_scope"),
            "stage_settings": (job_context.get("provenance") or {}).get("stage_settings"),
        })
        if lineage.get("source_design_name"):
            provenance["ppiflow"]["source_design_name"] = lineage["source_design_name"]
        if lineage.get("source_pdb_path"):
            provenance["ppiflow"]["source_pdb_path"] = lineage["source_pdb_path"]
        if record_kind == "maturation_filter":
            provenance["ppiflow"]["maturation_filter"] = data
            provenance["ppiflow"]["maturation_filter_json"] = str(json_path)
            if score_data:
                provenance["ppiflow"]["maturation_score"] = score_data
        else:
            score_key = "partial_flow_score" if record_kind == "partial_flow_score" else "maturation_score"
            provenance["ppiflow"][score_key] = score_data
            provenance["ppiflow"][f"{score_key}_json"] = str(json_path)

        filter_json = json_path if record_kind == "maturation_filter" else json_path.with_name(
            json_path.name.replace("_maturation_score.json", "_maturation_filter.json").replace("_partial_flow_score.json", "_maturation_filter.json")
        )
        if filter_json.exists():
            filter_payload = _load_json_payload(filter_json)
            if filter_payload:
                provenance["ppiflow"]["maturation_filter"] = filter_payload
                provenance["ppiflow"]["maturation_filter_json"] = str(filter_json)
                filter_score_data = filter_payload.get("score_data") if isinstance(filter_payload, dict) else None
                if isinstance(filter_score_data, dict):
                    provenance["ppiflow"]["maturation_score"] = filter_score_data
                filter_score_json = filter_payload.get("score_json") if isinstance(filter_payload, dict) else None
                if filter_score_json:
                    provenance["ppiflow"]["maturation_score_json"] = str(filter_score_json)

        sidecar_candidates = _ordered_unique([design.name] + _candidate_source_design_names(design.name))
        for candidate_name in sidecar_candidates:
            anchors_json = json_path.with_name(f"{candidate_name}_anchors.json")
            anchors_payload = _load_json_payload(anchors_json)
            if anchors_payload:
                provenance["ppiflow"]["anchors"] = anchors_payload
                provenance["ppiflow"]["anchors_json"] = str(anchors_json)
                break
        for candidate_name in sidecar_candidates:
            interface_json = json_path.with_name(f"{candidate_name}_interface_score.json")
            interface_payload = _load_json_payload(interface_json)
            if interface_payload:
                provenance["ppiflow"]["interface_score"] = interface_payload
                provenance["ppiflow"]["interface_score_json"] = str(interface_json)
                break
        for candidate_name in sidecar_candidates:
            rotamer_json = json_path.with_name(f"{candidate_name}_rotamer_enrichment.json")
            rotamer_payload = _load_json_payload(rotamer_json)
            if rotamer_payload:
                provenance["ppiflow"]["rotamer_enrichment"] = rotamer_payload
                provenance["ppiflow"]["rotamer_enrichment_json"] = str(rotamer_json)
                break
        for candidate_name in sidecar_candidates:
            enriched_pdb = json_path.with_name(f"{candidate_name}_enriched_complex.pdb")
            if enriched_pdb.exists():
                provenance["ppiflow"]["enriched_complex_pdb"] = str(enriched_pdb)
                break
        for candidate_name in sidecar_candidates:
            ppiflow_positions_path = json_path.with_name(f"{candidate_name}_ppiflow_positions.txt")
            if ppiflow_positions_path.exists():
                provenance["ppiflow"]["ppiflow_positions"] = ppiflow_positions_path.read_text().strip()
                provenance["ppiflow"]["ppiflow_positions_txt"] = str(ppiflow_positions_path)
                break
        for candidate_name in sidecar_candidates:
            cdr_positions_path = json_path.with_name(f"{candidate_name}_cdr_positions.txt")
            if cdr_positions_path.exists():
                provenance["ppiflow"]["cdr_positions"] = cdr_positions_path.read_text().strip()
                provenance["ppiflow"]["cdr_positions_txt"] = str(cdr_positions_path)
                break
        if fam_json_path:
            provenance["ppiflow"]["fampnn"] = fam_payload
            provenance["ppiflow"]["fampnn_json"] = str(fam_json_path)

        design.lineage_root_job_id = design.lineage_root_job_id or job_context.get("lineage_root_job_id")
        design.parent_design_id = design.parent_design_id or lineage["parent_design_id"]
        design.origin_design_id = design.origin_design_id or lineage["origin_design_id"]
        design.origin_job_id = design.origin_job_id or lineage["origin_job_id"] or job_context.get("origin_job_id")
        design.origin_backbone_design_id = design.origin_backbone_design_id or lineage["origin_backbone_design_id"]
        design.stage_family = design.stage_family or job_context.get("stage_family") or "ppiflow"
        design.stage_mode = design.stage_mode or job_context.get("stage_mode") or "maturation"
        design.selected_loop_scope = design.selected_loop_scope or job_context.get("selected_loop_scope")
        design.provenance = provenance
        flag_modified(design, "provenance")
        
        updated_count += 1
    
    if updated_count > 0:
        await session.commit()
        print(f"[Ingester] Updated {updated_count} designs with maturation metrics")
    
    return updated_count


async def ingest_published_maturation_structures(
    job_id: str,
    output_path: Path,
    session: AsyncSession,
    current_job: Optional[Job] = None,
) -> int:
    """
    Backfill completed maturation-child outputs directly from published PDBs.

    This keeps valid PPIFlow child results viewable even when richer CSV-based
    ingestion does not run.
    """
    result_dirs = [
        output_path / "run" / "ppiflow" / "results",
        output_path / "ppiflow" / "results",
    ]
    published_results_dir = next((path for path in result_dirs if path.exists()), None)
    if published_results_dir is None:
        return 0

    existing_names = set(
        (
            await session.execute(
                select(Design.name).where(Design.job_id == job_id)
            )
        ).scalars().all()
    )

    approved_names = {
        report.stem.replace("_maturation_filter", "")
        for report in published_results_dir.glob("*_maturation_filter.json")
    }

    structure_paths: list[Path] = []
    if approved_names:
        for name in sorted(approved_names):
            for ext in ("pdb", "cif", "mmcif"):
                candidate = published_results_dir / f"{name}.{ext}"
                if candidate.exists():
                    structure_paths.append(candidate)
                    break
    else:
        for ext in ("*.pdb", "*.cif", "*.mmcif"):
            structure_paths.extend(sorted(published_results_dir.glob(ext)))

    created = 0
    job_context = _job_stage_context(current_job)
    lineage_cache: Dict[str, Optional[Design]] = {}
    for structure_path in structure_paths:
        design_name = structure_path.stem
        if design_name in existing_names:
            continue

        fam_json_path = _find_fampnn_sidecar_path(structure_path, output_path)
        fam_payload = _load_json_payload(fam_json_path)
        fam_metrics = _extract_fampnn_metrics(fam_payload)
        lineage = await _resolve_parent_design_lineage(
            session,
            job_context,
            design_name,
            cache=lineage_cache,
        )
        structure_cdr_lengths = _coalesce_cdr_lengths(
            _parse_hlt_cdr_lengths(structure_path),
            _parse_source_cdr_lengths(lineage.get("source_pdb_path")),
            lineage.get("source_cdr_lengths"),
        )
        sample_index = _parse_ppiflow_sample_index(design_name)
        ppiflow_provenance = {
            "source": "published_maturation_structures",
            "structure_path": str(structure_path),
        }
        if sample_index is not None:
            ppiflow_provenance["sample_index"] = sample_index
        if lineage.get("source_design_name"):
            ppiflow_provenance["source_design_name"] = lineage["source_design_name"]
        if lineage.get("source_pdb_path"):
            ppiflow_provenance["source_pdb_path"] = lineage["source_pdb_path"]
        filter_json = structure_path.with_name(f"{design_name}_maturation_filter.json")
        filter_payload = _load_json_payload(filter_json)
        if filter_payload:
            ppiflow_provenance["maturation_filter"] = filter_payload
            ppiflow_provenance["maturation_filter_json"] = str(filter_json)
        rotamer_json = structure_path.with_name(f"{design_name.split('_ppiflow_sample', 1)[0]}_rotamer_enrichment.json")
        rotamer_payload = _load_json_payload(rotamer_json)
        if rotamer_payload:
            ppiflow_provenance["rotamer_enrichment"] = rotamer_payload
            ppiflow_provenance["rotamer_enrichment_json"] = str(rotamer_json)
        enriched_pdb = structure_path.with_name(f"{design_name.split('_ppiflow_sample', 1)[0]}_enriched_complex.pdb")
        if enriched_pdb.exists():
            ppiflow_provenance["enriched_complex_pdb"] = str(enriched_pdb)
        ppiflow_positions_path = structure_path.with_name(f"{design_name.split('_ppiflow_sample', 1)[0]}_ppiflow_positions.txt")
        if ppiflow_positions_path.exists():
            ppiflow_provenance["ppiflow_positions"] = ppiflow_positions_path.read_text().strip()
            ppiflow_provenance["ppiflow_positions_txt"] = str(ppiflow_positions_path)
        cdr_positions_path = structure_path.with_name(f"{design_name.split('_ppiflow_sample', 1)[0]}_cdr_positions.txt")
        if cdr_positions_path.exists():
            ppiflow_provenance["cdr_positions"] = cdr_positions_path.read_text().strip()
            ppiflow_provenance["cdr_positions_txt"] = str(cdr_positions_path)
        confidence_metrics: Dict[str, Any] = {}
        if fam_payload:
            ppiflow_provenance["fampnn"] = fam_payload
            confidence_metrics["fampnn"] = fam_payload

        session.add(Design(
            id=str(uuid.uuid4()),
            job_id=job_id,
            name=design_name,
            pdb_path=str(structure_path),
            json_path=str(fam_json_path) if fam_json_path else None,
            backbone_id=parse_backbone_id(design_name),
            lineage_root_job_id=job_context.get("lineage_root_job_id"),
            parent_design_id=lineage["parent_design_id"],
            origin_design_id=lineage["origin_design_id"],
            origin_job_id=lineage["origin_job_id"] or job_context.get("origin_job_id"),
            origin_backbone_design_id=lineage["origin_backbone_design_id"],
            stage_family=job_context.get("stage_family") or "ppiflow",
            stage_mode=job_context.get("stage_mode") or "maturation",
            selected_loop_scope=job_context.get("selected_loop_scope"),
            provenance={
                **job_context.get("provenance", {}),
                "artifact_group": "ppiflow",
                "ppiflow": ppiflow_provenance,
            },
            plddt_overall=None,
            residue_plddt=None,
            mpnn_score=fam_metrics.get("mpnn_score"),
            fampnn_psce=fam_metrics.get("avg_psce"),
            binder_length=fam_metrics.get("binder_length"),
            confidence_metrics=confidence_metrics or None,
            cdr_h1_length=structure_cdr_lengths.get("H1"),
            cdr_h2_length=structure_cdr_lengths.get("H2"),
            cdr_h3_length=structure_cdr_lengths.get("H3"),
            cdr_l1_length=structure_cdr_lengths.get("L1"),
            cdr_l2_length=structure_cdr_lengths.get("L2"),
            cdr_l3_length=structure_cdr_lengths.get("L3"),
            is_favorite=False,
            created_at=datetime.utcnow(),
        ))
        existing_names.add(design_name)
        created += 1

    if created > 0:
        await session.commit()
        print(f"[Ingester] Backfilled {created} published maturation structures for job {job_id}")

    return created


async def ingest_frustration_data(
    job_id: str,
    output_path: Path,
    session: AsyncSession
) -> int:
    """
    Parse FrustraMPNN output CSVs and update matching designs with frustration data.
    
    FrustraMPNN outputs are in {output_dir}/frustration/{design_name}_frustration.csv
    """
    frustration_dir = output_path / "frustration"
    if not frustration_dir.exists():
        print(f"[Ingester] No frustration directory found at {frustration_dir}")
        return 0
    
    # Find all frustration CSVs
    frustration_csvs = list(frustration_dir.glob("*_frustration.csv"))
    if not frustration_csvs:
        print(f"[Ingester] No frustration CSV files found in {frustration_dir}")
        return 0
    
    print(f"[Ingester] Found {len(frustration_csvs)} frustration CSVs to process")
    
    from database import Job
    import sqlalchemy as sa
    
    job_info = await session.execute(
        select(Job).where(Job.id == job_id)
    )
    current_job = job_info.scalar_one_or_none()
    
    design_job_ids = [job_id]
    if current_job:
        if current_job.parent_job_id:
            design_job_ids.append(current_job.parent_job_id)
        if current_job.batch_id:
            batch_res = await session.execute(
                select(Job.id).where(Job.batch_id == current_job.batch_id)
            )
            design_job_ids.extend([row[0] for row in batch_res.all()])
        
        child_result = await session.execute(
            select(Job.id).where(Job.parent_job_id == job_id)
        )
        design_job_ids.extend([row[0] for row in child_result.all()])

        # Check for iteration Source ID stored in params
        params_dict = _parse_job_params(current_job.params)
        if params_dict.get("iteration_source_job_id"):
            design_job_ids.append(params_dict["iteration_source_job_id"])
        if params_dict.get("iteration_source_root_job_id"):
            design_job_ids.append(params_dict["iteration_source_root_job_id"])

    design_job_ids = list(set(design_job_ids))

    async def find_matching_design(design_token: str) -> Optional[Design]:
        normalized = _normalize_frustration_target_name(design_token)
        if not normalized:
            return None

        candidate_names = [normalized]
        exact_result = await session.execute(
            select(Design).where(
                Design.job_id.in_(design_job_ids),
                Design.name.in_(candidate_names)
            )
        )
        design = exact_result.scalar_one_or_none()
        if design:
            return design

        prefix_result = await session.execute(
            select(Design).where(
                Design.job_id.in_(design_job_ids),
                Design.name.like(f"{normalized}%")
            )
        )
        design = prefix_result.scalars().first()
        if design:
            return design

        pdb_result = await session.execute(
            select(Design).where(
                Design.job_id.in_(design_job_ids),
                Design.pdb_path.like(f"%/{normalized}.pdb")
            )
        )
        return pdb_result.scalars().first()

    updated_count = 0
    
    for csv_path in frustration_csvs:
        csv_targets = extract_frustration_targets(csv_path)
        target_names = csv_targets or [csv_path.stem.replace("_frustration", "")]

        for target_name in target_names:
            design = await find_matching_design(target_name)
            if not design:
                print(f"[Ingester] No matching design for frustration target: {target_name}")
                continue

            frust_data = parse_frustration_csv(csv_path, pdb_name_filter=target_name if csv_targets else None)
            if not frust_data:
                print(f"[Ingester] Failed to parse frustration CSV: {csv_path} (target={target_name})")
                continue

            design.frustration_high_count = frust_data['high_count']
            design.frustration_min_count = frust_data['min_count']
            design.frustration_pct_high = frust_data['pct_high']
            design.frustration_residues = frust_data['residues']
            design.frustration_csv_path = str(csv_path)

            updated_count += 1
            print(
                f"[Ingester] Updated {design.name} with frustration data: "
                f"{frust_data['high_count']} high, {frust_data['min_count']} min"
            )
    
    if updated_count > 0:
        await session.commit()
        print(f"[Ingester] Updated {updated_count} designs with frustration data")
    
    return updated_count


async def ingest_loose_files(
    job_id: str,
    output_path: Path,
    session: AsyncSession,
    current_job: Optional[Job] = None,
) -> int:
    """Ingest designs from individual JSON/PDB files (fallback)."""
    
    job_params: Dict[str, Any] = {}
    job_result = await session.execute(select(Job.mode, Job.params).where(Job.id == job_id))
    job_row = job_result.one_or_none()
    job_mode = job_row[0] if job_row else None
    raw_job_params = job_row[1] if job_row else None
    job_params = _parse_job_params(raw_job_params)
    is_maturation_child = str(job_mode or "").strip().lower() == "maturation_child"
    job_context = _job_stage_context(current_job)
    lineage_cache: Dict[str, Optional[Design]] = {}
    allow_binder_target_metrics = _job_has_explicit_binder_target_roles(current_job)
    validation_role_fields = _validation_role_fields(current_job, job_params)
    detected_antibody_chains = validation_role_fields.get("detected_antibody_chains")
    detected_target_chain = validation_role_fields.get("detected_target_chain")

    epitope_residues = _parse_epitope_residues(
        job_params.get("epitope_residues") or job_params.get("selected_residues")
    )
    
    # Locations to search for confidence/metrics JSONs
    # Boltz outputs often in pdb_files/predictions/
    # RF3 outputs in pdb_files/rf3/output/*/
    search_paths = [
        output_path / "pdb_files" / "predictions",
        output_path / "pdb_files" / "validated_designs",
        output_path / "pdb_files",
        output_path / "collected",
        output_path,
    ]
    
    # Also search RF3 nested output directories
    rf3_base = output_path / "pdb_files" / "rf3" / "output"
    if rf3_base.exists():
        for subdir in rf3_base.iterdir():
            if subdir.is_dir():
                search_paths.append(subdir)
                # Also search seed-*/sample-* subdirs
                for sample_dir in subdir.glob("seed-*_sample-*"):
                    if sample_dir.is_dir():
                        search_paths.append(sample_dir)

    # Protenix outputs: predictions/{design_name}/ containing .cif + confidence.json
    protenix_base = output_path / "pdb_files" / "predictions"
    if not protenix_base.exists():
        protenix_base = output_path / "run" / "protenix" / "predictions"
    protenix_run_base = output_path / "run" / "protenix_complex" / "predictions"
    for pbase in [protenix_base, protenix_run_base]:
        if pbase.exists():
            for subdir in pbase.iterdir():
                if subdir.is_dir():
                    search_paths.append(subdir)
    
    designs_created = 0
    
    # Track ingested names to avoid duplicates
    ingested_names = set()
    
    print(f"[Ingester DEBUG] Search paths: {[str(p) for p in search_paths]}")

    for search_dir in search_paths:
        if not search_dir.exists():
            continue
        
        recursive_scan = search_dir.name in {"pdb_files", "validated_designs", "collected"} or "collected" in search_dir.parts
        json_files = set(list(search_dir.rglob("confidence_*.json")) if recursive_scan else list(search_dir.glob("confidence_*.json")))
        boltz_aligned_jsons = list(search_dir.rglob("*_boltzpred.json")) if recursive_scan else list(search_dir.glob("*_boltzpred.json"))
        json_files.update(boltz_aligned_jsons)
        json_files = sorted(json_files)
        print(f"[Ingester DEBUG] {search_dir}: {len(json_files)} confidence JSONs found")
        
        # BOLTZ2: Look for confidence_*.json patterns
        for json_file in json_files:
            try:
                raw_name = json_file.stem
                if raw_name.endswith("_boltzpred"):
                    design_name = raw_name.replace("_boltzpred", "")
                else:
                    design_name = raw_name.replace("confidence_", "")
                
                # Skip input templates (no numeric suffix) - these are not actual designs
                # Actual designs are named like: boltzgen_input_0, boltzgen_input_1, etc.
                import re
                if not re.search(r'_\d+$', design_name):
                    print(f"[Ingester] Skipping input template: {design_name}")
                    continue
                
                if design_name in ingested_names:
                    continue
                
                # Look for corresponding Structure (CIF preferred for complexes, PDB fallback)
                structure_candidates = [
                    search_dir / f"{design_name}.cif",
                    output_path / "pdb_files" / f"{design_name}.cif",
                    search_dir / f"{raw_name}.pdb",
                    search_dir / f"{design_name}_boltzpred.pdb",
                    search_dir / f"{design_name}.pdb",
                    output_path / "pdb_files" / f"{raw_name}.pdb",
                    output_path / "pdb_files" / f"{design_name}_boltzpred.pdb",
                    output_path / "pdb_files" / f"{design_name}.pdb",
                    output_path / "pdb_files" / "predictions" / f"{raw_name}.pdb",
                    output_path / "pdb_files" / "predictions" / f"{design_name}_boltzpred.pdb",
                    output_path / "pdb_files" / "predictions" / f"{design_name}.pdb",
                ]
                structure_path = next((candidate for candidate in structure_candidates if candidate.exists()), None)

                if structure_path is None:
                    continue
                    
                # Read Boltz2 metrics
                with open(json_file, 'r') as f:
                    metrics = json.load(f)
                
                # Boltz2 format: complex_plddt, ptm, iptm, confidence_score, complex_pde
                plddt = metrics.get('complex_plddt') or metrics.get('plddt')
                if plddt is not None and plddt <= 1.0:
                    plddt = plddt * 100.0
                
                conf_score = metrics.get('confidence_score')
                ptm = metrics.get('ptm')
                ligand_iptm = metrics.get('ligand_iptm')
                
                # NEW: Extract interface metrics
                iptm = metrics.get('iptm')
                protein_iptm = metrics.get('protein_iptm')
                complex_iplddt = metrics.get('complex_iplddt')
                complex_ipde = metrics.get('complex_ipde')
                chains_ptm = metrics.get('chains_ptm')  # dict: {"0": 0.76, "1": 0.51}
                pair_chains_iptm = metrics.get('pair_chains_iptm')  # NxN matrix
                has_clash_raw = metrics.get('full_has_clash')
                if has_clash_raw is None:
                    has_clash_raw = metrics.get('has_clash')
                disorder = metrics.get('disorder') or metrics.get('full_disorder_prob_mean')
                num_recycles = metrics.get('num_recycles')
                rmsd_overall = metrics.get('rmsd_overall') or metrics.get('boltz_overall_rmsd')
                rmsd_binder = metrics.get('rmsd_binder') or metrics.get('boltz_binder_rmsd')
                rmsd_target = metrics.get('rmsd_target') or metrics.get('protenix_target_rmsd') or metrics.get('boltz_target_rmsd')

                # Only store true PAE values. PDE/IPDE-derived summaries are not interchangeable.
                pae = metrics.get('complex_pae') or metrics.get('pae')
                
                # Look for Affinity JSON
                affinity_score = None
                binder_probability = None
                
                # Try multiple locations for affinity file
                affinity_file = search_dir / f"affinity_{design_name}.json"
                if not affinity_file.exists():
                    affinity_file = output_path / "pdb_files" / "predictions" / f"affinity_{design_name}.json"
                    
                if affinity_file.exists():
                    try:
                        with open(affinity_file, 'r') as af:
                            aff_metrics = json.load(af)
                            affinity_score = aff_metrics.get('affinity_pred_value')
                            binder_probability = aff_metrics.get('affinity_probability_binary')
                    except Exception as e:
                        print(f"[Ingester] Error parsing affinity file {affinity_file}: {e}")

                # Extract per-residue pLDDT from PDB B-factors
                _, residue_plddt = extract_plddt_from_pdb(structure_path)
                structure_cdr_lengths = _parse_hlt_cdr_lengths(Path(structure_path))
                
                # Calculate epitope contacts if epitope_residues provided
                epitope_contact_count = None
                epitope_min_distance = None
                if epitope_residues and structure_path:
                    epitope_contact_count, epitope_min_distance = calculate_epitope_contacts(
                        structure_path, 
                        epitope_residues,
                        antibody_chain="A",  # RFantibody outputs antibody as chain A
                        target_chain="B"     # Target as chain B
                    )
                
                # Create design
                lineage = await _resolve_parent_design_lineage(
                    session,
                    job_context,
                    design_name,
                    cache=lineage_cache,
                )
                fam_json_path = _find_fampnn_sidecar_path(Path(structure_path), output_path)
                fam_payload = _load_json_payload(fam_json_path)
                fam_provenance: Dict[str, Any] = {
                    "source": "fampnn" if fam_payload and "fampnn_avg_psce" in fam_payload else "loose_file",
                    "structure_path": str(structure_path),
                }
                if lineage.get("source_design_name"):
                    fam_provenance["source_design_name"] = lineage["source_design_name"]
                if lineage.get("source_pdb_path"):
                    fam_provenance["source_pdb_path"] = lineage["source_pdb_path"]
                if fam_payload:
                    fam_provenance["fampnn"] = fam_payload
                combined_confidence = dict(metrics) if isinstance(metrics, dict) else {}
                if fam_payload:
                    combined_confidence["fampnn"] = fam_payload
                aligned_error_fields = _strict_aligned_error_fields(
                    structure_path=Path(structure_path),
                    summary_json_path=Path(json_file) if json_file else None,
                    detected_antibody_chains=detected_antibody_chains,
                    detected_target_chain=detected_target_chain,
                )

                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(structure_path),
                    json_path=str(json_file) if json_file else (str(fam_json_path) if fam_json_path.exists() else None),
                    
                    # Backbone grouping
                    backbone_id=parse_backbone_id(design_name),
                    lineage_root_job_id=job_context.get("lineage_root_job_id"),
                    parent_design_id=lineage["parent_design_id"],
                    origin_design_id=lineage["origin_design_id"],
                    origin_job_id=lineage["origin_job_id"] or job_context.get("origin_job_id"),
                    origin_backbone_design_id=lineage["origin_backbone_design_id"],
                    stage_family=job_context.get("stage_family"),
                    stage_mode=job_context.get("stage_mode"),
                    selected_loop_scope=job_context.get("selected_loop_scope"),
                    provenance={
                        **job_context.get("provenance", {}),
                        **fam_provenance,
                    },
                    confidence_metrics=combined_confidence or None,
                    detected_antibody_chains=detected_antibody_chains,
                    detected_target_chain=detected_target_chain,
                    **aligned_error_fields,
                    
                    # Epitope contact metrics
                    epitope_contact_count=epitope_contact_count,
                    epitope_min_distance=epitope_min_distance,
                    
                    # Metrics
                    plddt_overall=safe_float(plddt),
                    pae_overall=safe_float(pae),
                    ptm=safe_float(ptm),
                    conf_score=safe_float(conf_score),
                    ligand_iptm=safe_float(ligand_iptm),
                    rmsd_overall=safe_float(rmsd_overall),
                    rmsd_binder=safe_float(rmsd_binder),
                    rmsd_target=safe_float(rmsd_target),
                    affinity_score=safe_float(affinity_score),
                    binder_probability=safe_float(binder_probability),
                    residue_plddt=residue_plddt,
                    
                    # NEW: Interface metrics
                    iptm=safe_float(iptm),
                    protein_iptm=safe_float(protein_iptm),
                    complex_iplddt=safe_float(complex_iplddt),
                    complex_ipde=safe_float(complex_ipde),
                    chains_ptm=chains_ptm,
                    pair_chains_iptm=pair_chains_iptm,
                    disorder=safe_float(disorder),
                    num_recycles=safe_int(num_recycles),
                    has_clash=(bool(has_clash_raw) if has_clash_raw is not None else None),
                    cdr_h1_length=structure_cdr_lengths.get("H1"),
                    cdr_h2_length=structure_cdr_lengths.get("H2"),
                    cdr_h3_length=structure_cdr_lengths.get("H3"),
                    cdr_l1_length=structure_cdr_lengths.get("L1"),
                    cdr_l2_length=structure_cdr_lengths.get("L2"),
                    cdr_l3_length=structure_cdr_lengths.get("L3"),
                    
                    # Defaults for others
                    is_favorite=False,
                    created_at=datetime.utcnow()
                )
                
                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)
                
            except Exception as e:
                print(f"[Ingester] Error parsing Boltz2 file {json_file}: {e}")
        
        # RF3: Look for *_summary_confidences.json patterns
        rf3_jsons = list(search_dir.rglob("*_summary_confidences.json")) if recursive_scan else list(search_dir.glob("*_summary_confidences.json"))
        for json_file in rf3_jsons:
            try:
                # Filename format: DESIGNNAME_summary_confidences.json
                design_name = json_file.stem.replace("_summary_confidences", "")
                
                if design_name in ingested_names:
                    continue
                
                # Look for corresponding structure file (RF3 outputs .cif not .pdb)
                # Try CIF first (RF3 default), then PDB as fallback
                structure_path = None
                
                # Check for CIF with _model suffix
                cif_path = search_dir / f"{design_name}_model.cif"
                if not cif_path.exists():
                    cif_path = search_dir / f"{design_name}.cif"
                if not cif_path.exists():
                    cif_path = search_dir.parent / f"{design_name}_model.cif"
                if not cif_path.exists():
                    cif_path = search_dir.parent / f"{design_name}.cif"
                
                if cif_path.exists():
                    structure_path = cif_path
                else:
                    # Fallback to PDB
                    pdb_path = search_dir / f"{design_name}.pdb"
                    if not pdb_path.exists():
                        pdb_path = search_dir.parent / f"{design_name}.pdb"
                    if not pdb_path.exists():
                        pdb_path = search_dir.parent.parent / f"{design_name}.pdb"
                    if not pdb_path.exists():
                        # Try without seed/sample suffix
                        base_name = design_name.rsplit("_seed-", 1)[0]
                        pdb_path = output_path / "pdb_files" / f"{base_name}.pdb"
                    if pdb_path.exists():
                        structure_path = pdb_path
                
                if not structure_path:
                    print(f"[Ingester] No structure file found for RF3 design {design_name}")
                    continue
                    
                # Read RF3 metrics
                with open(json_file, 'r') as f:
                    metrics = json.load(f)
                
                # RF3 format: overall_plddt, ptm, iptm, overall_pae, ranking_score
                plddt = metrics.get('overall_plddt')
                if plddt is not None and plddt <= 1.0:
                    plddt = plddt * 100.0
                
                pae = metrics.get('overall_pae')
                ptm = metrics.get('ptm')
                iptm = metrics.get('iptm')
                ranking_score = metrics.get('ranking_score')
                
                # Use ranking_score as confidence if available
                conf_score = ranking_score if ranking_score is not None else None
                
                # Extract per-residue pLDDT from structure B-factors (works for both PDB and CIF via Biotite)
                from .structure_utils import get_residue_plddt
                _, residue_plddt = get_residue_plddt(structure_path)
                structure_cdr_lengths = _parse_hlt_cdr_lengths(Path(structure_path))
                lineage = await _resolve_parent_design_lineage(
                    session,
                    job_context,
                    design_name,
                    cache=lineage_cache,
                )
                aligned_error_fields = _strict_aligned_error_fields(
                    structure_path=Path(structure_path),
                    summary_json_path=Path(json_file),
                    detected_antibody_chains=detected_antibody_chains,
                    detected_target_chain=detected_target_chain,
                )
                
                # Create design
                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(structure_path),  # Can be .cif or .pdb
                    json_path=str(json_file),
                    
                    # Backbone grouping
                    backbone_id=parse_backbone_id(design_name),
                    lineage_root_job_id=job_context.get("lineage_root_job_id"),
                    parent_design_id=lineage["parent_design_id"],
                    origin_design_id=lineage["origin_design_id"],
                    origin_job_id=lineage["origin_job_id"] or job_context.get("origin_job_id"),
                    origin_backbone_design_id=lineage["origin_backbone_design_id"],
                    stage_family=job_context.get("stage_family"),
                    stage_mode=job_context.get("stage_mode"),
                    selected_loop_scope=job_context.get("selected_loop_scope"),
                    provenance=job_context.get("provenance", {}),
                    
                    # Metrics
                    plddt_overall=safe_float(plddt),
                    pae_overall=safe_float(pae),
                    ptm=safe_float(ptm),
                    iptm=safe_float(iptm),  # NEW: Store RF3 iptm
                    conf_score=safe_float(conf_score),
                    residue_plddt=residue_plddt,
                    confidence_metrics=metrics,
                    detected_antibody_chains=detected_antibody_chains,
                    detected_target_chain=detected_target_chain,
                    cdr_h1_length=structure_cdr_lengths.get("H1"),
                    cdr_h2_length=structure_cdr_lengths.get("H2"),
                    cdr_h3_length=structure_cdr_lengths.get("H3"),
                    cdr_l1_length=structure_cdr_lengths.get("L1"),
                    cdr_l2_length=structure_cdr_lengths.get("L2"),
                    cdr_l3_length=structure_cdr_lengths.get("L3"),
                    **aligned_error_fields,
                    
                    # Defaults for others
                    is_favorite=False,
                    created_at=datetime.utcnow()
                )
                
                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)
                
            except Exception as e:
                print(f"[Ingester] Error parsing RF3 file {json_file}: {e}")

        # PROTENIX: Current CLI emits *_summary_confidence_sample_*.json alongside
        # *_sample_*.cif in predictions/. Keep legacy confidence.json parsing too.
        protenix_jsons = set()
        if search_dir.name in {"pdb_files", "predictions", "validated_designs", "collected"}:
            for json_path in search_dir.rglob("*_summary_confidence_sample_*.json"):
                protenix_jsons.add(json_path)
            for sub in search_dir.iterdir():
                if sub.is_dir():
                    conf_json = sub / "confidence.json"
                    if conf_json.exists():
                        protenix_jsons.add(conf_json)
        else:
            for json_path in search_dir.glob("*_summary_confidence_sample_*.json"):
                protenix_jsons.add(json_path)
            conf_json = search_dir / "confidence.json"
            if conf_json.exists():
                protenix_jsons.add(conf_json)

        for json_file in sorted(protenix_jsons):
            try:
                structure_path = None
                design_name = json_file.parent.name
                stem = json_file.stem

                # New format:
                #   <name>_summary_confidence_sample_<rank>.json
                #   <name>_sample_<rank>.cif
                if "_summary_confidence_sample_" in stem:
                    base_name, sample_rank = stem.rsplit("_summary_confidence_sample_", 1)
                    design_name = f"{base_name}_sample_{sample_rank}"
                    candidate = json_file.with_name(f"{design_name}.cif")
                    if not candidate.exists():
                        candidate = json_file.with_name(f"{design_name}.pdb")
                    if candidate.exists():
                        structure_path = candidate

                if design_name in ingested_names:
                    continue

                # Legacy format: confidence.json in per-sample subdir
                if structure_path is None:
                    cif_files = list(json_file.parent.glob("*.cif"))
                    if cif_files:
                        structure_path = cif_files[0]
                    else:
                        pdb_files = list(json_file.parent.glob("*.pdb"))
                        if not pdb_files:
                            print(f"[Ingester] No CIF/PDB found for Protenix design {design_name}")
                            continue
                        structure_path = pdb_files[0]

                # Read Protenix confidence metrics
                with open(json_file, 'r') as f:
                    metrics = json.load(f)

                # Protenix confidence keys vary across releases:
                #   current: plddt/ptm/iptm/gpde/chain_*/*_iptm/ranking_score/has_clash
                #   legacy: complex_plddt/complex_pde/...
                plddt = (
                    metrics.get('full_plddt')
                    or metrics.get('complex_plddt')
                    or metrics.get('plddt')
                )
                if plddt is not None and plddt <= 1.0:
                    plddt = plddt * 100.0

                conf_score = metrics.get('ranking_score') or metrics.get('confidence_score')
                ptm = metrics.get('full_ptm') or metrics.get('ptm')
                iptm = metrics.get('full_iptm') or metrics.get('iptm')
                protein_iptm = metrics.get('protein_iptm')
                ligand_iptm = metrics.get('ligand_iptm')
                complex_iplddt = metrics.get('complex_iplddt')
                complex_ipde = (
                    metrics.get('complex_ipde')
                    or metrics.get('gpde')
                    or metrics.get('complex_pde')
                )
                chains_ptm = metrics.get('chain_ptm') or metrics.get('chains_ptm')
                pair_chains_iptm = metrics.get('chain_pair_iptm') or metrics.get('pair_chains_iptm')
                chain_plddt = metrics.get('chain_plddt')
                has_clash = metrics.get('full_has_clash')
                if has_clash is None:
                    has_clash = metrics.get('has_clash')
                disorder = metrics.get('disorder')
                if disorder is None:
                    disorder = metrics.get('full_disorder_prob_mean')
                num_recycles = metrics.get('num_recycles')
                rmsd_overall = metrics.get('rmsd_overall') or metrics.get('protenix_overall_rmsd')
                rmsd_binder = metrics.get('rmsd_binder') or metrics.get('protenix_binder_rmsd')
                rmsd_target = metrics.get('rmsd_target') or metrics.get('protenix_target_rmsd') or metrics.get('boltz_target_rmsd')

                # Only store true PAE values. gpde/PDE are different metrics and must not be
                # backfilled into the PAE column if we want strict downstream interface scoring.
                pae = metrics.get('complex_pae') or metrics.get('pae')

                # Extract per-residue pLDDT from CIF B-factors
                _, residue_plddt = extract_plddt_from_pdb(structure_path)
                structure_cdr_lengths = _parse_hlt_cdr_lengths(Path(structure_path))

                plddt_binder = None
                plddt_target = None
                if allow_binder_target_metrics and isinstance(chain_plddt, list) and len(chain_plddt) >= 2:
                    plddt_binder = chain_plddt[0]
                    plddt_target = chain_plddt[1]
                    if plddt_binder is not None and plddt_binder <= 1.0:
                        plddt_binder *= 100.0
                    if plddt_target is not None and plddt_target <= 1.0:
                        plddt_target *= 100.0

                epitope_contact_count = None
                epitope_min_distance = None
                if epitope_residues and structure_path:
                    epitope_contact_count, epitope_min_distance = calculate_epitope_contacts(
                        structure_path,
                        epitope_residues,
                        antibody_chain="A",
                        target_chain="B",
                    )
                lineage = await _resolve_parent_design_lineage(
                    session,
                    job_context,
                    design_name,
                    cache=lineage_cache,
                )
                aligned_error_fields = _strict_aligned_error_fields(
                    structure_path=Path(structure_path),
                    summary_json_path=Path(json_file),
                    detected_antibody_chains=detected_antibody_chains,
                    detected_target_chain=detected_target_chain,
                )

                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(structure_path),
                    json_path=str(json_file),

                    backbone_id=parse_backbone_id(design_name),
                    lineage_root_job_id=job_context.get("lineage_root_job_id"),
                    parent_design_id=lineage["parent_design_id"],
                    origin_design_id=lineage["origin_design_id"],
                    origin_job_id=lineage["origin_job_id"] or job_context.get("origin_job_id"),
                    origin_backbone_design_id=lineage["origin_backbone_design_id"],
                    stage_family=job_context.get("stage_family"),
                    stage_mode=job_context.get("stage_mode"),
                    selected_loop_scope=job_context.get("selected_loop_scope"),
                    provenance=job_context.get("provenance", {}),
                    epitope_contact_count=epitope_contact_count,
                    epitope_min_distance=epitope_min_distance,
                    detected_antibody_chains=detected_antibody_chains,
                    detected_target_chain=detected_target_chain,

                    plddt_overall=safe_float(plddt),
                    plddt_binder=safe_float(plddt_binder),
                    plddt_target=safe_float(plddt_target),
                    pae_overall=safe_float(pae),
                    ptm=safe_float(ptm),
                    iptm=safe_float(iptm),
                    protein_iptm=safe_float(protein_iptm),
                    rmsd_overall=safe_float(rmsd_overall),
                    rmsd_binder=safe_float(rmsd_binder),
                    rmsd_target=safe_float(rmsd_target),
                    conf_score=safe_float(conf_score),
                    ligand_iptm=safe_float(ligand_iptm),
                    complex_iplddt=safe_float(complex_iplddt),
                    complex_ipde=safe_float(complex_ipde),
                    chains_ptm=chains_ptm,
                    pair_chains_iptm=pair_chains_iptm,
                    residue_plddt=residue_plddt,
                    cdr_h1_length=structure_cdr_lengths.get("H1"),
                    cdr_h2_length=structure_cdr_lengths.get("H2"),
                    cdr_h3_length=structure_cdr_lengths.get("H3"),
                    cdr_l1_length=structure_cdr_lengths.get("L1"),
                    cdr_l2_length=structure_cdr_lengths.get("L2"),
                    cdr_l3_length=structure_cdr_lengths.get("L3"),
                    disorder=safe_float(disorder),
                    num_recycles=safe_int(num_recycles),
                    has_clash=(bool(has_clash) if has_clash is not None else None),
                    confidence_metrics=metrics,
                    **aligned_error_fields,

                    is_favorite=False,
                    created_at=datetime.utcnow()
                )

                # Store clash info in notes if present
                if has_clash:
                    design.notes = 'steric_clash_detected'

                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)

            except Exception as e:
                print(f"[Ingester] Error parsing Protenix file {json_file}: {e}")
                
    # If still no designs, try just finding raw structures (e.g. valid job but missing metadata)
    if designs_created == 0:
        print("[Ingester] No JSON metrics found. Scanning for raw structure files...")

        # Determine job type for model-specific ingestion logic
        job_model_id = None
        try:
            job_result = await session.execute(select(Job.model_id).where(Job.id == job_id))
            job_model_id = job_result.scalar_one_or_none()
        except Exception:
            pass
        is_oligo = (job_model_id or "").lower() in ("oligo_design", "oligo_designer")

        # --- Oligo-specific ingestion ---
        # For oligo_design jobs: ONLY ingest from run/rebuilt/ (full-atom PDBs).
        # Do NOT fallback to rglob which grabs backbone PDBs from run/rfdpoly/ and run/nampnn/.
        if is_oligo:
            print("[Ingester] Oligo design job detected — using oligo-specific ingestion")
            structure_paths = []
            rebuilt_dir = output_path / "run" / "rebuilt"
            if rebuilt_dir.exists():
                structure_paths.extend(list(rebuilt_dir.glob("out_*.pdb")))
                # Also check nested rebuilt/rebuilt/ (older publishDir layout)
                nested = rebuilt_dir / "rebuilt"
                if nested.exists():
                    structure_paths.extend(list(nested.glob("out_*.pdb")))
                print(f"[Ingester] Found {len(structure_paths)} rebuilt PDBs in {rebuilt_dir}")
            
            if not structure_paths:
                print(f"[Ingester] No rebuilt PDBs found for oligo job under {rebuilt_dir}")

            # Parse NA-MPNN quality metrics from nampnn_metrics.json
            nampnn_design_metrics = {}
            for metrics_path in [
                output_path / "run" / "nampnn" / "nampnn_metrics.json",
                output_path / "run" / "rebuilt" / "rebuild_metrics.json",
            ]:
                if metrics_path.exists():
                    try:
                        with open(metrics_path) as f:
                            parsed = json.load(f)
                        # nampnn_metrics.json has a 'designs' list with per-design metrics
                        if "designs" in parsed:
                            for d in parsed["designs"]:
                                conf = d.get("overall_confidence")
                                rec = d.get("seq_rec")
                                header = d.get("header", "")
                                if conf is not None or rec is not None:
                                    nampnn_design_metrics[header] = {
                                        "overall_confidence": conf,
                                        "seq_rec": rec,
                                    }
                        # rebuild_metrics.json has 'nampnn_metrics' dict
                        if "nampnn_metrics" in parsed:
                            for key, metrics in parsed["nampnn_metrics"].items():
                                nampnn_design_metrics[key] = metrics
                        print(f"[Ingester] Parsed {len(nampnn_design_metrics)} design metrics from {metrics_path.name}")
                    except Exception as e:
                        print(f"[Ingester] Warning: could not parse {metrics_path}: {e}")

            for structure_path in structure_paths:
                design_name = structure_path.stem
                if design_name in ingested_names:
                    continue
                    
                # For oligo jobs: B-factors contain NA-MPNN design confidence (not pLDDT)
                # Extract them but label correctly
                bfactor_avg, residue_bfactors = extract_plddt_from_pdb(structure_path)
                
                # Look up NA-MPNN metrics for this design
                overall_confidence = None
                seq_rec = None
                for key, metrics in nampnn_design_metrics.items():
                    if design_name.replace("out_", "") in key or key in design_name:
                        overall_confidence = metrics.get("overall_confidence")
                        seq_rec = metrics.get("seq_rec")
                        break
                
                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(structure_path),
                    json_path=None,
                    
                    backbone_id=parse_backbone_id(design_name),
                    
                    # For oligo: B-factors are NA-MPNN design confidence, NOT pLDDT
                    # Store in plddt_overall for viewer compatibility but note it's design confidence
                    plddt_overall=bfactor_avg if bfactor_avg and bfactor_avg > 0 else None,
                    residue_plddt=residue_bfactors,
                    
                    # NA-MPNN quality metrics
                    conf_score=overall_confidence,  # overall_confidence from FASTA header
                    mpnn_score=seq_rec,  # sequence recovery from FASTA header
                    
                    is_favorite=False,
                    created_at=datetime.utcnow()
                )
                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)

        # --- Standard (non-oligo) ingestion ---
        else:
            structure_paths = []

            if is_maturation_child:
                published_results_dir = output_path / "run" / "ppiflow" / "results"
                if published_results_dir.exists():
                    # For maturation children, only ingest the published final outputs.
                    # Do not recurse into the whole directory tree because that can pull
                    # in intermediate PPIFlow/redesign artifacts and misrepresent them
                    # as final matured designs.
                    approved_names = {
                        report.stem.replace("_maturation_filter", "")
                        for report in published_results_dir.glob("*_maturation_filter.json")
                    }
                    if approved_names:
                        for name in sorted(approved_names):
                            for ext in ("pdb", "cif", "mmcif"):
                                candidate = published_results_dir / f"{name}.{ext}"
                                if candidate.exists():
                                    structure_paths.append(candidate)
                                    break
                    else:
                        structure_paths.extend(sorted(published_results_dir.glob("*.pdb")))
                        structure_paths.extend(sorted(published_results_dir.glob("*.cif")))
                        structure_paths.extend(sorted(published_results_dir.glob("*.mmcif")))
                if not structure_paths:
                    print(f"[Ingester] No published maturation result structures found under {output_path}")
                    return 0

            # For non-oligo jobs: prefer run/rebuilt/ over raw structures
            if not is_maturation_child:
                rebuilt_dir = output_path / "run" / "rebuilt"
                if rebuilt_dir.exists():
                    structure_paths.extend(list(rebuilt_dir.glob("*.pdb")))
                    nested = rebuilt_dir / "rebuilt"
                    if nested.exists():
                        structure_paths.extend(list(nested.glob("*.pdb")))
                    print(f"[Ingester] Found {len(structure_paths)} rebuilt PDBs in {rebuilt_dir}")

                if not structure_paths:
                    structure_paths.extend(list(output_path.rglob("*.pdb")))
                    structure_paths.extend(list(output_path.rglob("*.cif")))
                    structure_paths.extend(list(output_path.rglob("*.mmcif")))

            if not structure_paths:
                print(f"[Ingester] No raw structures found under {output_path}")

            for structure_path in structure_paths:
                design_name = structure_path.stem
                if design_name in ingested_names:
                    continue

                lineage = await _resolve_parent_design_lineage(
                    session,
                    job_context,
                    design_name,
                    cache=lineage_cache,
                )
                fam_json_path = _find_fampnn_sidecar_path(structure_path, output_path)
                fam_payload = _load_json_payload(fam_json_path)
                fam_metrics = _extract_fampnn_metrics(fam_payload)
                    
                # For raw RFantibody outputs, the meaningful confidence lives in the
                # .trb sidecar rather than the output PDB B-factors.
                rfa_trb = load_rfantibody_trb_summary(structure_path)
                if rfa_trb:
                    plddt = safe_float(rfa_trb.get("plddt_overall"))
                    residue_plddt = rfa_trb.get("residue_plddt")
                else:
                    plddt, residue_plddt = extract_plddt_from_pdb(structure_path)
                    if fam_payload and str(job_context.get("stage_family") or "").strip().lower() == "fampnn":
                        if plddt is not None and plddt <= 0:
                            plddt = None
                        if isinstance(residue_plddt, list) and not any(safe_float(value) and safe_float(value) > 0 for value in residue_plddt):
                            residue_plddt = None
                structure_cdr_lengths = _parse_hlt_cdr_lengths(Path(structure_path))
                epitope_contact_count = None
                epitope_min_distance = None
                if epitope_residues and structure_path:
                    epitope_contact_count, epitope_min_distance = calculate_epitope_contacts(
                        structure_path,
                        epitope_residues,
                        antibody_chain="A",
                        target_chain="B",
                    )

                design_provenance = {
                    **job_context.get("provenance", {}),
                    "structure_path": str(structure_path),
                }
                if lineage.get("source_design_name"):
                    design_provenance["source_design_name"] = lineage["source_design_name"]
                if lineage.get("source_pdb_path"):
                    design_provenance["source_pdb_path"] = lineage["source_pdb_path"]
                combined_confidence: Dict[str, Any] = {}
                if fam_payload:
                    design_provenance["fampnn"] = fam_payload
                    combined_confidence["fampnn"] = fam_payload
                if rfa_trb:
                    design_provenance["rfantibody"] = rfa_trb
                    combined_confidence["rfantibody"] = rfa_trb.get("rfa_metadata") or rfa_trb
                    
                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(structure_path),
                    json_path=str(fam_json_path) if fam_json_path.exists() else None,
                    
                    backbone_id=parse_backbone_id(design_name),
                    lineage_root_job_id=job_context.get("lineage_root_job_id"),
                    parent_design_id=lineage["parent_design_id"],
                    origin_design_id=lineage["origin_design_id"],
                    origin_job_id=lineage["origin_job_id"] or job_context.get("origin_job_id"),
                    origin_backbone_design_id=lineage["origin_backbone_design_id"],
                    stage_family=job_context.get("stage_family"),
                    stage_mode=job_context.get("stage_mode"),
                    selected_loop_scope=job_context.get("selected_loop_scope"),
                    provenance=design_provenance or None,
                    epitope_contact_count=epitope_contact_count,
                    epitope_min_distance=epitope_min_distance,
                    
                    plddt_overall=plddt,
                    residue_plddt=residue_plddt,
                    mpnn_score=fam_metrics.get("mpnn_score"),
                    fampnn_psce=fam_metrics.get("avg_psce"),
                    binder_length=fam_metrics.get("binder_length"),
                    rfa_hotspot_min_distance=safe_float(rfa_trb.get("rfa_hotspot_min_distance")),
                    rfa_hotspot_avg_min_distance=safe_float(rfa_trb.get("rfa_hotspot_avg_min_distance")),
                    rfa_runtime_seconds=safe_float(rfa_trb.get("rfa_runtime_seconds")),
                    rfa_device=rfa_trb.get("rfa_device"),
                    rfa_diffusion_steps=safe_int(rfa_trb.get("rfa_diffusion_steps")),
                    rfa_noise_scale_ca=safe_float(rfa_trb.get("rfa_noise_scale_ca")),
                    rfa_noise_scale_frame=safe_float(rfa_trb.get("rfa_noise_scale_frame")),
                    rfa_guide_scale=safe_float(rfa_trb.get("rfa_guide_scale")),
                    rfa_plddt_initial=safe_float(rfa_trb.get("rfa_plddt_initial")),
                    rfa_plddt_final=safe_float(rfa_trb.get("rfa_plddt_final")),
                    rfa_plddt_delta=safe_float(rfa_trb.get("rfa_plddt_delta")),
                    rfa_plddt_selected=safe_float(rfa_trb.get("rfa_plddt_selected")),
                    rfa_plddt_nonselected=safe_float(rfa_trb.get("rfa_plddt_nonselected")),
                    rfa_design_loops=rfa_trb.get("rfa_design_loops"),
                    rfa_hotspots=rfa_trb.get("rfa_hotspots"),
                    confidence_metrics=combined_confidence or None,
                    cdr_h1_length=structure_cdr_lengths.get("H1"),
                    cdr_h2_length=structure_cdr_lengths.get("H2"),
                    cdr_h3_length=structure_cdr_lengths.get("H3"),
                    cdr_l1_length=structure_cdr_lengths.get("L1"),
                    cdr_l2_length=structure_cdr_lengths.get("L2"),
                    cdr_l3_length=structure_cdr_lengths.get("L3"),
                    
                    is_favorite=False,
                    created_at=datetime.utcnow()
                )
                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)

    if designs_created > 0:
        try:
            await session.commit()
            print(f"[Ingester] Ingested {designs_created} designs from loose files for job {job_id}")
        except Exception as e:
            print(f"[Ingester] Error committing loose files: {e}")
            await session.rollback()
            return 0
            
    return designs_created


def extract_pdb_files(output_path: Path) -> Path:
    """
    Extract PDB files from tar.gz archives to a pdb_files directory.
    Returns the path to the directory containing extracted PDBs.
    """
    import tarfile
    
    pdb_dir = output_path / "pdb_files"

    if not output_path.exists():
        print(f"[Ingester] Output path missing, skipping extraction: {output_path}")
        return pdb_dir
    
    # Skip if already extracted
    if pdb_dir.exists() and any(pdb_dir.glob("*.pdb")):
        print(f"[Ingester] PDB files already extracted to {pdb_dir}")
        return pdb_dir
    
    pdb_dir.mkdir(exist_ok=True)
    
    # Look for result tar.gz files to extract
    tar_locations = [
        output_path / "run" / "af2" / "af2_results.tar.gz",
        output_path / "run" / "boltz" / "boltz_results.tar.gz", 
        output_path / "run" / "rf3" / "rf3_results.tar.gz",
        output_path / "results" / "best_designs.tar.gz",
    ]
    
    for tar_path in tar_locations:
        if tar_path.exists():
            try:
                with tarfile.open(tar_path, 'r:gz') as tar:
                    for member in tar.getmembers():
                        if member.name.endswith('.pdb'):
                            # Extract just the filename
                            member.name = Path(member.name).name
                            tar.extract(member, pdb_dir)
                            print(f"[Ingester] Extracted {member.name}")
            except Exception as e:
                print(f"[Ingester] Error extracting {tar_path}: {e}")
    
    pdb_count = len(list(pdb_dir.glob("*.pdb")))
    print(f"[Ingester] Extracted {pdb_count} PDB files to {pdb_dir}")
    return pdb_dir


def find_pdb_path(output_path: Path, design_name: str) -> str:
    """Find the PDB file path for a design."""
    # Check in pdb_files directory first (extracted from tar.gz)
    pdb_files = output_path / "pdb_files"
    if pdb_files.exists():
        pdb_file = pdb_files / f"{design_name}.pdb"
        if pdb_file.exists():
            return str(pdb_file)
        # Check validated designs subdir
        validated_dir = pdb_files / "validated_designs"
        if validated_dir.exists():
            pdb_file = validated_dir / f"{design_name}.pdb"
            if pdb_file.exists():
                return str(pdb_file)
    
    # Check in best_designs directory
    best_designs = output_path / "best_designs"
    if best_designs.exists():
        pdb_file = best_designs / f"{design_name}.pdb"
        if pdb_file.exists():
            return str(pdb_file)
    
    # Fallback - return expected path in pdb_files
    return str(output_path / "pdb_files" / f"{design_name}.pdb")


def safe_float(value) -> Optional[float]:
    """Safely convert to float, returning None on failure."""
    if value is None or value == '' or value == 'NA' or value == 'nan':
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def safe_int(value) -> Optional[int]:
    """Safely convert to int, returning None on failure."""
    if value is None or value == '' or value == 'NA' or value == 'nan':
        return None
    try:
        return int(float(value))  # Handle "3.0" -> 3
    except (ValueError, TypeError):
        return None


def extract_plddt_from_pdb(pdb_path):
    """
    Extract pLDDT from structure B-factors.
    Supports both PDB and CIF files via Biotite, with fallback to manual parsing.
    Returns (avg_plddt, per_residue_array).
    """
    path = Path(pdb_path) if not isinstance(pdb_path, Path) else pdb_path
    
    # Try Biotite first (handles PDB and CIF)
    try:
        from .structure_utils import get_residue_plddt
        avg_plddt, per_residue = get_residue_plddt(path)
        if avg_plddt is not None:
            return avg_plddt, per_residue
    except ImportError:
        pass  # Biotite not available, fall through to manual
    except Exception as e:
        print(f"[Ingester] Biotite extraction failed for {path}, trying manual: {e}")
    
    # Fallback: Manual PDB parsing (only works for .pdb files)
    if not str(path).lower().endswith('.pdb'):
        print(f"[Ingester] Cannot manually parse non-PDB file: {path}")
        return None, None
        
    try:
        residue_scores = []  # One score per residue (CA atom)
        all_scores = []  # All atom scores for average
        
        with open(path, 'r') as f:
            for line in f:
                if line.startswith("ATOM  ") or line.startswith("HETATM"):
                    # B-factor is columns 61-66 (1-indexed) -> 60-66 (0-indexed)
                    try:
                        bfactor = float(line[60:66].strip())
                        all_scores.append(bfactor)
                        
                        # Extract CA atoms only for per-residue (one per residue)
                        atom_name = line[12:16].strip()
                        if atom_name == "CA":
                            residue_scores.append(round(bfactor, 2))
                    except ValueError:
                        pass
        
        avg_plddt = sum(all_scores) / len(all_scores) if all_scores else None
        per_residue = residue_scores if residue_scores else None
        
        return avg_plddt, per_residue
    except Exception:
        return None, None


async def get_job_summary_metrics(output_dir: str) -> dict:
    """
    Read success_metrics.json for job summary stats.
    """
    metrics_path = Path(output_dir) / "results" / "success_metrics.json"
    
    if not metrics_path.exists():
        return {}
    
    try:
        with open(metrics_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"[Ingester] Error reading metrics: {e}")
        return {}
