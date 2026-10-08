#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

try:
    import pandas as pd
except Exception:  # pragma: no cover - runtime dependency inside container
    pd = None  # type: ignore[assignment]


def _sanitize_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(value or "").strip())
    return cleaned.strip("_") or "caliby"


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


_CALIBY_CHECKPOINTS = {
    "caliby": Path("caliby/caliby.ckpt"),
    "soluble_caliby": Path("caliby/soluble_caliby.ckpt"),
    "soluble_caliby_v1": Path("caliby/soluble_caliby_v1.ckpt"),
    "caliby_packer_000": Path("caliby/caliby_packer_000.ckpt"),
    "caliby_packer_010": Path("caliby/caliby_packer_010.ckpt"),
    "caliby_packer_030": Path("caliby/caliby_packer_030.ckpt"),
}


def resolve_expected_caliby_checkpoint(model_name: str, model_params_dir: Path) -> Path:
    normalized_name = str(model_name or "").strip()
    relative_path = _CALIBY_CHECKPOINTS.get(normalized_name)
    if relative_path is None:
        supported = ", ".join(sorted(_CALIBY_CHECKPOINTS))
        raise RuntimeError(f"Unknown Caliby checkpoint '{normalized_name}'. Supported: {supported}")
    return (model_params_dir / relative_path).resolve()


def _validate_cache_directory(env_name: str) -> None:
    raw_value = str(os.environ.get(env_name) or "").strip()
    if not raw_value:
        return
    cache_path = Path(raw_value).expanduser()
    if cache_path.exists() and not cache_path.is_dir():
        raise RuntimeError(f"{env_name} must point to a writable directory, not {cache_path}")
    existing_parent = cache_path
    while not existing_parent.exists() and existing_parent != existing_parent.parent:
        existing_parent = existing_parent.parent
    if not existing_parent.is_dir() or not os.access(existing_parent, os.W_OK):
        raise RuntimeError(f"{env_name} is not writable: {cache_path}")


def preflight_caliby_runtime(
    *,
    task: str,
    model_name: str,
    packer_model_name: str | None = None,
    allow_download: bool | None = None,
) -> dict[str, Any]:
    normalized_task = str(task or "").strip().lower()
    if normalized_task not in {"sequence_design", "ensemble_design", "sidechain_pack"}:
        raise RuntimeError(f"Unsupported Caliby task: {normalized_task}")

    raw_model_params_dir = str(os.environ.get("MODEL_PARAMS_DIR") or "").strip()
    if not raw_model_params_dir:
        raise RuntimeError("MODEL_PARAMS_DIR must be set before loading Caliby")
    model_params_dir = Path(raw_model_params_dir).expanduser().resolve()

    selected_model = packer_model_name if normalized_task == "sidechain_pack" else model_name
    checkpoint_path = resolve_expected_caliby_checkpoint(str(selected_model or ""), model_params_dir)
    downloads_allowed = (
        parse_bool(os.environ.get("CALIBY_ALLOW_DOWNLOAD", "0"))
        if allow_download is None
        else bool(allow_download)
    )

    for env_name in ("HF_HOME", "XDG_CACHE_HOME", "TRITON_CACHE_DIR"):
        _validate_cache_directory(env_name)

    if not checkpoint_path.is_file() and not downloads_allowed:
        raise RuntimeError(
            f"Caliby {normalized_task} checkpoint is missing: {checkpoint_path}. "
            "Install the selected checkpoint or explicitly set CALIBY_ALLOW_DOWNLOAD=1."
        )

    return {
        "task": normalized_task,
        "model_name": str(selected_model or ""),
        "model_params_dir": str(model_params_dir),
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_exists": checkpoint_path.is_file(),
        "allow_download": downloads_allowed,
    }


def parse_omit_aas(raw: str | None) -> list[str]:
    tokens = []
    for token in str(raw or "").replace(";", ",").split(","):
        aa = token.strip().upper()
        if not aa:
            continue
        if len(aa) != 1 or not aa.isalpha():
            raise ValueError(f"Invalid amino acid token in omit list: {token}")
        tokens.append(aa)
    return tokens


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def collect_structure_paths(directory: Path) -> list[str]:
    candidates = sorted(
        {
            *directory.glob("*.pdb"),
            *directory.glob("*.cif"),
            *directory.glob("*.mmcif"),
        }
    )
    return [str(path.resolve()) for path in candidates if path.is_file()]


def read_name_list(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def filter_structure_paths_by_name(paths: Iterable[str], allowed_names: set[str]) -> list[str]:
    if not allowed_names:
        return list(paths)
    kept: list[str] = []
    for raw in paths:
        path = Path(raw)
        if path.stem in allowed_names or path.name in allowed_names:
            kept.append(str(path))
    return kept


def build_conformer_mapping(conformer_dir: Path, allowed_names: set[str] | None = None) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    allowed = allowed_names or set()

    direct_dirs = sorted(path for path in conformer_dir.iterdir() if path.is_dir()) if conformer_dir.exists() else []
    for directory in direct_dirs:
        if allowed and directory.name not in allowed:
            continue
        conformers = collect_structure_paths(directory)
        if conformers:
            mapping[directory.name] = conformers

    if mapping:
        return mapping

    all_structures = collect_structure_paths(conformer_dir)
    by_prefix: dict[str, list[str]] = {}
    for raw in all_structures:
        path = Path(raw)
        stem = path.stem
        key = stem.split("_")[0]
        if allowed and key not in allowed and stem not in allowed:
            continue
        by_prefix.setdefault(key, []).append(str(path))

    return {
        key: sorted(paths)
        for key, paths in by_prefix.items()
        if paths
    }


def load_constraints_dataframe(csv_path: Path | None):
    if csv_path is None or not csv_path.exists() or csv_path.stat().st_size == 0:
        return None
    if pd is None:  # pragma: no cover - runtime dependency inside container
        raise RuntimeError("pandas is required to load Caliby positional constraints")
    return pd.read_csv(csv_path)


def maybe_clean_inputs(
    *,
    pdb_paths: list[str],
    cleaned_dir: Path,
    num_workers: int,
) -> list[str]:
    if not pdb_paths:
        return []
    from caliby import clean_pdbs  # pragma: no cover - runtime dependency

    cleaned_dir.mkdir(parents=True, exist_ok=True)
    return clean_pdbs(pdb_paths, out_dir=str(cleaned_dir), num_workers=max(1, num_workers))


def parse_chain_order(pdb_path: Path) -> list[str]:
    try:
        import gemmi  # pragma: no cover - runtime dependency

        structure = gemmi.read_structure(str(pdb_path))
        chain_ids: list[str] = []
        seen: set[str] = set()
        for model in structure:
            for chain in model:
                chain_id = str(chain.name or "").strip()
                if not chain_id or chain_id in seen:
                    continue
                seen.add(chain_id)
                chain_ids.append(chain_id)
            if chain_ids:
                break
        if chain_ids:
            return chain_ids
    except Exception:
        pass

    chain_ids = []
    seen: set[str] = set()
    with pdb_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.startswith(("ATOM", "HETATM")):
                continue
            chain_id = line[21:22].strip()
            if not chain_id or chain_id in seen:
                continue
            seen.add(chain_id)
            chain_ids.append(chain_id)
    return chain_ids


_FIXED_POS_TOKEN_RE = re.compile(r"^([A-Za-z0-9])(\d+(?:-\d+)?)$")


def remap_fixed_position_spec(spec: str | None, chain_map: dict[str, str]) -> str:
    tokens = []
    for raw_token in str(spec or "").split(","):
        token = raw_token.strip()
        if not token:
            continue
        match = _FIXED_POS_TOKEN_RE.match(token)
        if not match:
            tokens.append(token)
            continue
        chain_id, residue_span = match.groups()
        tokens.append(f"{chain_map.get(chain_id, chain_id)}{residue_span}")
    return ",".join(tokens)


_POSITION_TOKEN_RE = re.compile(r"([A-Za-z0-9])(\d+(?:-\d+)?)")


def remap_position_like_spec(spec: str | None, chain_map: dict[str, str]) -> str:
    text = str(spec or "").strip()
    if not text:
        return ""
    return _POSITION_TOKEN_RE.sub(lambda match: f"{chain_map.get(match.group(1), match.group(1))}{match.group(2)}", text)


def remap_constraint_dataframe_to_cleaned_paths(
    pos_constraint_df,
    *,
    original_paths: list[str],
    cleaned_paths: list[str],
):
    if pos_constraint_df is None or pd is None:
        return pos_constraint_df

    original_by_key = {Path(path).stem: Path(path) for path in original_paths}
    cleaned_by_key = {Path(path).stem: Path(path) for path in cleaned_paths}
    remapped = pos_constraint_df.copy()

    for index, row in remapped.iterrows():
        pdb_key = str(row.get("pdb_key") or "").strip()
        if not pdb_key:
            continue
        original_path = original_by_key.get(pdb_key)
        cleaned_path = cleaned_by_key.get(pdb_key)
        if original_path is None or cleaned_path is None:
            continue
        original_chain_order = parse_chain_order(original_path)
        cleaned_chain_order = parse_chain_order(cleaned_path)
        if not original_chain_order or not cleaned_chain_order:
            continue
        if original_chain_order == cleaned_chain_order:
            continue

        chain_map = {
            original_chain: cleaned_chain
            for original_chain, cleaned_chain in zip(original_chain_order, cleaned_chain_order)
        }
        if not chain_map:
            continue
        for column_name in (
            "fixed_pos_seq",
            "fixed_pos_scn",
            "fixed_pos_override_seq",
            "pos_restrict_aatype",
            "symmetry_pos",
        ):
            if column_name in remapped.columns:
                remapped.at[index, column_name] = remap_position_like_spec(row.get(column_name), chain_map)

    return remapped


def load_caliby_model(model_name: str, device: str | None = None):
    from caliby import load_model  # pragma: no cover - runtime dependency

    return load_model(model_name, device=device)


def maybe_run_self_consistency(
    *,
    model,
    designed_paths: list[str],
    output_dir: Path,
    enabled: bool,
    num_models: int,
    num_recycles: int,
    use_multimer: bool,
) -> dict[str, dict[str, float]]:
    if not enabled or not designed_paths:
        return {}
    return model.self_consistency_eval(
        designed_paths,
        out_dir=str(output_dir),
        num_models=num_models,
        num_recycles=num_recycles,
        use_multimer=use_multimer,
    )


_SELF_CONSISTENCY_ALIASES = {
    "caliby_sc_plddt": ("caliby_sc_plddt", "sc_plddt", "plddt", "avg_plddt", "mean_plddt"),
    "caliby_sc_rmsd": ("caliby_sc_rmsd", "sc_rmsd", "rmsd", "ca_rmsd", "bb_rmsd", "backbone_rmsd"),
    "caliby_sc_ptm": ("caliby_sc_ptm", "sc_ptm", "ptm", "predicted_tm_score"),
    "caliby_sc_tm": ("caliby_sc_tm", "sc_tm", "tm", "tm_score"),
}


def canonicalize_self_consistency_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(metrics or {})
    if not payload:
        return {}
    canonical: dict[str, Any] = {}
    for canonical_key, aliases in _SELF_CONSISTENCY_ALIASES.items():
        for alias in aliases:
            numeric = safe_float(payload.get(alias))
            if numeric is not None:
                canonical[canonical_key] = numeric
                break
    return canonical


def _convert_cif_to_pdb(source: Path, target: Path) -> None:
    import gemmi  # pragma: no cover - runtime dependency

    structure = gemmi.read_structure(str(source))
    structure.write_pdb(str(target))


def publish_structure(source: Path, target_dir: Path, target_name: str) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{target_name}.pdb"
    suffix = source.suffix.lower()
    if suffix == ".pdb":
        shutil.copy2(source, target_path)
    else:
        _convert_cif_to_pdb(source, target_path)
    return target_path


def normalize_sampling_results(
    *,
    results: dict[str, list[Any]],
    output_pdb_dir: Path,
    output_meta_dir: Path,
    prefix: str,
    source: str,
    stage_mode: str,
    extra_metadata: dict[str, Any] | None = None,
    self_consistency: dict[str, dict[str, float]] | None = None,
) -> list[dict[str, Any]]:
    example_ids = list(results.get("example_id", []))
    output_paths = list(results.get("out_pdb", []))
    sequences = list(results.get("seq", []))
    energies = list(results.get("U", []))
    input_sequences = list(results.get("input_seq", []))

    manifest: list[dict[str, Any]] = []
    sc_metrics = self_consistency or {}
    extra = extra_metadata or {}

    for index, example_id in enumerate(example_ids, start=1):
        design_id = f"{prefix}_{index:04d}"
        structure_source = Path(str(output_paths[index - 1])).resolve()
        published_pdb = publish_structure(structure_source, output_pdb_dir, design_id)

        metadata = {
            "design_id": design_id,
            "example_id": example_id,
            "source_backbone_id": example_id,
            "source": source,
            "source_model": source,
            "generator_family": source,
            "generator_mode": stage_mode,
            "stage_family": "caliby",
            "stage_mode": stage_mode,
            "artifact_class": "sequence_designed_complex",
            "result_set": "sequence_designs",
            "review_profile_id": "sequence_design_v1",
            "review_contract_version": 1,
            "review_contract_source": "producer",
            "review_role_map": {
                "result_role": "sequence_designed_complex",
                "source_backbone_id": example_id,
            },
            "review_artifact_manifest": {
                "schema": "bms.review-artifacts.v1",
                "artifacts": {
                    "structure": {
                        "kind": "structure",
                        "state": "ready",
                        "path": str(published_pdb),
                    }
                },
            },
            "score_family": "caliby",
            "selection_metric": "caliby_potts_energy",
            "selection_direction": "lower_is_better",
            "af3score_used": False,
            "upstream_ppiflow_rank_score_used": False,
            "sequence": sequences[index - 1] if index - 1 < len(sequences) else None,
            "input_sequence": input_sequences[index - 1] if index - 1 < len(input_sequences) else None,
            "caliby_potts_energy": energies[index - 1] if index - 1 < len(energies) else None,
            **extra,
        }
        if example_id in sc_metrics:
            raw_self_consistency = sc_metrics[example_id]
            metadata["self_consistency"] = raw_self_consistency
            metadata.update(canonicalize_self_consistency_metrics(raw_self_consistency))

        metadata_path = output_meta_dir / f"generator_{design_id}.json"
        dump_json(metadata_path, metadata)
        manifest.append(
            {
                "design_id": design_id,
                "structure_path": str(published_pdb),
                "metadata_path": str(metadata_path),
                "sequence": metadata.get("sequence"),
                "example_id": example_id,
            }
        )

    return manifest


def convert_fampnn_constraints_to_caliby(input_csv: Path, output_csv: Path) -> None:
    import csv

    rows: list[dict[str, str]] = []
    with input_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            pdb_key = Path(str(row.get("pdb") or "")).stem
            if not pdb_key:
                continue
            rows.append(
                {
                    "pdb_key": pdb_key,
                    "fixed_pos_seq": str(row.get("fixed_seq_positions") or "").strip(),
                    "fixed_pos_scn": str(row.get("fixed_sidechains") or "").strip(),
                }
            )

    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["pdb_key", "fixed_pos_seq", "fixed_pos_scn"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
