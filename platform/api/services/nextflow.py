"""
Nextflow job launcher service.

Handles launching and managing Nextflow pipeline processes.
"""

import asyncio
import subprocess
import os
import signal
import json
import csv
import yaml
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple, Set
import logging

logger = logging.getLogger(__name__)

CPU_RESERVED_THREADS = 4
MIN_DYNAMIC_GPU_CPUS = 2
DEFAULT_BOLTZ_CP_COMPAT_CONTAINER = "boltz2-pre-community-20260417-211613.sif"
DEFAULT_NEXTFLOW_JAVA_HOME = Path("/home/dalab/.local/jdks/temurin-17")


def _java_patch_version(text: str) -> Optional[str]:
    """Extract a Java 17.0.x patch token from java/jspawnhelper output."""
    import re

    match = re.search(r"17\.0\.\d+", text or "")
    return match.group(0) if match else None


def _path_is_java_home(path: Path) -> bool:
    return bool(path) and (path / "bin" / "java").exists()


def resolve_nextflow_java_env(base_env: Dict[str, str]) -> Tuple[Dict[str, str], List[str]]:
    """Pin Nextflow launches to a consistent JDK when one is available.

    BMS has observed system OpenJDK package drift where the active JVM and
    lib/jspawnhelper came from different patch versions. Nextflow uses Java
    ProcessBuilder heavily, so that mismatch can wedge local workflow tasks.
    """
    env = dict(base_env)
    notes: List[str] = []
    explicit = (os.getenv("BMS_NEXTFLOW_JAVA_HOME") or "").strip()
    candidates: List[Tuple[str, Path]] = []
    if explicit:
        candidates.append(("BMS_NEXTFLOW_JAVA_HOME", Path(explicit).expanduser()))
    candidates.append(("default Temurin JDK", DEFAULT_NEXTFLOW_JAVA_HOME))
    inherited = (env.get("JAVA_HOME") or "").strip()
    if inherited:
        candidates.append(("inherited JAVA_HOME", Path(inherited).expanduser()))

    for label, candidate in candidates:
        if _path_is_java_home(candidate):
            resolved = str(candidate)
            env["JAVA_HOME"] = resolved
            java_bin = str(candidate / "bin")
            old_path = env.get("PATH", "")
            env["PATH"] = java_bin if not old_path else f"{java_bin}:{old_path}"
            notes.append(f"Nextflow Java pinned from {label}: {resolved}")
            return env, notes

    notes.append("No explicit/default JAVA_HOME found for Nextflow; using existing PATH")
    return env, notes


def preflight_nextflow_java(env: Dict[str, str]) -> Tuple[bool, str]:
    """Validate that the selected Java runtime is internally consistent."""
    java_home = (env.get("JAVA_HOME") or "").strip()
    java_bin = Path(java_home) / "bin" / "java" if java_home else Path("java")
    try:
        result = subprocess.run(
            [str(java_bin), "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            timeout=15,
            check=False,
        )
    except Exception as exc:
        return False, f"java -version failed for {java_bin}: {exc}"

    java_output = result.stdout or ""
    if result.returncode != 0:
        return False, f"java -version exited {result.returncode}: {java_output.strip()}"

    java_version = _java_patch_version(java_output)
    if java_home:
        helper = Path(java_home) / "lib" / "jspawnhelper"
        if helper.exists():
            try:
                helper_text = helper.read_bytes().decode("utf-8", errors="ignore")
            except Exception as exc:
                return False, f"Could not read jspawnhelper {helper}: {exc}"
            helper_version = _java_patch_version(helper_text)
            if java_version and helper_version and java_version != helper_version:
                return (
                    False,
                    "Java/jspawnhelper version mismatch: "
                    f"java={java_version} helper={helper_version} JAVA_HOME={java_home}",
                )
            return True, f"Java preflight ok: java={java_version or 'unknown'} JAVA_HOME={java_home}"
        return True, f"Java preflight ok: java={java_version or 'unknown'} JAVA_HOME={java_home}; no jspawnhelper found"

    return True, f"Java preflight ok from PATH: java={java_version or 'unknown'}"

# Track running processes
_running_processes: Dict[str, asyncio.subprocess.Process] = {}
_launching_jobs: Set[str] = set()

from paths import (
    get_code_root,
    get_db_path,
    get_data_root,
    get_work_dir,
    get_weights_root,
    get_rfd_models_dir,
    get_colabfold_db,
    get_msa_cache_dir,
)
from antibody_pipeline_contract import (
    ANTIBODY_DENOVO_PIPELINE,
    ANTIBODY_REFINEMENT_PIPELINE,
    is_antibody_pipeline_mode,
)
from .boltzgen_scaffolding import prepare_boltzgen_params_for_launch
from .boltz_cp_shard_plans import (
    BOLTZ_CP_DEFAULT_SHARD_PLAN_ID,
    coerce_boltz_cp_shard_plan_id,
    get_boltz_cp_logical_size_cp,
    infer_boltz_cp_shard_plan_id,
    largest_square_divisor as boltz_cp_largest_square_divisor,
)
from .gpu_config import read_scheduler_config
from .ont_ngs_contract import normalize_ont_launch_params, resolve_ont_workflow_alias
from .workflow_adapter import (
    cancel_via_workflow_adapter,
    get_adapter_running_jobs,
    launch_via_workflow_adapter,
    workflow_adapter_enabled,
)
from runtime_policy import assert_workflow_launch_allowed

# Project root (parent of platform directory)
PROJECT_ROOT = get_code_root()

LEGACY_MAIN_ENTRYPOINT = "main.nf"
DEFAULT_WORKFLOW_ENTRYPOINT = "workflows/protein_design.nf"
COMPLEX_PREDICTION_ENTRYPOINT = "workflows/complex_prediction.nf"
STRUCTURE_PREDICTION_ENTRYPOINT = "workflows/structure_prediction.nf"

# Workflow/product-specific Nextflow entrypoints that have been intentionally
# migrated away from the legacy global main.nf router. Keep this keyed by the
# resolved BioModStack workflow/profile identity only when the profile is not a
# shared engine. Profiles such as "boltz" and "protenix" are reused by ordinary
# structure prediction, antibody validation, PPiFlow, mutagenesis, and core
# protein-design contexts; those route through MODEL_MODE_WORKFLOW_ENTRYPOINTS.
WORKFLOW_ENTRYPOINTS: Dict[str, str] = {
    "oligo_design": "workflows/oligo_design.nf",
    "nanopore_methylation": "workflows/ngs/ont_methylation_analysis.nf",
    "ont_basecall_dna": "workflows/ngs/ont_basecall_dna.nf",
    "ont_basecall_rna": "workflows/ngs/ont_basecall_rna.nf",
    "ont_plasmid_qc": "workflows/ngs/ont_plasmid_qc.nf",
    "ont_construct_screening": "workflows/ngs/ont_construct_screening.nf",
    "ont_methylation_analysis": "workflows/ngs/ont_methylation_analysis.nf",
    "ont_fastq_qc": "workflows/ngs/ont_fastq_qc.nf",
    "protein_local_redesign": "workflows/protein_local_redesign.nf",
    "protein_cad_experimental": "workflows/protein_cad_experimental.nf",
    "caliby_experimental": "workflows/caliby_experimental.nf",
    "protein_hunter_experimental": "workflows/protein_hunter_experimental.nf",
    "boltz_cp_experimental": "workflows/boltz_cp_experimental.nf",
    "confornets_experimental": "workflows/confornets_experimental.nf",
    "esmfold2_experimental": "workflows/esmfold2_experimental.nf",
    "boltzgen": "workflows/boltzgen_design.nf",
    "ppiflow_generator": "workflows/ppiflow_generator_design.nf",
    "antibody_child": "workflows/antibody_child.nf",
    "antibody_backbone": "workflows/rfantibody_backbone.nf",
    "maturation_child": "workflows/maturation_child.nf",
    "bindcraft_child": "workflows/bindcraft_design.nf",
    "docking": "workflows/docking.nf",
    "unidock": "workflows/docking.nf",
    "dual_docking": "workflows/docking.nf",
}

MODEL_MODE_WORKFLOW_ENTRYPOINTS: Dict[Tuple[str, str], str] = {
    ("boltz2", "predict"): STRUCTURE_PREDICTION_ENTRYPOINT,
    ("rf3", "predict"): STRUCTURE_PREDICTION_ENTRYPOINT,
    ("protenix", "predict"): STRUCTURE_PREDICTION_ENTRYPOINT,
    ("boltz2", "complex"): COMPLEX_PREDICTION_ENTRYPOINT,
    ("protenix", "complex"): COMPLEX_PREDICTION_ENTRYPOINT,
    ("bindcraft", "minibinder"): "workflows/bindcraft_design.nf",
    ("bindcraft", "peptide"): "workflows/bindcraft_design.nf",
    ("bindcraft", "bindcraft_child"): "workflows/bindcraft_design.nf",
    ("ppiflow", "generator_backbone_refine"): "workflows/ppiflow_generator_design.nf",
    ("boltzgen", "design"): "workflows/boltzgen_design.nf",
    ("diffdock", "dock"): "workflows/docking.nf",
    ("diffdock", "ntp_dock"): "workflows/docking.nf",
    ("unidock", "dock"): "workflows/docking.nf",
    ("unidock", "ntp_dock"): "workflows/docking.nf",
    ("docking", "compare"): "workflows/docking.nf",
    ("docking", "consensus"): "workflows/docking.nf",
    ("antibody_denovo", ANTIBODY_DENOVO_PIPELINE): "workflows/antibody_denovo.nf",
    ("antibody_denovo", ANTIBODY_REFINEMENT_PIPELINE): "workflows/antibody_denovo.nf",
    ("antibody_denovo", "default"): "workflows/antibody_denovo.nf",
    ("template_antibody_denovo", ANTIBODY_DENOVO_PIPELINE): "workflows/antibody_denovo.nf",
    ("template_antibody_denovo", ANTIBODY_REFINEMENT_PIPELINE): "workflows/antibody_denovo.nf",
    ("template_antibody_denovo", "default"): "workflows/antibody_denovo.nf",
    ("template_antibody_denovo", "maturation_child"): "workflows/maturation_child.nf",
    ("antibody_child", "validation_batch"): "workflows/antibody_child.nf",
    ("rfantibody_child", "antibody_backbone"): "workflows/rfantibody_backbone.nf",
    ("fampnn_child", "sequence_design"): "workflows/fampnn_child.nf",
}


def _params_request_complex_prediction(params: Optional[Dict[str, Any]]) -> bool:
    if not params:
        return False
    return any(
        params.get(key)
        for key in (
            "complex_components",
            "complex_json_path",
            "complex_batch_dir",
        )
    )


def resolve_nextflow_entrypoint(
    *,
    effective_profile: str,
    model_id: Optional[str] = None,
    mode: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
) -> str:
    """Return the workflow-specific Nextflow entrypoint for a launch.

    Entrypoint selection is by product/workflow intent, not by shared engine
    profile alone. Unknown legacy protein-design modes route to
    workflows/protein_design.nf; main.nf is only a thin CLI compatibility wrapper.
    """
    normalized_model_id = str(model_id or "").strip()
    normalized_mode = str(mode or "").strip()

    if normalized_model_id in {"boltz2", "protenix"} and _params_request_complex_prediction(params):
        return COMPLEX_PREDICTION_ENTRYPOINT

    model_mode_entrypoint = MODEL_MODE_WORKFLOW_ENTRYPOINTS.get((normalized_model_id, normalized_mode))
    if model_mode_entrypoint:
        return model_mode_entrypoint

    return WORKFLOW_ENTRYPOINTS.get(effective_profile, DEFAULT_WORKFLOW_ENTRYPOINT)


def parse_stage_progress(work_dir: str, stage: str, total_designs: int = None) -> Optional[str]:
    """
    Parse progress from a Nextflow work directory's .command.log.
    
    Returns a string like "5/30" or None if progress can't be determined.
    
    Each stage has different log patterns:
    - RFAntibody: "Making design antibody_job_X" / "Finished design"
    - FAMPNN/RunFAMPNN: "Processing design X" / pdb file counts
    - Boltz2: "[step X/1000]" or completed sample counts
    - RFdiffusion: "[step X/50]" diffusion steps
    """
    import re
    
    if not work_dir:
        return None
    
    try:
        content_chunks = []
        for candidate in (".command.log", ".command.out", ".command.err"):
            log_path = Path(work_dir) / candidate
            if not log_path.exists():
                continue
            with open(log_path, 'r', errors='replace') as f:
                content_chunks.append(''.join(f.readlines()[-200:]))
        if not content_chunks:
            return None
        content = '\n'.join(content_chunks)
        
        stage_lower = stage.lower() if stage else ""
        
        # RFAntibody: Count "Making design" or "Finished design"
        if 'rfantibody' in stage_lower:
            output_dir = Path(work_dir) / "output"
            completed_outputs = 0
            if output_dir.exists():
                completed_outputs = len(list(output_dir.glob("rfantibody_child_*.pdb")))

            # Count completed designs from recent logs as a fallback only.
            finished = len(re.findall(r'Finished design in', content))
            completed = max(completed_outputs, finished)
            timestep_match = re.findall(r'Timestep\s+(\d+)', content, re.IGNORECASE)
            making_match = re.findall(r'Making design .*?_(\d+)(?:\D|$)', content)
            if timestep_match:
                current_timestep = timestep_match[-1]
                if making_match:
                    current_design = int(making_match[-1]) + 1
                else:
                    current_design = completed + 1
                if total_designs:
                    current_design = min(current_design, total_designs)
                if total_designs:
                    return f"design {current_design}/{total_designs}, diffusion t={current_timestep}"
                return f"design {current_design}, diffusion t={current_timestep}"
            # Try to get total from params or estimate from log
            if total_designs:
                return f"{completed}/{total_designs}"
            # Look for "Making design antibody_job_X" to estimate
            making = re.findall(r'Making design.*antibody_job_(\d+)', content)
            if making:
                max_idx = max(int(m) for m in making) + 1  # 0-indexed
                return f"{completed}/{max_idx}"
            return f"{completed}/?" if completed else None
        
        # FAMPNN: Check for tqdm progress bar or completed designs
        elif 'fampnn' in stage_lower:
            # Check for tqdm progress bar: "Sampling...:  13%|█▎ | 67/500"
            tqdm_match = re.findall(r'\|\s*(\d+)/(\d+)\s*\[', content)
            if tqdm_match:
                last_progress = tqdm_match[-1]
                return f"step {last_progress[0]}/{last_progress[1]}"
            # Fallback: count completed designs
            completed = len(re.findall(r'Saved design', content, re.IGNORECASE))
            if completed and total_designs:
                return f"{completed}/{total_designs}"
            return None

        # Wait stages: child orchestration progress
        elif 'waitfor' in stage_lower and 'children' in stage_lower:
            wait_matches = re.findall(
                r'Progress:\s*(\d+)/(\d+)\s+done,\s*(\d+)\s+running,\s*(\d+)\s+pending,\s*(\d+)\s+failed,\s*(\d+)\s+cancelled',
                content,
                re.IGNORECASE,
            )
            if wait_matches:
                done, total, running, pending, failed, cancelled = wait_matches[-1]
                detail_bits = [f"{done}/{total} done"]
                if int(running) > 0:
                    detail_bits.append(f"{running} running")
                if int(pending) > 0:
                    detail_bits.append(f"{pending} pending")
                if int(failed) > 0:
                    detail_bits.append(f"{failed} failed")
                if int(cancelled) > 0:
                    detail_bits.append(f"{cancelled} cancelled")
                return ", ".join(detail_bits)
            if re.search(r'All children complete!', content, re.IGNORECASE):
                return "complete"
            return None
        
        # Boltz2: Look for step counters or sample completion
        elif 'boltz' in stage_lower:
            # Check for diffusion steps [500/1000]
            step_match = re.findall(r'\[(\d+)/(\d+)\]', content)
            if step_match:
                last_step = step_match[-1]
                return f"step {last_step[0]}/{last_step[1]}"
            # Count completed samples
            samples = len(re.findall(r'Saved prediction|Completed sample', content, re.IGNORECASE))
            if samples and total_designs:
                return f"{samples}/{total_designs}"
            return None
        
        # RFdiffusion: Diffusion steps
        elif 'rfdiffusion' in stage_lower:
            step_match = re.findall(r'step.*?(\d+)/(\d+)', content, re.IGNORECASE)
            if step_match:
                last_step = step_match[-1]
                return f"step {last_step[0]}/{last_step[1]}"
            return None
        
        # ThermoMPNN: Per-residue scoring
        elif 'thermo' in stage_lower:
            residues = len(re.findall(r'residue|position', content, re.IGNORECASE))
            return f"{residues} residues" if residues else None
        
        return None
        
    except Exception as e:
        logger.debug(f"Error parsing progress from {work_dir}: {e}")
        return None


def infer_task_work_dir(task_bucket: str, task_prefix: str) -> Optional[str]:
    if not task_bucket or not task_prefix:
        return None
    work_roots = [Path(get_work_dir()), PROJECT_ROOT / "work"]
    seen_roots = set()
    for work_root in work_roots:
        root_key = str(work_root)
        if root_key in seen_roots:
            continue
        seen_roots.add(root_key)
        bucket_dir = work_root / task_bucket
        if not bucket_dir.exists():
            continue
        try:
            matches = sorted(bucket_dir.glob(f"{task_prefix}*"))
        except Exception:
            continue
        for candidate in matches:
            if candidate.is_dir():
                return str(candidate)
    return None


def sanitize_filename(name: str) -> str:
    """
    Sanitize a string for use as a filename in shell commands.
    
    - Replaces spaces with underscores
    - Removes special characters that could break shell commands
    - Preserves alphanumeric, underscore, hyphen, and dot
    """
    import re
    if not name:
        return "unnamed"
    # Replace spaces with underscores
    sanitized = name.replace(' ', '_')
    # Remove any characters that aren't alphanumeric, underscore, hyphen, or dot
    sanitized = re.sub(r'[^\w\-.]', '', sanitized)
    # Collapse multiple underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    # Remove leading/trailing underscores
    sanitized = sanitized.strip('_')
    # Ensure not empty
    return sanitized if sanitized else "unnamed"


def _normalize_sequence_batch_entries(raw: object, *, prefix: str = "variant") -> List[Dict[str, str]]:
    if not isinstance(raw, list):
        return []

    safe_prefix = sanitize_filename(prefix) or "variant"
    entries: List[Dict[str, str]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        sequence = str(item.get("sequence") or "").strip().upper()
        sequence = "".join(char for char in sequence if char.isalpha())
        if not sequence:
            continue
        raw_name = str(item.get("name") or "").strip()
        safe_name = sanitize_filename(raw_name) if raw_name else ""
        safe_suffix = safe_name[:48] if safe_name else "seq"
        runtime_name = f"{safe_prefix}_{index:03d}"
        if safe_suffix:
            runtime_name = f"{runtime_name}_{safe_suffix}"
        entries.append(
            {
                "name": runtime_name,
                "sequence": sequence,
                "label": raw_name or runtime_name,
                "original_name": raw_name or "",
                "batch_index": str(index),
            }
        )
    return entries


def _write_sequence_batch_name_map(
    *,
    output_dir: Path,
    entries: List[Dict[str, Any]],
) -> None:
    if not entries:
        return

    csv_path = output_dir / "sequence_batch_manifest.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "batch_index",
                "runtime_name",
                "label",
                "original_name",
                "sequence_length",
                "sequence",
                "complex_json",
            ],
        )
        writer.writeheader()
        for entry in entries:
            writer.writerow(
                {
                    "batch_index": entry.get("batch_index", ""),
                    "runtime_name": entry.get("name", ""),
                    "label": entry.get("label", ""),
                    "original_name": entry.get("original_name", ""),
                    "sequence_length": len(str(entry.get("sequence") or "")),
                    "sequence": entry.get("sequence", ""),
                    "complex_json": entry.get("complex_json", ""),
                }
            )


def _write_sequence_batch_payloads(
    *,
    output_dir: str,
    params: Dict[str, Any],
    complex_components: Optional[List[Dict[str, Any]]],
) -> Tuple[Optional[Path], Optional[Path], Optional[List[Dict[str, Any]]]]:
    batch_prefix = (
        str(
            params.get("sequence_batch_prefix")
            or params.get("sequence_name")
            or params.get("job_name")
            or params.get("name")
            or "variant"
        ).strip()
    )
    batch_entries = _normalize_sequence_batch_entries(
        params.pop("sequence_batch_entries", None),
        prefix=batch_prefix,
    )
    if not batch_entries:
        return None, None, complex_components

    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    sequence_batch_json_path: Optional[Path] = None
    complex_batch_dir: Optional[Path] = None

    if complex_components:
        normalized_components: List[Dict[str, Any]] = [dict(component) for component in complex_components]
        protein_components = [
            component
            for component in normalized_components
            if str(component.get("type") or "").strip().lower() in {"protein", "peptide"}
        ]

        if len(protein_components) == 1:
            used_ids = {
                str(component.get("id") or "").strip()
                for component in normalized_components
                if str(component.get("id") or "").strip()
            }
            implicit_component_id = ""
            for candidate_ord in range(ord("A"), ord("Z") + 1):
                candidate = chr(candidate_ord)
                if candidate not in used_ids:
                    implicit_component_id = candidate
                    break
            if not implicit_component_id:
                implicit_component_id = f"P{len(used_ids) + 1}"

            batch_binder_name = (
                str(params.get("sequence_batch_component_name") or "").strip()
                or f"{batch_prefix} binder"
            )
            normalized_components.append(
                {
                    "type": "protein",
                    "id": implicit_component_id,
                    "sequence": batch_entries[0]["sequence"],
                    "name": batch_binder_name,
                }
            )
            params["sequence_batch_component_id"] = implicit_component_id

        replace_component_id = str(
            params.get("sequence_batch_component_id")
            or params.get("binder_chains")
            or params.get("primary_chain_id")
            or ""
        ).split(",")[0].strip()
        if not replace_component_id:
            protein_ids = [
                str(component.get("id") or "").strip()
                for component in normalized_components
                if str(component.get("type") or "").strip().lower() in {"protein", "peptide"}
            ]
            replace_component_id = protein_ids[-1] if protein_ids else ""
        if not replace_component_id:
            raise ValueError("Could not determine which complex protein component should be replaced by sequence_batch_entries")

        complex_batch_dir = out_root / "complex_batch_inputs"
        complex_batch_dir.mkdir(parents=True, exist_ok=True)
        batch_manifest: List[Dict[str, Any]] = []
        for index, entry in enumerate(batch_entries, start=1):
            variant_name = sanitize_filename(f"{entry['name']}")
            variant_components: List[Dict[str, Any]] = []
            replaced = False
            for component in normalized_components:
                copied = dict(component)
                component_id = str(copied.get("id") or "").strip()
                component_type = str(copied.get("type") or "").strip().lower()
                if component_id == replace_component_id and component_type in {"protein", "peptide"}:
                    copied["sequence"] = entry["sequence"]
                    replaced = True
                variant_components.append(copied)
            if not replaced:
                raise ValueError(
                    f"sequence_batch_component_id={replace_component_id!r} did not match a protein/peptide component in complex_components"
                )
            variant_payload = {
                "name": variant_name,
                "components": variant_components,
            }
            variant_path = complex_batch_dir / f"{index:03d}_{variant_name}.json"
            with variant_path.open("w", encoding="utf-8") as handle:
                json.dump(variant_payload, handle, indent=2)
            batch_manifest.append(
                {
                    "name": variant_name,
                    "label": entry["label"],
                    "sequence": entry["sequence"],
                    "complex_json": str(variant_path),
                }
            )
        sequence_batch_json_path = out_root / "sequence_batch_manifest.json"
        with sequence_batch_json_path.open("w", encoding="utf-8") as handle:
            json.dump(batch_manifest, handle, indent=2)
        _write_sequence_batch_name_map(output_dir=out_root, entries=batch_manifest)
        complex_components = normalized_components
    else:
        sequence_batch_json_path = Path(output_dir) / "sequence_batch_manifest.json"
        with sequence_batch_json_path.open("w", encoding="utf-8") as handle:
            json.dump(batch_entries, handle, indent=2)
        _write_sequence_batch_name_map(output_dir=out_root, entries=batch_entries)

    return sequence_batch_json_path, complex_batch_dir, complex_components


def _parse_boltz_cp_gpu_ids(value: object) -> List[int]:
    raw_values = value if isinstance(value, list) else str(value or "").split(",")
    seen: Set[int] = set()
    parsed: List[int] = []
    for raw_value in raw_values:
        try:
            gpu_id = int(raw_value)
        except (TypeError, ValueError):
            continue
        if gpu_id < 0 or gpu_id in seen:
            continue
        seen.add(gpu_id)
        parsed.append(gpu_id)
    return parsed


def _largest_square_divisor(gpu_count: int, requested_size_cp: object) -> int:
    return boltz_cp_largest_square_divisor(gpu_count, requested_size_cp)


def _derive_boltz_cp_gpu_launch_settings(
    *,
    pinned_gpus: object,
    requested_size_cp: object,
    fallback_gpu_ids: object = None,
    scheduler_gpu_id: object = None,
) -> Tuple[str, int]:
    """Resolve the physical CP launch bridge without assuming host GPU ordinals."""
    raw_gpu_ids = None
    if isinstance(pinned_gpus, list) and pinned_gpus:
        raw_gpu_ids = pinned_gpus
    elif fallback_gpu_ids not in (None, ""):
        raw_gpu_ids = fallback_gpu_ids
    elif scheduler_gpu_id not in (None, ""):
        raw_gpu_ids = scheduler_gpu_id

    parsed_gpu_ids = _parse_boltz_cp_gpu_ids(raw_gpu_ids)
    return ",".join(str(gpu_id) for gpu_id in parsed_gpu_ids), _largest_square_divisor(len(parsed_gpu_ids), requested_size_cp)


def _normalize_boltz_cp_component_id(value: object, fallback: str) -> List[str]:
    component_id = str(value or fallback).strip() or fallback
    return [component_id]


def _normalize_boltz_cp_sequence(value: object) -> str:
    return str(value or "").strip().upper()


def _build_boltz_cp_sequence_entry(
    component: Dict[str, Any],
    index: int,
    *,
    use_msa: bool = False,
) -> Dict[str, Any]:
    component_type = str(component.get("type") or "protein").strip().lower()
    if component_type == "ion":
        component_type = "ligand"
    fallback_id = chr(ord("A") + (index % 26))
    component_id = _normalize_boltz_cp_component_id(component.get("id"), fallback_id)

    if component_type in {"protein", "peptide"}:
        sequence = _normalize_boltz_cp_sequence(component.get("sequence"))
        if not sequence:
            raise ValueError(f"Boltz-CP protein component {component_id[0]!r} is missing a sequence")
        protein_payload: Dict[str, Any] = {
            "id": component_id,
            "sequence": sequence,
        }
        if not use_msa:
            protein_payload["msa"] = "empty"
        return {"protein": protein_payload}

    if component_type == "dna":
        sequence = _normalize_boltz_cp_sequence(component.get("sequence"))
        if not sequence:
            raise ValueError(f"Boltz-CP DNA component {component_id[0]!r} is missing a sequence")
        return {
            "dna": {
                "id": component_id,
                "sequence": sequence,
            }
        }

    if component_type == "rna":
        sequence = _normalize_boltz_cp_sequence(component.get("sequence"))
        if not sequence:
            raise ValueError(f"Boltz-CP RNA component {component_id[0]!r} is missing a sequence")
        return {
            "rna": {
                "id": component_id,
                "sequence": sequence,
            }
        }

    if component_type in {"ligand", "small_molecule"}:
        ligand_payload: Dict[str, Any] = {"id": component_id}
        for field_name in ("smiles", "ccd", "path", "name"):
            field_value = component.get(field_name)
            if field_value not in (None, ""):
                ligand_payload[field_name] = field_value
        if not any(field in ligand_payload for field in ("smiles", "ccd", "path")):
            raise ValueError(
                f"Boltz-CP ligand component {component_id[0]!r} requires one of smiles, ccd, or path"
            )
        return {"ligand": ligand_payload}

    raise ValueError(f"Unsupported Boltz-CP component type: {component_type!r}")


def _write_boltz_cp_input_yaml(
    *,
    output_dir: str,
    params: Dict[str, Any],
    complex_components: Optional[List[Dict[str, Any]]],
) -> Optional[Path]:
    if params.get("bcp_input_path") or params.get("input_path"):
        return None

    use_msa = _coerce_bool(params.get("boltz_use_msa"), default=False)

    if complex_components:
        sequences = [
            _build_boltz_cp_sequence_entry(component, index, use_msa=use_msa)
            for index, component in enumerate(complex_components)
        ]
    else:
        sequence = _normalize_boltz_cp_sequence(params.get("sequence") or params.get("sequence_input"))
        if not sequence:
            return None
        primary_chain_id = str(
            params.get("primary_chain_id")
            or params.get("target_chains")
            or "A"
        ).split(",")[0].strip() or "A"
        protein_payload: Dict[str, Any] = {
            "id": [primary_chain_id],
            "sequence": sequence,
        }
        if not use_msa:
            protein_payload["msa"] = "empty"
        sequences = [{"protein": protein_payload}]

    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    yaml_path = out_root / "boltz_cp_input.yaml"
    yaml_path.write_text(
        yaml.safe_dump({"version": 1, "sequences": sequences}, sort_keys=False),
        encoding="utf-8",
    )
    return yaml_path


def _coerce_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _coerce_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_protenix_job(model_id: str, params: Dict[str, Any]) -> bool:
    if (model_id or "").lower() == "protenix":
        return True
    pred_method = str(params.get("pred_method", "")).strip().lower()
    return pred_method == "protenix"


def _is_esm_model(model_name: str) -> bool:
    lowered = (model_name or "").lower()
    return "esm" in lowered or "ism" in lowered


def _normalize_msa_preset(value: object) -> str:
    preset = str(value).strip().lower() if value is not None else "fast"
    if preset in {"maximum", "max"}:
        return "maximum"
    if preset in {"balanced", "balance", "medium"}:
        return "balanced"
    if preset in {"fast", "quick", "default"}:
        return "fast"
    return "fast"


def _normalize_protenix_msa_backend(value: object) -> str:
    backend = str(value).strip().lower() if value is not None else ""
    if backend in {"auto", "local", "colabfold_api"}:
        return backend
    return ""


def _dynamic_gpu_cpu_pool_threads() -> int:
    total_threads = os.cpu_count() or 48
    return max(1, total_threads - CPU_RESERVED_THREADS)


async def _resolve_dynamic_gpu_cpu_share(session, job, launch_params: Dict[str, Any]) -> Optional[int]:
    scheduler_config = read_scheduler_config()
    global_config = scheduler_config.get("global", {})
    configured_share = global_config.get("cpu_threads_per_job")
    auto_cpu_threads = bool(global_config.get("auto_cpu_threads", True))
    auto_cpu_thread_job_threshold = global_config.get("auto_cpu_thread_job_threshold", 2)
    try:
        if not auto_cpu_threads and configured_share is not None:
            return max(1, min(24, int(configured_share)))
    except (TypeError, ValueError):
        pass

    try:
        max_cpu_share = max(1, min(24, int(configured_share))) if configured_share is not None else 24
    except (TypeError, ValueError):
        max_cpu_share = 24
    try:
        job_threshold = max(1, int(auto_cpu_thread_job_threshold))
    except (TypeError, ValueError):
        job_threshold = 2

    from sqlalchemy import select
    from database import Job

    gpu_id = launch_params.get("gpu_id")
    if gpu_id in (None, "") and getattr(job, "assigned_gpu", None) is None:
        return None

    concurrency_target = 0
    batch_id = getattr(job, "batch_id", None)

    if batch_id:
        sibling_rows = await session.execute(
            select(Job.id).where(
                Job.batch_id == batch_id,
                Job.queue_status.in_(["queued", "running"]),
            )
        )
        concurrency_target = len(list(sibling_rows.scalars().all()))
    else:
        running_rows = await session.execute(
            select(Job.id).where(
                Job.queue_status == "running",
                Job.assigned_gpu.isnot(None),
            )
        )
        concurrency_target = len(list(running_rows.scalars().all()))

    concurrency_target = max(1, concurrency_target)
    if concurrency_target <= job_threshold:
        cpu_share = max_cpu_share
    else:
        cpu_share = max(MIN_DYNAMIC_GPU_CPUS, _dynamic_gpu_cpu_pool_threads() // concurrency_target)
        cpu_share = min(max_cpu_share, cpu_share)
    return cpu_share


def _estimate_protenix_token_count(params: Dict[str, Any]) -> int:
    """
    Approximate Protenix token count from input payload.

    Protenix memory scales primarily with total tokens over protein/DNA/RNA/peptide
    entities, not with only the longest chain.
    """
    components = params.get("complex_components")
    total_tokens = 0
    if isinstance(components, list):
        for comp in components:
            if not isinstance(comp, dict):
                continue
            comp_type = str(comp.get("type", "")).strip().lower()
            if comp_type not in {"protein", "peptide", "dna", "rna"}:
                continue
            seq = comp.get("sequence")
            if not isinstance(seq, str):
                continue
            count = max(1, _coerce_int(comp.get("count", 1), 1))
            total_tokens += len(seq) * count

    if total_tokens > 0:
        return total_tokens

    for key in ("sequence_input", "sequence"):
        seq = params.get(key)
        if isinstance(seq, str) and seq:
            return len(seq)

    return 300


def _apply_protenix_preflight(params: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """
    Apply conservative Protenix-only launch guardrails before first run.

    This avoids obvious OOM scenarios without touching non-Protenix workflows.
    """
    tuned = dict(params)
    notes: List[str] = []

    token_count = _estimate_protenix_token_count(tuned)
    use_msa = _coerce_bool(tuned.get("protenix_use_msa", True), default=True)
    msa_preset = _normalize_msa_preset(tuned.get("msa_preset", "fast"))

    n_sample = max(1, _coerce_int(tuned.get("protenix_n_sample", 5), 5))
    n_cycle = max(1, _coerce_int(tuned.get("protenix_n_cycle", 10), 10))

    tier = "low"
    if use_msa:
        if token_count >= 1700 or (token_count >= 1400 and msa_preset in {"balanced", "maximum"}):
            tier = "high"
        elif token_count >= 1200:
            tier = "medium"
    else:
        if token_count >= 2400:
            tier = "high"
        elif token_count >= 1800:
            tier = "medium"

    if tier == "medium":
        if n_sample > 3:
            tuned["protenix_n_sample"] = 3
            notes.append(f"protenix_n_sample: {n_sample} -> 3")
        if n_cycle > 8:
            tuned["protenix_n_cycle"] = 8
            notes.append(f"protenix_n_cycle: {n_cycle} -> 8")

    if tier == "high":
        if n_sample > 1:
            tuned["protenix_n_sample"] = 1
            notes.append(f"protenix_n_sample: {n_sample} -> 1")
        if n_cycle > 4:
            tuned["protenix_n_cycle"] = 4
            notes.append(f"protenix_n_cycle: {n_cycle} -> 4")

    if use_msa:
        backend = _normalize_protenix_msa_backend(tuned.get("protenix_msa_backend")) or "auto"
        msa_cache_only = _coerce_bool(tuned.get("msa_cache_only", False), default=False)
        requested_validation_batch = max(1, _coerce_int(tuned.get("seqs_per_validation_job", tuned.get("seqs_per_boltz_job", 10)), 10))
        batch_cap_key = "protenix_local_msa_max_seqs_per_validation_job" if backend == "local" else "protenix_msa_max_seqs_per_validation_job"
        batch_cap_default = 1
        batch_cap = max(1, _coerce_int(tuned.get(batch_cap_key, batch_cap_default), batch_cap_default))
        if backend == "local" and msa_cache_only:
            batch_cap = requested_validation_batch
        if requested_validation_batch > batch_cap:
            tuned["seqs_per_validation_job"] = batch_cap
            notes.append(f"seqs_per_validation_job: {requested_validation_batch} -> {batch_cap}")
        if "protenix_local_msa_timeout_seconds" not in tuned:
            tuned["protenix_local_msa_timeout_seconds"] = 900

    # Allow override; keep the retry ladder configured but disabled by default.
    if "protenix_oom_retry_attempts" not in tuned:
        tuned["protenix_oom_retry_attempts"] = 2
    if "protenix_auto_oom_retry" not in tuned:
        tuned["protenix_auto_oom_retry"] = False

    if notes:
        notes.insert(0, f"tier={tier}, token_estimate={token_count}, use_msa={use_msa}")
    return tuned, notes


def _attempt_has_cuda_oom(lines: List[str]) -> bool:
    oom_markers = (
        "CUDA out of memory",
        "torch.OutOfMemoryError",
        "OutOfMemoryError",
        "CUBLAS_STATUS_ALLOC_FAILED",
    )
    joined = "\n".join(lines)
    return any(marker in joined for marker in oom_markers)


def _apply_protenix_oom_retry_downshift(params: Dict[str, Any], rung: int) -> Tuple[Dict[str, Any], List[str]]:
    """
    OOM retry ladder for Protenix.

    rung=1: reduce sample/cycle and force fast MSA.
    rung=2: disable MSA and switch to mini ESM if needed.
    rung=3: reduce diffusion/inference steps.
    """
    tuned = dict(params)
    changes: List[str] = []

    n_sample = max(1, _coerce_int(tuned.get("protenix_n_sample", 5), 5))
    n_cycle = max(1, _coerce_int(tuned.get("protenix_n_cycle", 10), 10))
    n_step = max(1, _coerce_int(tuned.get("protenix_n_step", 200), 200))
    use_msa = _coerce_bool(tuned.get("protenix_use_msa", True), default=True)
    msa_preset = _normalize_msa_preset(tuned.get("msa_preset", "fast"))
    model_name = str(tuned.get("protenix_model_weights", "protenix_base_20250630_v1.0.0"))

    if rung >= 1:
        if n_sample > 1:
            tuned["protenix_n_sample"] = 1
            changes.append(f"protenix_n_sample: {n_sample} -> 1")
        if n_cycle > 4:
            tuned["protenix_n_cycle"] = 4
            changes.append(f"protenix_n_cycle: {n_cycle} -> 4")
        if use_msa and msa_preset != "fast":
            tuned["msa_preset"] = "fast"
            changes.append(f"msa_preset: {msa_preset} -> fast")

    if rung >= 2:
        if use_msa:
            tuned["protenix_use_msa"] = False
            changes.append("protenix_use_msa: true -> false")
        if not _is_esm_model(model_name):
            tuned["protenix_model_weights"] = "protenix_mini_esm_v0.5.0"
            changes.append(f"protenix_model_weights: {model_name} -> protenix_mini_esm_v0.5.0")

    if rung >= 3:
        if n_step > 100:
            tuned["protenix_n_step"] = 100
            changes.append(f"protenix_n_step: {n_step} -> 100")

    return tuned, changes


def _is_antibody_job(job) -> bool:
    model_id = (job.model_id or "").lower()
    mode = (job.mode or "").lower()
    name = (job.name or "").lower()
    return (
        model_id in {"rfantibody", "antibody_denovo", "template_antibody_denovo", "antibody_child"} or
        "antibody" in model_id or
        "antibody" in mode or
        "nanobody" in mode or
        "vhh" in mode or
        "antibody" in name or
        "nanobody" in name or
        "vhh" in name
    )


async def maybe_auto_annotate_cdrs(job, session) -> None:
    """
    Backward-compatible wrapper for the newer viewer-minimum analysis autorun.
    """
    try:
        from services.analysis_autorun import schedule_viewer_minimum_analyses_for_job

        schedule_viewer_minimum_analyses_for_job(str(job.id))
    except Exception as e:
        logger.warning(f"[CDR AUTO] Failed to schedule viewer-minimum analyses: {e}")


async def maybe_trigger_batch_frustrampnn(job, session) -> None:
    """
    BATCH-STAGE-GATE: Trigger batch FrustraMPNN after ALL sibling variants complete.
    
    Checks if:
    1. Job is part of a batch (has batch_id)
    2. Parent MSA job has run_frustrampnn_batch=True
    3. ALL sibling variant jobs are complete (completed or failed)
    
    If all conditions met, collects PDBs from all variants and runs FrustraMPNN once.
    """
    if not job.batch_id:
        return
    
    from database import Job, Design
    from sqlalchemy import select, func, and_, or_
    
    try:
        # Find parent MSA job in this batch
        msa_result = await session.execute(
            select(Job).where(
                Job.batch_id == job.batch_id,
                Job.job_phase == "msa_generation"
            )
        )
        msa_job = msa_result.scalar_one_or_none()
        
        if not msa_job:
            return
        
        # Check if FrustraMPNN batch is requested
        run_frustrampnn_batch = msa_job.params.get("run_frustrampnn_batch", False)
        if not run_frustrampnn_batch:
            return
        
        # Check if already triggered (avoid duplicate runs)
        if msa_job.params.get("_frustrampnn_batch_triggered"):
            return
        
        # Count sibling variant jobs
        variant_result = await session.execute(
            select(func.count(Job.id)).where(
                Job.batch_id == job.batch_id,
                Job.job_phase == "inference"  # Variant jobs
            )
        )
        total_variants = variant_result.scalar() or 0
        
        completed_result = await session.execute(
            select(func.count(Job.id)).where(
                Job.batch_id == job.batch_id,
                Job.job_phase == "inference",
                or_(Job.status == "completed", Job.status == "failed")
            )
        )
        completed_variants = completed_result.scalar() or 0
        
        logger.info(f"[FRUST BATCH] Variant progress: {completed_variants}/{total_variants} for batch {job.batch_id[:8]}")
        
        if completed_variants < total_variants:
            return
        
        # ALL VARIANTS COMPLETE - trigger batch FrustraMPNN
        logger.info(f"[FRUST BATCH] All {total_variants} variants complete! Triggering batch FrustraMPNN...")
        
        # Mark as triggered to prevent duplicates
        msa_job.params = {**msa_job.params, "_frustrampnn_batch_triggered": True}
        
        # Collect all PDB paths from designs in this batch
        design_result = await session.execute(
            select(Design.pdb_path, Design.job_id).where(
                Design.job_id.in_(
                    select(Job.id).where(
                        Job.batch_id == job.batch_id,
                        Job.job_phase == "inference"
                    )
                )
            )
        )
        designs = design_result.all()
        pdb_paths = [d.pdb_path for d in designs if d.pdb_path]
        
        if not pdb_paths:
            logger.warning(f"[FRUST BATCH] No PDBs found for batch {job.batch_id[:8]}")
            return
        
        logger.info(f"[FRUST BATCH] Running FrustraMPNN on {len(pdb_paths)} PDBs...")
        
        # Run FrustraMPNN batch in background task
        asyncio.create_task(
            run_batch_frustrampnn(pdb_paths, job.batch_id, session)
        )
        
        await session.commit()
        
    except Exception as e:
        logger.error(f"[FRUST BATCH] Error checking batch completion: {e}", exc_info=True)


async def maybe_trigger_mutation_seed_refinement(job, session) -> None:
    """
    Trigger a follow-on antibody refinement round after a mutagenesis batch
    finishes generating structural seeds.

    This is used for mutation-seeded refinement of CDR indel batches: first we
    rebuild structures for the mutated sequences, then we feed the successful
    rebuilt structures back into the antibody workflow orchestrator.
    """
    if not job.batch_id:
        return

    from database import Job, Design
    from sqlalchemy import select, func, or_

    try:
        msa_result = await session.execute(
            select(Job).where(
                Job.batch_id == job.batch_id,
                Job.job_phase == "msa_generation",
            )
        )
        msa_job = msa_result.scalar_one_or_none()
        if not msa_job:
            return

        trigger_cfg = msa_job.params.get("mutation_seed_refinement_trigger") if isinstance(msa_job.params, dict) else None
        if not isinstance(trigger_cfg, dict):
            return
        if msa_job.params.get("_mutation_seed_refinement_triggered"):
            return

        variant_result = await session.execute(
            select(func.count(Job.id)).where(
                Job.batch_id == job.batch_id,
                Job.job_phase == "inference",
            )
        )
        total_variants = variant_result.scalar() or 0
        terminal_result = await session.execute(
            select(func.count(Job.id)).where(
                Job.batch_id == job.batch_id,
                Job.job_phase == "inference",
                or_(Job.status == "completed", Job.status == "failed"),
            )
        )
        terminal_variants = terminal_result.scalar() or 0
        logger.info(
            f"[MUT-SEED] Variant progress: {terminal_variants}/{total_variants} for batch {job.batch_id[:8]}"
        )
        if total_variants == 0 or terminal_variants < total_variants:
            return

        successful_jobs_result = await session.execute(
            select(Job)
            .where(
                Job.batch_id == job.batch_id,
                Job.job_phase == "inference",
                Job.status == "completed",
            )
            .order_by(Job.created_at.asc())
        )
        successful_jobs = successful_jobs_result.scalars().all()

        msa_job.params = {**msa_job.params, "_mutation_seed_refinement_triggered": True}
        if not successful_jobs:
            logger.warning(f"[MUT-SEED] No successful variant jobs found for batch {job.batch_id[:8]}")
            await session.commit()
            return

        design_result = await session.execute(
            select(Design).where(Design.job_id.in_([variant_job.id for variant_job in successful_jobs]))
        )
        found_designs = design_result.scalars().all()
        if not found_designs:
            logger.warning(f"[MUT-SEED] No ingested designs found for successful batch {job.batch_id[:8]}")
            await session.commit()
            return

        design_by_job: Dict[str, List[Design]] = {}
        for design in found_designs:
            design_by_job.setdefault(str(design.job_id), []).append(design)

        ordered_designs: List[Design] = []
        design_job_map: Dict[str, Job] = {}
        for variant_job in successful_jobs:
            matched_designs = design_by_job.get(str(variant_job.id), [])
            for design in matched_designs:
                ordered_designs.append(design)
                design_job_map[str(design.job_id)] = variant_job

        if not ordered_designs:
            logger.warning(f"[MUT-SEED] Successful batch {job.batch_id[:8]} had no ordered designs to seed refinement")
            await session.commit()
            return

        source_job_id = str(trigger_cfg.get("source_job_id") or "").strip()
        root_job_id = str(trigger_cfg.get("root_job_id") or "").strip()
        if not source_job_id or not root_job_id:
            logger.warning(f"[MUT-SEED] Missing source/root job ids in trigger config for batch {job.batch_id[:8]}")
            await session.commit()
            return

        source_job = await session.get(Job, source_job_id)
        root_job = await session.get(Job, root_job_id)
        if not source_job or not root_job:
            logger.warning(f"[MUT-SEED] Could not resolve source/root jobs for batch {job.batch_id[:8]}")
            await session.commit()
            return

        from fastapi import BackgroundTasks
        from routers.jobs import (
            _materialize_seed_selection_from_completed_designs,
            _build_antibody_iteration_job,
            create_job,
        )

        selection_dir, fixed_json_path = _materialize_seed_selection_from_completed_designs(
            root_job=root_job,
            source_job=source_job,
            designs=ordered_designs,
            design_job_map=design_job_map,
            action="mutation_seeded_refinement",
        )
        param_overrides = dict(trigger_cfg.get("param_overrides") or {})
        param_overrides.update({
            "manual_mutation_mode": "seeded_refinement",
            "manual_mutation_method": str(trigger_cfg.get("manual_mutation_method") or "cdr_indels"),
            "manual_mutation_fixed_positions_json": str(fixed_json_path),
        })
        launch_request = _build_antibody_iteration_job(
            root_job=root_job,
            source_job=source_job,
            action="ui_refinement",
            selection_dir=selection_dir,
            design_ids=[design.id for design in ordered_designs],
            name_suffix=str(trigger_cfg.get("name_suffix") or "mutation_seeded_refinement"),
            param_overrides=param_overrides,
        )

        await session.commit()
        logger.info(
            f"[MUT-SEED] Launching seeded refinement from {len(ordered_designs)} rebuilt designs for batch {job.batch_id[:8]}"
        )
        await create_job(launch_request, BackgroundTasks(), session)

    except Exception as e:
        logger.error(f"[MUT-SEED] Error triggering seeded refinement: {e}", exc_info=True)


async def run_batch_frustrampnn(pdb_paths: list, batch_id: str, parent_session) -> None:
    """
    Run FrustraMPNN on a batch of PDBs and update Design table with frustration metrics.
    
    Uses the frustrampnn container with single model load for efficiency.
    """
    from database import async_session, Design
    from sqlalchemy import select
    from pathlib import Path
    import subprocess
    import tempfile
    
    logger.info(f"[FRUST BATCH] Starting FrustraMPNN for {len(pdb_paths)} PDBs...")
    
    try:
        # Create manifest of PDBs
        from paths import get_project_root
        container_path = Path(get_project_root()) / "containers" / "frustrampnn.sif"
        
        if not container_path.exists():
            logger.error(f"[FRUST BATCH] Container not found: {container_path}")
            return
        
        # Process each PDB
        for pdb_path in pdb_paths:
            if not Path(pdb_path).exists():
                logger.warning(f"[FRUST BATCH] PDB not found: {pdb_path}")
                continue
            
            output_csv = Path(pdb_path).with_suffix('.frustration.csv')
            
            # Run frustrampnn predict
            cmd = [
                "apptainer", "run", "--nv",
                str(container_path),
                "predict",
                "--pdb", pdb_path,
                "--checkpoint", "/opt/frustrampnn_weights/megascale.ckpt",
                "--output", str(output_csv)
            ]
            
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if result.returncode != 0:
                    logger.warning(f"[FRUST BATCH] FrustraMPNN failed for {pdb_path}: {result.stderr[:200]}")
                    continue
            except subprocess.TimeoutExpired:
                logger.warning(f"[FRUST BATCH] FrustraMPNN timeout for {pdb_path}")
                continue
            
            # Parse and update Design
            if output_csv.exists():
                from services.result_ingester import parse_frustration_csv
                
                frust_data = parse_frustration_csv(output_csv)
                if frust_data:
                    async with async_session() as session:
                        # Find design by PDB path
                        result = await session.execute(
                            select(Design).where(Design.pdb_path == pdb_path)
                        )
                        design = result.scalar_one_or_none()
                        
                        if design:
                            design.frustration_pct_high = frust_data.get("pct_high")
                            design.frustration_high_count = frust_data.get("high_count")
                            design.frustration_min_count = frust_data.get("min_count")
                            design.frustration_residues = frust_data.get("residues")
                            design.frustration_csv_path = str(output_csv)
                            await session.commit()
                            logger.info(f"[FRUST BATCH] Updated frustration for {design.name}")
        
        logger.info(f"[FRUST BATCH] Completed FrustraMPNN batch for {len(pdb_paths)} PDBs")
        
    except Exception as e:
        logger.error(f"[FRUST BATCH] Error running batch FrustraMPNN: {e}", exc_info=True)


def _build_msa_batch_command(params: Dict[str, Any], output_dir: str) -> List[str]:
    sequences_json = params.get('sequences_json', '[]')
    if isinstance(sequences_json, (list, dict)):
        sequences_json = json.dumps(sequences_json)
    elif not isinstance(sequences_json, str):
        sequences_json = '[]'

    raw_gpu_id = params.get('gpu_id')
    try:
        gpu_id = int(raw_gpu_id) if raw_gpu_id is not None else None
    except (TypeError, ValueError):
        gpu_id = None

    reference_sequence = params.get('reference_sequence', '')
    force_refresh = _coerce_bool(params.get('msa_force_refresh', False), default=False)
    cache_only = _coerce_bool(params.get('msa_cache_only', False), default=False)
    msa_use_gpu = _coerce_bool(params.get('msa_use_gpu', True), default=True)
    msa_max_seqs = params.get('msa_max_seqs')
    msa_preset = _normalize_msa_preset(params.get('msa_preset', 'fast'))
    msa_use_expand = params.get('msa_use_expand')
    msa_use_env = params.get('msa_use_env')
    msa_num_iterations = params.get('msa_num_iterations')
    msa_evalue = params.get('msa_evalue')
    msa_min_seq_id = params.get('msa_min_seq_id')
    msa_min_coverage = params.get('msa_min_coverage')
    msa_taxon_list = params.get('msa_taxon_list')
    msa_min_depth_warning = params.get('msa_min_depth_warning')
    msa_min_depth_fail = params.get('msa_min_depth_fail')
    msa_gpu_mode = params.get('msa_gpu_mode')
    msa_gpu_threshold = params.get('msa_gpu_threshold')
    msa_preferred_gpus = params.get('msa_preferred_gpus')
    msa_excluded_gpus = params.get('msa_excluded_gpus')
    msa_gpu_server_mode = params.get('msa_gpu_server_mode')
    msa_gpu_server_wait_timeout = params.get('msa_gpu_server_wait_timeout')
    msa_gpu_server_db_load_mode = params.get('msa_gpu_server_db_load_mode')
    msa_gpu_server_startup_wait = params.get('msa_gpu_server_startup_wait')
    msa_threads = params.get('msa_threads')
    msa_target_shard_mode = params.get('msa_target_shard_mode')
    msa_target_shards = params.get('msa_target_shards')
    msa_target_shard_min_size_gb = params.get('msa_target_shard_min_size_gb')

    from paths import get_colabfold_db, get_msa_cache_dir
    db_path = str(params.get('msa_local_db') or get_colabfold_db())
    cache_dir = str(params.get('msa_cache_dir') or get_msa_cache_dir())
    script_path = PROJECT_ROOT / 'scripts' / 'batch_msa.py'
    cmd = [
        'python3', str(script_path),
        '--sequences', sequences_json,
        '--output_dir', output_dir,
        '--db_path', db_path,
        '--cache_dir', cache_dir,
        '--preset', msa_preset,
    ]
    if gpu_id is not None:
        cmd.extend(['--gpu_id', str(gpu_id)])
    if reference_sequence:
        cmd.extend(['--reference_sequence', reference_sequence])
    if force_refresh:
        cmd.append('--force_refresh')
    if cache_only:
        cmd.append('--cache-only')
    if msa_use_gpu is False:
        cmd.append('--cpu-only')
    if msa_max_seqs is not None:
        cmd.extend(['--max-seqs', str(msa_max_seqs)])
    if msa_threads is not None:
        cmd.extend(['--threads', str(max(1, int(msa_threads)))])
    if msa_use_expand is not None:
        cmd.extend(['--use-expand', '1' if _coerce_bool(msa_use_expand) else '0'])
    if msa_use_env is not None:
        cmd.extend(['--use-env', '1' if _coerce_bool(msa_use_env) else '0'])
    if msa_num_iterations is not None:
        cmd.extend(['--num-iterations', str(msa_num_iterations)])
    if msa_evalue is not None:
        cmd.extend(['--evalue', str(msa_evalue)])
    if msa_min_seq_id is not None:
        cmd.extend(['--min-seq-id', str(msa_min_seq_id)])
    if msa_min_coverage is not None:
        cmd.extend(['--min-coverage', str(msa_min_coverage)])
    if msa_taxon_list:
        cmd.extend(['--taxon-list', str(msa_taxon_list)])
    if msa_min_depth_warning is not None:
        cmd.extend(['--min-depth-warning', str(msa_min_depth_warning)])
    if msa_min_depth_fail is not None:
        cmd.extend(['--min-depth-fail', str(msa_min_depth_fail)])
    if msa_gpu_mode:
        cmd.extend(['--gpu-mode', str(msa_gpu_mode)])
    if msa_gpu_threshold is not None:
        cmd.extend(['--gpu-threshold', str(msa_gpu_threshold)])
    if msa_preferred_gpus:
        preferred = (
            ','.join(str(v) for v in msa_preferred_gpus if str(v).strip())
            if isinstance(msa_preferred_gpus, list)
            else str(msa_preferred_gpus).strip()
        )
        if preferred:
            cmd.extend(['--preferred-gpus', preferred])
    if msa_excluded_gpus:
        excluded = (
            ','.join(str(v) for v in msa_excluded_gpus if str(v).strip())
            if isinstance(msa_excluded_gpus, list)
            else str(msa_excluded_gpus).strip()
        )
        if excluded:
            cmd.extend(['--excluded-gpus', excluded])
    if msa_gpu_server_mode:
        cmd.extend(['--gpu-server-mode', str(msa_gpu_server_mode)])
    if msa_gpu_server_wait_timeout is not None:
        cmd.extend(['--gpu-server-wait-timeout', str(msa_gpu_server_wait_timeout)])
    if msa_gpu_server_db_load_mode is not None:
        cmd.extend(['--gpu-server-db-load-mode', str(msa_gpu_server_db_load_mode)])
    if msa_gpu_server_startup_wait is not None:
        cmd.extend(['--gpu-server-startup-wait', str(msa_gpu_server_startup_wait)])
    if msa_target_shard_mode:
        cmd.extend(['--target-shard-mode', str(msa_target_shard_mode)])
    if msa_target_shards is not None:
        cmd.extend(['--target-shards', str(max(1, int(msa_target_shards)))])
    if msa_target_shard_min_size_gb is not None:
        cmd.extend(['--target-shard-min-size-gb', str(msa_target_shard_min_size_gb)])
    return cmd


async def launch_msa_batch_job(
    job_id: str,
    params: Dict[str, Any],
    output_dir: str
) -> None:
    """
    Launch an MSA batch job using batch_msa.py directly.

    This runs the batch MSA script and then unlocks child inference jobs.
    """
    from database import async_session, Job
    from sqlalchemy import select
    from schemas import JobStatus

    logger.info(f"[MSA BATCH] Launching job {job_id}")

    async with async_session() as session:
        # Update job to running
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            logger.error(f"[MSA BATCH] Job {job_id} not found")
            return

        job.status = JobStatus.RUNNING.value
        job.queue_status = 'running'
        job.started_at = datetime.utcnow()
        await session.commit()

    # Get activity metadata from params for logging/touch_query_activity.
    raw_gpu_id = params.get('gpu_id')
    try:
        gpu_id = int(raw_gpu_id) if raw_gpu_id is not None else None
    except (TypeError, ValueError):
        gpu_id = None
    msa_use_gpu_raw = params.get('msa_use_gpu', True)
    if isinstance(msa_use_gpu_raw, str):
        msa_use_gpu = msa_use_gpu_raw.strip().lower() not in {"0", "false", "no", "off"}
    else:
        msa_use_gpu = bool(msa_use_gpu_raw)
    msa_preset = _normalize_msa_preset(params.get('msa_preset', 'fast'))

    # Build batch_msa.py command
    cmd = _build_msa_batch_command(params=params, output_dir=output_dir)
    
    logger.info(f"[MSA BATCH] Command: {' '.join(cmd[:6])}...")
    
    try:
        from services.msa_server import touch_query_activity
        touch_query_activity(
            {
                "event": "msa_batch_start",
                "job_id": job_id,
                "gpu_id": gpu_id if msa_use_gpu else None,
                "preset": msa_preset,
            }
        )

        # Run batch_msa.py
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        
        stdout, _ = await process.communicate()
        exit_code = process.returncode
        
        # Save log
        log_path = Path(output_dir) / "msa_batch.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, 'w') as f:
            f.write(stdout.decode() if stdout else "")
        
        async with async_session() as session:
            result = await session.execute(select(Job).where(Job.id == job_id))
            job = result.scalar_one_or_none()
            
            if exit_code == 0:
                job.status = JobStatus.COMPLETED.value
                job.queue_status = 'completed'
                job.completed_at = datetime.utcnow()
                job.msa_manifest_path = str(Path(output_dir) / "msa_manifest.json")
                logger.info(f"[MSA BATCH] Job {job_id} completed successfully")
                touch_query_activity(
                    {
                        "event": "msa_batch_complete",
                        "job_id": job_id,
                        "gpu_id": gpu_id if msa_use_gpu else None,
                        "preset": msa_preset,
                    }
                )
                
                # Unlock child inference jobs
                await session.commit()
                await unlock_child_inference_jobs(job_id, job.msa_manifest_path)
            else:
                job.status = JobStatus.FAILED.value
                job.queue_status = 'failed'
                job.error_message = f"MSA batch failed with exit code {exit_code}"
                logger.error(f"[MSA BATCH] Job {job_id} failed: exit code {exit_code}")
                touch_query_activity(
                    {
                        "event": "msa_batch_failed",
                        "job_id": job_id,
                        "gpu_id": gpu_id if msa_use_gpu else None,
                        "preset": msa_preset,
                        "exit_code": exit_code,
                    }
                )
                await session.commit()
    
    except Exception as e:
        logger.error(f"[MSA BATCH] Job {job_id} error: {e}")
        try:
            from services.msa_server import touch_query_activity
            touch_query_activity(
                {
                    "event": "msa_batch_error",
                    "job_id": job_id,
                    "gpu_id": gpu_id if msa_use_gpu else None,
                    "preset": msa_preset,
                    "error": str(e),
                }
            )
        except Exception:
            pass
        async with async_session() as session:
            result = await session.execute(select(Job).where(Job.id == job_id))
            job = result.scalar_one_or_none()
            if job:
                job.status = JobStatus.FAILED.value
                job.queue_status = 'failed'
                job.error_message = str(e)
                await session.commit()


async def unlock_child_inference_jobs(msa_job_id: str, manifest_path: str) -> None:
    """
    Unlock child inference jobs after MSA batch completes.
    
    Updates child jobs from 'pending_msa' to 'queued' status.
    """
    from database import async_session, Job
    from sqlalchemy import select
    import json
    
    logger.info(f"[MSA COMPLETE] Unlocking child jobs for MSA job {msa_job_id}")
    
    async with async_session() as session:
        # Get child jobs waiting for this MSA job
        result = await session.execute(
            select(Job).where(
                Job.parent_job_id == msa_job_id,
                Job.queue_status == "pending_msa"
            )
        )
        child_jobs = result.scalars().all()
        
        if not child_jobs:
            logger.info(f"[MSA COMPLETE] No child jobs found for {msa_job_id}")
            return
        
        # Parse manifest for MSA paths
        msa_paths = {}
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            for seq_info in manifest.get("sequences", []):
                if seq_info.get("success"):
                    msa_paths[seq_info.get("sequence_hash", "")] = seq_info.get("msa_path")
        except Exception as e:
            logger.warning(f"[MSA COMPLETE] Could not parse manifest: {e}")
        
        # Update each child job
        import hashlib
        for job in child_jobs:
            seq_hash = job.params.get("msa_sequence_hash")
            if not isinstance(seq_hash, str) or not seq_hash:
                sequence = job.params.get("sequence") or job.params.get("sequence_input") or ""
                ref_sequence = job.params.get("msa_reference_sequence") or ""
                hash_source = str(ref_sequence or sequence)
                seq_hash = hashlib.sha256(hash_source.encode()).hexdigest() if hash_source else ""
            msa_path = msa_paths.get(seq_hash)
            if msa_path:
                job.params = {**job.params, "msa_path": msa_path}
            job.queue_status = 'queued'  # Now ready for inference!
            logger.info(f"[MSA COMPLETE] Unlocked {job.name} for inference (MSA: {msa_path or 'not found'})")
        
        await session.commit()
        logger.info(f"[MSA COMPLETE] Unlocked {len(child_jobs)} inference jobs")



async def launch_nextflow_job(
    job_id: str,
    model_id: str,
    mode: str,
    params: Dict[str, Any],
    output_dir: str,
    allow_running_job: bool = False,
) -> None:
    """
    Launch a Nextflow pipeline job.
    
    This runs in a background task and updates the database with status.
    """
    assert_workflow_launch_allowed("launch workflow jobs")
    from database import async_session, Job
    from sqlalchemy import select
    from schemas import JobStatus
    
    logger.info(f"Launching job {job_id} (model={model_id}, mode={mode})")

    existing_process = _running_processes.get(job_id)
    if existing_process and existing_process.returncode is None:
        logger.warning(f"Skipping duplicate launch request for active job {job_id}")
        return
    
    # ═══════════════════════════════════════════════════════════════════════════
    # MSA BATCH JOBS: Run batch_msa.py directly (not Nextflow)
    # ═══════════════════════════════════════════════════════════════════════════
    if model_id == 'msa_batch':
        await launch_msa_batch_job(job_id, params, output_dir)
        return

    # Use a mutable launch-params copy so retries can downshift Protenix safely.
    launch_params: Dict[str, Any] = dict(params or {})

    structure_validator = str(launch_params.get("structure_validator", "")).strip().lower()
    uses_protenix_validation = _is_protenix_job(model_id, launch_params) or structure_validator == "protenix"
    use_protenix_msa = _coerce_bool(launch_params.get("protenix_use_msa", True), default=True)
    normalized_protenix_backend = _normalize_protenix_msa_backend(launch_params.get("protenix_msa_backend"))
    if uses_protenix_validation:
        if normalized_protenix_backend:
            launch_params["protenix_msa_backend"] = normalized_protenix_backend
        elif use_protenix_msa:
            launch_params["protenix_msa_backend"] = (
                "colabfold_api"
                if _normalize_msa_preset(launch_params.get("msa_preset", "fast")) == "maximum"
                else "auto"
            )

    launch_params, boltzgen_notes = await prepare_boltzgen_params_for_launch(launch_params)
    preflight_notes: List[str] = list(boltzgen_notes)
    is_protenix = _is_protenix_job(model_id, launch_params)
    if is_protenix:
        launch_params, preflight_notes = _apply_protenix_preflight(launch_params)
        if preflight_notes:
            logger.warning(
                f"[PROTENIX-GUARDRAIL] Preflight downshift applied for job {job_id}: "
                + " | ".join(preflight_notes)
            )
    
    async with async_session() as session:
        # Update job to running
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        
        if not job:
            logger.error(f"Job {job_id} not found in database")
            return

        if job.status == JobStatus.RUNNING.value and job.started_at is not None and not allow_running_job:
            logger.warning(f"Job {job_id} is already marked running; skipping duplicate launcher entry")
            return
        if job.status == JobStatus.RUNNING.value and job.started_at is not None and allow_running_job:
            logger.info(
                "Job %s is already marked running in the shared state store; continuing because this launch was explicitly handed off.",
                job_id,
            )
        
        # Check if job was cancelled while queued
        if job.status == JobStatus.CANCELLED.value:
            logger.info(f"Job {job_id} was cancelled before starting, aborting launch")
            return
        
        if job.status != JobStatus.RUNNING.value or job.started_at is None:
            job.status = JobStatus.RUNNING.value
            job.started_at = datetime.utcnow()
            await session.commit()
        
        # Re-check cancellation status right before spawning (minimize race window)
        await session.refresh(job)
        if job.status == JobStatus.CANCELLED.value:
            logger.info(f"Job {job_id} was cancelled just before spawn, aborting")
            return

        dynamic_gpu_cpus = await _resolve_dynamic_gpu_cpu_share(session, job, launch_params)
        if dynamic_gpu_cpus is not None:
            launch_params["cpus_per_gpu"] = dynamic_gpu_cpus
            logger.info(
                f"[CPU] Job {job_id} dynamic GPU CPU share set to {dynamic_gpu_cpus} "
                f"threads from pool {_dynamic_gpu_cpu_pool_threads()} "
                f"(batch={job.batch_id or 'none'})"
            )

        try:
            if workflow_adapter_enabled():
                adapter_response = launch_via_workflow_adapter(
                    job_id=job_id,
                    model_id=model_id,
                    mode=mode,
                    params=launch_params,
                    output_dir=output_dir,
                )
                if adapter_response.get("accepted") is False:
                    raise RuntimeError(
                        f"Workflow adapter rejected launch for job {job_id}: {adapter_response!r}"
                    )
                adapter_run_id = (
                    adapter_response.get("nextflow_run_id")
                    or adapter_response.get("run_id")
                    or adapter_response.get("job_id")
                    or job_id
                )
                job.nextflow_run_id = str(adapter_run_id)
                await session.commit()
                logger.info(
                    "[WORKFLOW-ADAPTER] Job %s delegated to host adapter with run id %s",
                    job_id,
                    job.nextflow_run_id,
                )
                return

            # ═══════════════════════════════════════════════════════════════
            # GPU ASSIGNMENT: Set CUDA_VISIBLE_DEVICES from orchestrator
            # ═══════════════════════════════════════════════════════════════
            # Extract gpu_id from params (set by orchestrator)
            gpu_id = launch_params.get('gpu_id')

            # Build environment with GPU pinning
            env = {**os.environ, "NXF_ANSI_LOG": "false"}
            env, java_notes = resolve_nextflow_java_env(env)
            java_ok, java_message = preflight_nextflow_java(env)
            for note in java_notes:
                logger.info("[NEXTFLOW-JAVA] %s", note)
            if java_ok:
                logger.info("[NEXTFLOW-JAVA] %s", java_message)
            else:
                logger.error("[NEXTFLOW-JAVA] %s", java_message)
                async with async_session() as fail_session:
                    result = await fail_session.execute(select(Job).where(Job.id == job_id))
                    failed_job = result.scalar_one_or_none()
                    if failed_job:
                        failed_job.status = JobStatus.FAILED.value
                        failed_job.queue_status = "failed"
                        failed_job.error_message = f"Nextflow Java preflight failed: {java_message}"
                        failed_job.completed_at = datetime.utcnow()
                        failed_job.assigned_gpu = None
                        await fail_session.commit()
                return
            gpu_id_str = None
            if gpu_id is not None:
                try:
                    gpu_id_str = str(int(gpu_id))
                except (TypeError, ValueError):
                    gpu_id_str = None
            if gpu_id_str is not None:
                env["CUDA_VISIBLE_DEVICES"] = gpu_id_str
                logger.info(f"[GPU] Job {job_id} pinned to GPU {gpu_id_str} via CUDA_VISIBLE_DEVICES")
            else:
                logger.warning(f"[GPU] Job {job_id} has no valid gpu_id - using default GPU selection")
            if is_protenix:
                # Reduces allocator fragmentation spikes on large pair/MSA tensors.
                env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
            
            # ═══════════════════════════════════════════════════════════════
            # PER-JOB SESSION ISOLATION (NXF_CACHE_DIR)
            # ═══════════════════════════════════════════════════════════════
            # Each job gets its own .nextflow cache directory so concurrent
            # runs and resumes never collide on LevelDB locks.
            # Ref: https://www.nextflow.io/docs/latest/reference/env-vars.html
            resume_source_dir = launch_params.get("resume_source_dir")
            if resume_source_dir:
                # Resume: use the ORIGINAL job's cache to find its session
                job_cache_dir = str(Path(resume_source_dir) / ".nextflow")
                logger.info(f"[JOB {job_id}] NXF_CACHE_DIR → original job cache: {job_cache_dir}")
            else:
                # Fresh run: create cache in this job's output dir
                job_cache_dir = str(Path(output_dir) / ".nextflow")
                logger.info(f"[JOB {job_id}] NXF_CACHE_DIR → {job_cache_dir}")
            env["NXF_CACHE_DIR"] = job_cache_dir
            
            # ═══════════════════════════════════════════════════════════════════
            # RUN NEXTFLOW + STREAM OUTPUT (with resume-lock retry hardening)
            # ═══════════════════════════════════════════════════════════════════
            full_log = []
            if preflight_notes:
                full_log.append(
                    "[PROTENIX-GUARDRAIL] Preflight downshift: " + " | ".join(preflight_notes) + "\n"
                )
            import re
            # Regex to capture process name: "[... ] process > PROCESS_NAME (tag) [ 10%]"
            # We want "PROCESS_NAME"
            process_regex = re.compile(r"process >\s+([^(\[]+)")
            resume_lock_pattern = "Unable to acquire lock on session with ID"

            # Respect global retry policy: if retries are disabled, fail fast to
            # surface chokepoints instead of auto-healing them.
            allow_retries_raw = launch_params.get("allow_retries", False)
            if isinstance(allow_retries_raw, str):
                allow_retries = allow_retries_raw.strip().lower() in {"1", "true", "yes", "on"}
            else:
                allow_retries = bool(allow_retries_raw)

            max_resume_lock_retries = 0
            if launch_params.get("resume_work_dir") and allow_retries:
                try:
                    max_resume_lock_retries = int(launch_params.get("resume_lock_retry_attempts", 2))
                except (TypeError, ValueError):
                    max_resume_lock_retries = 2
                max_resume_lock_retries = max(0, min(5, max_resume_lock_retries))

            max_protenix_oom_retries = 0
            if is_protenix and _coerce_bool(launch_params.get("protenix_auto_oom_retry", False), default=False):
                max_protenix_oom_retries = max(
                    0,
                    min(3, _coerce_int(launch_params.get("protenix_oom_retry_attempts", 2), 2)),
                )

            last_stage = None
            exit_code = 1
            resume_lock_retries_used = 0
            protenix_oom_retries_used = 0
            attempt = 1

            while True:
                cmd = build_nextflow_command(model_id, mode, launch_params, output_dir, job_id=job_id)
                logger.info(
                    f"[JOB {job_id}] Launch attempt {attempt} "
                    f"(resume_retries={resume_lock_retries_used}/{max_resume_lock_retries}, "
                    f"protenix_oom_retries={protenix_oom_retries_used}/{max_protenix_oom_retries})"
                )

                log_path = Path(output_dir) / "nextflow.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_offset = log_path.stat().st_size if log_path.exists() else 0
                pending_fragment = ""

                async def handle_log_line(line_str: str) -> None:
                    nonlocal last_stage
                    attempt_log.append(line_str)
                    full_log.append(line_str)

                    # Check for stage update
                    # Example: "[4b/123456] process > NF_CORE:FAMPNN (1) [ 0%]"
                    match = process_regex.search(line_str)
                    task_match = re.search(r'^\[([0-9a-f]{2})/([0-9a-f]+)\]\s+Submitted process >', line_str, re.IGNORECASE)
                    if match:
                        # Extract stage name (e.g. "NF_CORE:FAMPNN" or "FAMPNN")
                        raw_stage = match.group(1).strip()
                        # Clean up: Remove workflow prefix if present
                        stage_clean = raw_stage.split(':')[-1].lower()

                        # Map to frontend stage IDs
                        stage = stage_clean
                        if 'fampnn' in stage_clean:
                            stage = 'fampnn'
                        elif 'rfantibody' in stage_clean:
                            stage = 'rfantibody'
                        elif 'boltz' in stage_clean:
                            stage = 'boltz2' # Frontend uses boltz2
                        elif 'rf3' in stage_clean:
                            stage = 'rf3'
                        # ──────────────────────────────────────────────────────────────
                        # MPNN VARIANTS - Check specific variants BEFORE generic 'mpnn'
                        # Order matters: most specific first, generic last
                        # ──────────────────────────────────────────────────────────────
                        elif 'frustra' in stage_clean:
                            stage = 'frustrampnn'  # FrustraMPNN (frustration analysis)
                        elif 'ligandmpnn' in stage_clean or 'ligand_mpnn' in stage_clean:
                            stage = 'ligandmpnn'   # LigandMPNN (ligand-aware design)
                        elif 'thermompnn' in stage_clean or 'thermo_mpnn' in stage_clean:
                            stage = 'thermompnn'   # ThermoMPNN (thermal stability)
                        elif 'proteinmpnn' in stage_clean or ('mpnn' in stage_clean and 'fa' not in stage_clean):
                            stage = 'proteinmpnn'  # ProteinMPNN (vanilla)
                        elif 'protenix' in stage_clean:
                            stage = 'protenix'  # Protenix structure prediction
                        elif 'doradobasecall' in stage_clean:
                            stage = 'dorado_basecall'
                        elif 'doradoalign' in stage_clean:
                            stage = 'dorado_align'
                        elif (
                            'bamprepare' in stage_clean
                            or 'bam_prepare' in stage_clean
                            or 'preparebamforanalysis' in stage_clean
                            or 'bammappedcheck' in stage_clean
                            or 'bam_mapped_check' in stage_clean
                            or 'referenceprepareforigv' in stage_clean
                            or 'reference_prepare' in stage_clean
                        ):
                            stage = 'bam_prepare'
                        elif 'fastqalign' in stage_clean or 'fastq_align' in stage_clean:
                            stage = 'fastq_align'
                        elif (
                            'fastqplasmidqc' in stage_clean
                            or 'fastq_qc' in stage_clean
                            or 'fastqqc' in stage_clean
                        ):
                            stage = 'fastq_qc'
                        elif 'modkit' in stage_clean:
                            stage = 'modkit'
                        elif 'multimer' in stage_clean:
                            stage = 'multimer_qc'
                        elif 'fastqdimeranalysis' in stage_clean or 'dimeranalysis' in stage_clean:
                            stage = 'dimer_analysis'
                        elif (
                            'runclonevalidation' in stage_clean
                            or 'clonevalidation' in stage_clean
                            or 'clone-validation' in stage_clean
                            or 'wf_clone' in stage_clean
                            or 'wf-clone' in stage_clean
                        ):
                            stage = 'wf_clone_validation'
                        elif 'af2' in stage_clean:
                            stage = 'af2'
                        elif 'rfdiffusion' in stage_clean:
                            stage = 'rfdiffusion'

                        if stage != last_stage:
                            logger.info(f"[JOB {job_id}] Entering stage: {stage} (raw: {raw_stage})")
                            last_stage = stage

                            # Update DB (separate session to avoid long-held locks)
                            try:
                                async with async_session() as update_session:
                                    j_stats = await update_session.execute(select(Job).where(Job.id == job_id))
                                    j = j_stats.scalar_one_or_none()
                                    if j:
                                        j.current_stage = stage
                                        j.stage_progress = None  # Reset progress on new stage
                                        await update_session.commit()
                            except Exception as db_err:
                                logger.warning(f"Failed to update stage for {job_id}: {db_err}")

                        if task_match:
                            inferred_work_dir = infer_task_work_dir(task_match.group(1), task_match.group(2))
                            if inferred_work_dir:
                                try:
                                    async with async_session() as update_session:
                                        j_stats = await update_session.execute(select(Job).where(Job.id == job_id))
                                        j = j_stats.scalar_one_or_none()
                                        if j:
                                            j.stage_work_dir = inferred_work_dir
                                            progress = parse_stage_progress(
                                                inferred_work_dir,
                                                j.current_stage,
                                                launch_params.get('rfantibody_num_designs') or launch_params.get('num_designs')
                                            )
                                            if progress:
                                                j.stage_progress = progress
                                            await update_session.commit()
                                except Exception as db_err:
                                    logger.warning(f"Failed to infer work dir for {job_id}: {db_err}")

                    # Check for work directory in TaskHandler output
                    # Example: "workDir: /home/.../work/91/0cd0da..."
                    workdir_match = re.search(r'workDir:\s*(/[^\s\]]+)', line_str)
                    if workdir_match:
                        current_work_dir = workdir_match.group(1)

                        # Update work dir and parse progress
                        try:
                            async with async_session() as update_session:
                                j_stats = await update_session.execute(select(Job).where(Job.id == job_id))
                                j = j_stats.scalar_one_or_none()
                                if j:
                                    j.stage_work_dir = current_work_dir
                                    # Parse progress from the work dir log
                                    progress = parse_stage_progress(
                                        current_work_dir,
                                        j.current_stage,
                                        j.params.get('rfantibody_num_designs') if j.params else None
                                    )
                                    if progress:
                                        j.stage_progress = progress
                                    await update_session.commit()
                        except Exception as db_err:
                            logger.debug(f"Failed to update work dir for {job_id}: {db_err}")

                async def consume_new_log(final: bool = False) -> None:
                    nonlocal log_offset, pending_fragment
                    if not log_path.exists():
                        return
                    with open(log_path, "rb") as reader:
                        reader.seek(log_offset)
                        chunk = reader.read()
                    if chunk:
                        log_offset += len(chunk)
                        text = pending_fragment + chunk.decode("utf-8", errors="replace")
                    elif final and pending_fragment:
                        text = pending_fragment
                    else:
                        return

                    pending_fragment = ""
                    lines = text.splitlines(keepends=True)
                    if lines and not lines[-1].endswith(("\n", "\r")):
                        pending_fragment = lines.pop()

                    if final and pending_fragment:
                        lines.append(pending_fragment)
                        pending_fragment = ""

                    for line_str in lines:
                        await handle_log_line(line_str)

                with open(log_path, "ab", buffering=0) as log_sink:
                    # Launch in a new session and write directly to a durable log file so
                    # the workflow survives API reloads/restarts instead of depending on a pipe reader.
                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        cwd=str(PROJECT_ROOT),
                        stdout=log_sink,
                        stderr=asyncio.subprocess.STDOUT,
                        env=env,
                        close_fds=True,
                        start_new_session=True,
                    )
                # Store process reference for potential cancellation
                _running_processes[job_id] = process
                # Store the Nextflow run ID (PID for now)
                job.nextflow_run_id = str(process.pid)
                await session.commit()

                attempt_log: List[str] = []
                try:
                    while True:
                        try:
                            exit_code = await asyncio.wait_for(process.wait(), timeout=1.0)
                            await consume_new_log(final=True)
                            break
                        except asyncio.TimeoutError:
                            await consume_new_log()
                finally:
                    _running_processes.pop(job_id, None)

                lock_failed = (
                    exit_code != 0
                    and any(resume_lock_pattern in ln for ln in attempt_log)
                )
                if lock_failed and resume_lock_retries_used < max_resume_lock_retries:
                    resume_lock_retries_used += 1
                    _running_processes.pop(job_id, None)
                    sleep_s = min(20, 5 * resume_lock_retries_used)
                    msg = (
                        f"[BMS] Resume lock retry {resume_lock_retries_used}/{max_resume_lock_retries}; "
                        f"sleeping {sleep_s}s before relaunch."
                    )
                    logger.warning(msg)
                    full_log.append(msg + "\n")
                    await asyncio.sleep(sleep_s)
                    attempt += 1
                    continue

                protenix_oom_failed = (
                    is_protenix
                    and exit_code != 0
                    and _attempt_has_cuda_oom(attempt_log)
                )
                if protenix_oom_failed and protenix_oom_retries_used < max_protenix_oom_retries:
                    selected_changes: List[str] = []
                    while protenix_oom_retries_used < max_protenix_oom_retries:
                        next_rung = protenix_oom_retries_used + 1
                        tuned_params, downshift_changes = _apply_protenix_oom_retry_downshift(
                            launch_params, next_rung
                        )
                        protenix_oom_retries_used = next_rung
                        if downshift_changes:
                            launch_params = tuned_params
                            selected_changes = downshift_changes
                            break

                    if selected_changes:
                        _running_processes.pop(job_id, None)
                        msg = (
                            f"[PROTENIX-GUARDRAIL] OOM retry {protenix_oom_retries_used}/{max_protenix_oom_retries}: "
                            + " | ".join(selected_changes)
                        )
                        logger.warning(msg)
                        full_log.append(msg + "\n")
                        await asyncio.sleep(min(20, 3 * protenix_oom_retries_used))
                        attempt += 1
                        continue

                break
            
            # Save Nextflow execution log to output directory
            try:
                log_path = Path(output_dir) / "nextflow.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(log_path, "w") as f:
                    f.writelines(full_log)
            except Exception as log_err:
                logger.warning(f"Failed to save nextflow.log: {log_err}")
            
            # Update final status
            result = await session.execute(select(Job).where(Job.id == job_id))
            job = result.scalar_one_or_none()
            
            if job:
                # Refresh status to see if it was cancelled by API while we waited
                await session.refresh(job)
                
                if job.status == JobStatus.CANCELLED.value:
                    logger.info(f"Job {job_id} was cancelled, keeping CANCELLED status")
                    job.queue_status = 'cancelled'
                    
                else:
                    if exit_code == 0:
                        # Allow launcher finalization to heal stale reconciliations where
                        # the orchestrator may have prematurely marked this job complete.
                        if job.awaiting_input:
                            job.status = JobStatus.AWAITING_INPUT.value
                            job.queue_status = 'completed'
                            job.paused = False
                            job.assigned_gpu = None
                            job.current_stage = job.awaiting_stage or job.current_stage or "Awaiting Input"
                        else:
                            job.status = JobStatus.COMPLETED.value
                            job.queue_status = 'completed'
                            job.paused = False
                            job.assigned_gpu = None
                            job.current_stage = "Complete"
                        job.error_message = None
                        
                        # Ingest results into Design table
                        try:
                            from services.result_ingester import ingest_job_results
                            
                            # Extract epitope residues from job params for contact calculation
                            epitope_residues = None
                            if job.params:
                                # hotspot_residues format: "A111,A112,..." or already list
                                hotspots = job.params.get('hotspot_residues') or job.params.get('epitope_residues')
                                if hotspots:
                                    if isinstance(hotspots, str):
                                        epitope_residues = [r.strip() for r in hotspots.split(',')]
                                    elif isinstance(hotspots, list):
                                        epitope_residues = hotspots
                            
                            design_count = await ingest_job_results(
                                job_id, output_dir, session,
                                epitope_residues=epitope_residues
                            )
                            logger.info(f"Ingested {design_count} designs for job {job_id}")
                            from services.analysis_autorun import schedule_viewer_minimum_analyses_for_job

                            schedule_viewer_minimum_analyses_for_job(str(job.id))
                            # BATCH-STAGE-GATE: Check if all sibling variants complete, trigger batch FrustraMPNN
                            await maybe_trigger_batch_frustrampnn(job, session)
                            await maybe_trigger_mutation_seed_refinement(job, session)
                        except Exception as ingest_err:
                            logger.warning(f"Result ingestion failed: {ingest_err}")
                            
                    # Check for cancellation exit codes (SIGTERM=15/-15/143, SIGKILL=9/-9/137)
                    elif exit_code in (-15, -9, 143, 137):
                        job.status = JobStatus.CANCELLED.value
                        job.queue_status = 'cancelled'
                        job.error_message = "Job cancelled by user"
                        logger.info(f"Job {job_id} exit code {exit_code} interpreted as CANCELLED")
                        
                    else:
                        if job.status == JobStatus.COMPLETED.value or (job.current_stage or "").lower() == "complete":
                            logger.warning(
                                f"Nextflow process for job {job_id} exited with code {exit_code} after job was already finalized; preserving completed status"
                            )
                        else:
                            job.status = JobStatus.FAILED.value
                            job.queue_status = 'failed'
                        resume_lock_line = next(
                            (ln.strip() for ln in full_log if "Unable to acquire lock on session with ID" in ln),
                            None,
                        )
                        oom_line = next(
                            (ln.strip() for ln in full_log if "CUDA out of memory" in ln),
                            None,
                        )
                        # Check for zero-yield (HQ filter culled all designs)
                        zero_yield_report = Path(output_dir) / "zero_yield_report.json"
                        if zero_yield_report.exists():
                            import json as _json
                            try:
                                report_data = _json.loads(zero_yield_report.read_text())
                                reason = report_data.get("reason", "unknown")
                                recommendation = report_data.get("recommendation", "")
                                # FAIL LOUD: zero-yield is a real failure, not a silent completion
                                job.error_message = (
                                    f"ZERO YIELD: {reason}. "
                                    f"{recommendation}"
                                )
                                logger.warning(
                                    f"Job {job_id} FAILED zero-yield: {reason}"
                                )
                            except Exception:
                                job.error_message = "ZERO YIELD: 0 validated designs (see zero_yield_report.json)"
                        elif resume_lock_line:
                            job.error_message = (
                                f"Nextflow resume lock contention after retries: {resume_lock_line}"
                            )
                        elif oom_line:
                            stage = job.current_stage or "unknown-stage"
                            job.error_message = (
                                f"Nextflow {stage} failed with CUDA OOM: {oom_line[:400]}"
                            )
                        else:
                            job.error_message = f"Nextflow exited with code {exit_code}"
                        if job.status == JobStatus.FAILED.value:
                            logger.error(f"Nextflow failed for job {job_id} with code {exit_code}")
                        else:
                            job.error_message = None
                            logger.warning(
                                f"Nextflow exited with code {exit_code} for already-completed job {job_id}; status preserved"
                            )
                        
                        # Log last few lines
                        if job.status == JobStatus.FAILED.value:
                            logger.error(f"Tail of log:\n{''.join(full_log[-20:])}")
                
                job.completed_at = datetime.utcnow()
                await session.commit()
                _running_processes.pop(job_id, None)
                
        except Exception as e:
            logger.exception(f"Error running job {job_id}")
            _running_processes.pop(job_id, None)
            
            result = await session.execute(select(Job).where(Job.id == job_id))
            job = result.scalar_one_or_none()
            if job:
                # Don't overwrite if already cancelled
                await session.refresh(job)
                if job.status == JobStatus.COMPLETED.value or (job.current_stage or "").lower() == "complete":
                    logger.warning(
                        f"Detached runner caught exception for already-completed job {job_id}; preserving completed status"
                    )
                elif job.status != JobStatus.CANCELLED.value:
                    job.status = JobStatus.FAILED.value
                    job.queue_status = 'failed'  # Update queue_status so job leaves the queue UI
                    job.error_message = str(e)
                    job.completed_at = datetime.utcnow()
                    await session.commit()


def launch_nextflow_job_detached(
    *,
    job_id: str,
    model_id: str,
    mode: str,
    params: Dict[str, Any],
    output_dir: str,
    allow_running_job: bool = False,
) -> asyncio.Task:
    """
    Schedule a Nextflow launch in the background while marking the job as
    actively launching immediately.

    This closes the race where the orchestrator marks a job running before the
    launcher has created the subprocess or registered it in _running_processes.
    """
    assert_workflow_launch_allowed("launch workflow jobs from the scheduler")
    if job_id in _launching_jobs:
        logger.warning("Job %s is already queued for detached launch in this process; skipping duplicate scheduling", job_id)

        async def _noop() -> None:
            return None

        return asyncio.create_task(_noop())

    _launching_jobs.add(job_id)

    async def _runner() -> None:
        try:
            await launch_nextflow_job(
                job_id=job_id,
                model_id=model_id,
                mode=mode,
                params=params,
                output_dir=output_dir,
                allow_running_job=allow_running_job,
            )
        finally:
            _launching_jobs.discard(job_id)

    return asyncio.create_task(_runner())


def build_nextflow_command(
    model_id: str,
    mode: str,
    params: Dict[str, Any],
    output_dir: str,
    job_id: str = None
) -> list:
    """
    Build the Nextflow command line dynamically.
    
    Converts all params to --key value flags.
    """
    # Never mutate caller params; launch retries may reuse the same dict.
    params = dict(params or {})

    # DEBUG: Log all params to trace complex_components
    logger.info(f"build_nextflow_command received params keys: {list(params.keys())}")
    if 'complex_components' in params:
        logger.info(f"complex_components found with {len(params['complex_components'])} items")
    else:
        logger.debug("complex_components not present; treating launch as sequence-only unless model-specific preprocessing adds complex inputs")

    def resolve_structure_prediction_profile(pred_method: object) -> str:
        normalized = str(pred_method or 'boltz').strip().lower()
        if normalized == 'protenix':
            return 'protenix'
        if normalized == 'rf3':
            return 'rf3'
        return 'boltz'

    structure_prediction_profile = resolve_structure_prediction_profile(params.get('pred_method'))

    # Mode to profile mapping for modes that need translation
    mode_to_profile = {
        # structure_validation and structure_prediction use pred_method
        'structure_validation': structure_prediction_profile,
        'structure_prediction': structure_prediction_profile,
        # DNA polymerase template
        'dna_polymerase': 'fampnn_predict',
    }

    # Model + mode to profile mapping (for API-driven jobs)
    model_mode_to_profile = {
        ('boltz2', 'predict'): 'boltz',
        ('boltz2', 'complex'): 'boltz',
        ('rf3', 'predict'): 'rf3',
        ('af2', 'predict'): 'af2',
        # BindCraft API modes route to the binder profile; workflow is selected by product intent.
        ('bindcraft', 'minibinder'): 'binder_denovo',
        ('bindcraft', 'peptide'): 'binder_denovo',
        ('bindcraft', 'bindcraft_child'): 'binder_denovo',
        # Mutagenesis batch workflow - routes to boltz for structure prediction
        ('mutagenesis', 'batch_predict'): 'boltz',
        # Antibody workflows use boltz profile (Boltz2 is the structure predictor)
        ('antibody_denovo', ANTIBODY_DENOVO_PIPELINE): 'boltz',
        ('antibody_denovo', ANTIBODY_REFINEMENT_PIPELINE): 'boltz',
        ('antibody_denovo', 'default'): 'boltz',
        ('template_antibody_denovo', ANTIBODY_DENOVO_PIPELINE): 'boltz',
        ('template_antibody_denovo', ANTIBODY_REFINEMENT_PIPELINE): 'boltz',
        ('template_antibody_denovo', 'default'): 'boltz',
        # Batch validation jobs (spawned by antibody_denovo logic)
        ('antibody_child', 'validation_batch'): 'boltz',
        # RFantibody child jobs (backbone generation - spawned by orchestrator)
        ('rfantibody_child', 'antibody_backbone'): 'antibody_backbone',  # Uses antibody_backbone profile which sets rfd_mode correctly
        # FAMPNN child jobs (sequence design - spawned by orchestrator)
        ('fampnn_child', 'sequence_design'): 'fampnn_predict',
        # Oligo Designer (RFDpoly multi-polymer design)
        ('oligo_design', 'oligo_design'): 'oligo_design',
        # Protein local redesign with constrained RFD3 remodeling
        ('protein_local_redesign', 'local_redesign'): 'protein_local_redesign',
        ('protein_cad_experimental', 'design'): 'protein_cad_experimental',
        ('caliby_experimental', 'design'): 'caliby_experimental',
        ('protein_hunter_experimental', 'design'): 'protein_hunter_experimental',
        ('boltz_cp_experimental', 'design'): 'boltz_cp_experimental',
        ('confornets_experimental', 'design'): 'confornets_experimental',
        ('esmfold2_experimental', 'predict'): 'esmfold2_experimental',
        # Nanopore/ONT product workflows: live device control is outside Nextflow;
        # these modes analyze existing POD5/FAST5/FASTQ/BAM outputs.
        ('nanopore', 'basecall_dna'): 'ont_basecall_dna',
        ('nanopore', 'basecall_rna'): 'ont_basecall_rna',
        ('nanopore', 'plasmid_qc'): 'ont_plasmid_qc',
        ('nanopore', 'construct_screening'): 'ont_construct_screening',
        ('nanopore', 'methylation_analysis'): 'ont_methylation_analysis',
        ('nanopore', 'fastq_qc'): 'ont_fastq_qc',
        ('nanopore', 'nanopore_methylation'): 'ont_methylation_analysis',
        # Protenix structure prediction
        ('protenix', 'predict'): 'protenix',
        ('protenix', 'complex'): 'protenix',
        # Seeded PPIFlow generator
        ('ppiflow', 'generator_backbone_refine'): 'boltz',
    }

    def resolve_antibody_validation_profile(default_profile: str) -> str:
        antibody_modes = {
            ('antibody_denovo', ANTIBODY_DENOVO_PIPELINE),
            ('antibody_denovo', ANTIBODY_REFINEMENT_PIPELINE),
            ('antibody_denovo', 'default'),
            ('template_antibody_denovo', ANTIBODY_DENOVO_PIPELINE),
            ('template_antibody_denovo', ANTIBODY_REFINEMENT_PIPELINE),
            ('template_antibody_denovo', 'default'),
            ('antibody_child', 'validation_batch'),
        }
        if (model_id, mode) not in antibody_modes:
            return default_profile

        validator = str(
            params.get('structure_validator')
            or params.get('validation_predictor')
            or params.get('pred_method')
            or 'boltz2'
        ).strip().lower()
        if validator == 'boltz':
            validator = 'boltz2'
        return 'protenix' if validator == 'protenix' else 'boltz'
    
    # Determine profile based on model and mode
    if (model_id, mode) in model_mode_to_profile:
        effective_profile = model_mode_to_profile[(model_id, mode)]
    elif mode in mode_to_profile:
        effective_profile = mode_to_profile[mode]
    else:
        effective_profile = mode

    effective_profile = resolve_antibody_validation_profile(effective_profile)

    if model_id == 'nanopore' or str(effective_profile).startswith('ont_') or effective_profile == 'nanopore_methylation':
        effective_profile = resolve_ont_workflow_alias(effective_profile)
        params = normalize_ont_launch_params(effective_profile, params)
    
    # Handle GPU priority forcing
    gpu_priority = params.get('gpu_priority', 'auto')
    
    profile = f"{effective_profile},workstation_ryzen7960x"
    
    # Special case: DiffDock standalone docking uses 'docking' profile
    if model_id == 'diffdock' and mode in ['dock', 'ntp_dock']:
        profile = "docking,workstation_ryzen7960x"
    
    # Special case: Uni-Dock standalone docking uses 'unidock' profile
    if model_id == 'unidock' and mode in ['dock', 'ntp_dock']:
        profile = "unidock,workstation_ryzen7960x"
    
    # Special case: Dual docking mode (both DiffDock and Uni-Dock)
    if model_id == 'docking' and mode in ['compare', 'consensus']:
        profile = "dual_docking,workstation_ryzen7960x"
    
    # Special case: BoltzGen standalone uses 'boltzgen' profile
    if model_id == 'boltzgen':
        profile = "boltzgen,workstation_ryzen7960x"

    workflow_entrypoint = resolve_nextflow_entrypoint(
        effective_profile=effective_profile,
        model_id=model_id,
        mode=mode,
        params=params,
    )

    explicit_data_root = params.get("data_root")
    if not explicit_data_root:
        env_data_root = os.getenv("BMS_DATA")
        if env_data_root:
            explicit_data_root = env_data_root
        else:
            out_path = Path(output_dir).expanduser()
            for candidate in [out_path] + list(out_path.parents):
                if candidate.name == "bms_results":
                    explicit_data_root = str(candidate.parent)
                    break
    if not explicit_data_root:
        explicit_data_root = str(get_data_root())

    explicit_code_root = params.get("code_root") or os.getenv("BMS_HOME") or str(get_code_root())
    explicit_weights_root = params.get("weights_root") or os.getenv("BMS_WEIGHTS") or str(get_weights_root())
    explicit_msa_db = params.get("msa_local_db") or os.getenv("BMS_COLABFOLD_DB") or str(get_colabfold_db())
    explicit_msa_cache = params.get("msa_cache_dir") or os.getenv("BMS_MSA_CACHE") or str(get_msa_cache_dir())
    explicit_work_dir = params.get("work_dir") or os.getenv("BMS_WORK") or str(get_work_dir())
    explicit_container_dir = (
        params.get("container_dir")
        or os.getenv("BMS_CONTAINER_DIR")
        or str(Path(explicit_data_root) / "apptainer")
    )
    explicit_rfd_models = params.get("rfd_models") or os.getenv("BMS_RFD_MODELS") or str(get_rfd_models_dir())
    explicit_af2_models = params.get("af2_models") or os.getenv("BMS_AF2_MODELS") or str(Path(explicit_weights_root) / "alphafold" / "params")
    explicit_boltz_models = params.get("boltz_models") or os.getenv("BMS_BOLTZ_MODELS") or str(Path(explicit_weights_root) / "boltz")
    explicit_alphafold_params = params.get("alphafold_params") or str(Path(explicit_weights_root) / "alphafold" / "params")

    
    # Base command
    # Base command logic with Resumption support
    resume_work_dir = params.get('resume_work_dir')
    if resume_work_dir:
        logger.info(f"Resuming job using work dir: {resume_work_dir}")
        cmd = [
            "nextflow", "run", workflow_entrypoint,
            "-profile", profile,
            "-w", resume_work_dir,
            "-resume",
            "--out_dir", output_dir,
        ]
        # Log the resume_source_dir if set (for NXF_CACHE_DIR tracing)
        if params.get('resume_source_dir'):
            logger.info(f"Resume cache source: {params['resume_source_dir']}")
    else:
        cmd = [
            "nextflow", "run", workflow_entrypoint,
            "-profile", profile,
            "-w", str(explicit_work_dir),
            "--out_dir", output_dir,
        ]
    
    # Add job_id for spawn-wait-collect tracking
    if job_id:
        cmd.extend(["--job_id", job_id])

    # Force core path params so moved data/model drives are always honored.
    # Only apply defaults when caller didn't explicitly provide a value.
    explicit_path_defaults = {
        "code_root": explicit_code_root,
        "data_root": explicit_data_root,
        "weights_root": explicit_weights_root,
        "msa_local_db": explicit_msa_db,
        "msa_cache_dir": explicit_msa_cache,
        "container_dir": explicit_container_dir,
        "rfd_models": explicit_rfd_models,
        "af2_models": explicit_af2_models,
        "boltz_models": explicit_boltz_models,
        "alphafold_params": explicit_alphafold_params,
    }
    for key, value in explicit_path_defaults.items():
        if params.get(key) in (None, ""):
            cmd.extend([f"--{key}", str(value)])

    # Inject MSA GPU policy defaults when caller did not explicitly specify them.
    # Precedence:
    # 1) job params
    # 2) persisted MSA Server Settings GPU pin
    # 3) scheduler global MSA preference list
    try:
        from services.gpu_config import read_scheduler_config
        from services.msa_server import read_server_settings

        scheduler_cfg = read_scheduler_config() or {}
        global_cfg = scheduler_cfg.get("global", {}) if isinstance(scheduler_cfg, dict) else {}
        overrides_cfg = scheduler_cfg.get("overrides", {}) if isinstance(scheduler_cfg, dict) else {}
        msa_server_settings = read_server_settings() or {}
        skip_persisted_msa_pin = (
            str(params.get("structure_validator", "")).strip().lower() == "protenix"
            and str(params.get("protenix_msa_backend", "auto")).strip().lower() == "local"
        )

        if params.get("msa_preferred_gpus") in (None, ""):
            preferred_ids = []
            preferred_source = None
            raw_pinned_gpu = None if skip_persisted_msa_pin else msa_server_settings.get("pinned_gpu_id")
            if raw_pinned_gpu not in (None, ""):
                try:
                    preferred_ids = [int(raw_pinned_gpu)]
                except (TypeError, ValueError):
                    preferred_ids = []
                if preferred_ids:
                    preferred_source = "persisted MSA server settings"
            elif skip_persisted_msa_pin:
                logger.info("[MSA] Skipping persisted MSA GPU pin for Protenix local MSA; allowing normal GPU selection")
            if not preferred_ids:
                raw_preferred = global_cfg.get("msa_preferred_gpu_ids")
                seen_preferred = set()
                if isinstance(raw_preferred, list):
                    for gpu_id in raw_preferred:
                        try:
                            normalized_id = int(gpu_id)
                        except (TypeError, ValueError):
                            continue
                        if normalized_id in seen_preferred:
                            continue
                        seen_preferred.add(normalized_id)
                        preferred_ids.append(normalized_id)
                if preferred_ids:
                    preferred_source = "scheduler config"
            if preferred_ids:
                params["msa_preferred_gpus"] = preferred_ids
                logger.info(f"[MSA] Injected preferred GPUs from {preferred_source}: {params['msa_preferred_gpus']}")

        if params.get("msa_excluded_gpus") in (None, ""):
            excluded_ids = []
            if isinstance(overrides_cfg, dict):
                for gpu_key, override in overrides_cfg.items():
                    if not isinstance(override, dict) or not override.get("disabled", False):
                        continue
                    try:
                        excluded_ids.append(int(gpu_key))
                    except (TypeError, ValueError):
                        continue
            if excluded_ids:
                params["msa_excluded_gpus"] = sorted(set(excluded_ids))
                logger.info(f"[MSA] Injected excluded GPUs from scheduler config: {params['msa_excluded_gpus']}")

        preferred_ids = params.get("msa_preferred_gpus")
        excluded_ids = params.get("msa_excluded_gpus")
        if preferred_ids not in (None, "") and excluded_ids not in (None, ""):
            preferred_set = {
                int(gpu_id)
                for gpu_id in (preferred_ids if isinstance(preferred_ids, list) else str(preferred_ids).split(","))
                if str(gpu_id).strip()
            }
            excluded_set = {
                int(gpu_id)
                for gpu_id in (excluded_ids if isinstance(excluded_ids, list) else str(excluded_ids).split(","))
                if str(gpu_id).strip()
            }
            overlap = preferred_set & excluded_set
            if overlap:
                preferred_set -= overlap
                if preferred_set:
                    params["msa_preferred_gpus"] = sorted(preferred_set)
                else:
                    params.pop("msa_preferred_gpus", None)
                logger.info(
                    f"[MSA] Removed overlapping preferred/excluded GPU IDs {sorted(overlap)}; "
                    f"preferred now {params.get('msa_preferred_gpus')}"
                )
    except Exception as exc:
        logger.warning(f"[MSA] Could not load scheduler GPU policy defaults: {exc}")

    try:
        from services.anarcii_runtime import (
            get_default_anarcii_mode,
            resolve_anarcii_runtime,
        )

        requested_anarcii_mode = params.get("anarcii_execution_mode") or get_default_anarcii_mode()
        anarcii_runtime = resolve_anarcii_runtime(
            requested_mode=requested_anarcii_mode,
            preferred_gpu=params.get("anarcii_gpu_id"),
            excluded_gpu_ids=params.get("msa_excluded_gpus"),
        )
        params["anarcii_execution_mode"] = anarcii_runtime.mode
        if anarcii_runtime.gpu_id is None:
            params.pop("anarcii_gpu_id", None)
        else:
            params["anarcii_gpu_id"] = anarcii_runtime.gpu_id
        logger.info(
            "[ANARCII] Launch runtime=%s gpu=%s (%s)",
            anarcii_runtime.mode,
            anarcii_runtime.gpu_id,
            anarcii_runtime.reason,
        )
    except Exception as exc:
        logger.warning(f"[ANARCII] Could not resolve runtime defaults: {exc}")
    
    # Map model-specific params to Nextflow params
    param_mapping = {
        # DiffDock param mapping
        'protein_pdb': 'skip_input_dir',
        'ligand_smiles': 'diffdock_ligand_smiles',
        'ligand_sdf': 'diffdock_ligand_sdf',
        'ntp_type': 'diffdock_ntp_type',
        'num_poses': 'diffdock_num_poses',
        'confidence_threshold': 'diffdock_confidence_threshold',
        # Uni-Dock param mapping
        'unidock_ligand_smiles': 'unidock_ligand_smiles',
        'unidock_ntp_type': 'unidock_ntp_type',
        'unidock_num_poses': 'unidock_num_poses',
        'unidock_exhaustiveness': 'unidock_exhaustiveness',
        'unidock_scoring': 'unidock_scoring',
        'unidock_box_size': 'unidock_box_size',
        'unidock_box_center': 'unidock_box_center',
        'unidock_flexible_residues': 'unidock_flexible_residues',
        'unidock_affinity_threshold': 'unidock_affinity_threshold',
        'exhaustiveness': 'unidock_exhaustiveness',  # Alias from YAML
        'scoring_function': 'unidock_scoring',  # Alias from YAML
        'box_size': 'unidock_box_size',  # Alias from YAML
        'box_center': 'unidock_box_center',  # Alias from YAML
        'flexible_residues': 'unidock_flexible_residues',  # Alias from YAML
        'affinity_threshold': 'unidock_affinity_threshold',  # Alias from YAML
        'search_mode': 'unidock_search_mode',  # Alias from YAML
        'min_rmsd': 'unidock_min_rmsd',  # Alias from YAML
        'energy_range': 'unidock_energy_range',  # Alias from YAML
        'seed': 'unidock_seed',  # Alias from YAML
        # NOTE: BoltzGen-specific mappings (target_pdb, ligand_description, etc.)
        # are applied conditionally below for model_id == 'boltzgen' only.
        # They were previously here and broke other workflows!
        # Boltz-2 structure prediction params
        'boltz_recycling_steps': 'boltz_recycling_steps',
        'boltz_sampling_steps': 'boltz_sampling_steps',
        'boltz_num_samples': 'boltz_num_samples',
        'boltz_diffusion_samples': 'boltz_diffusion_samples',  # Alias for boltz_num_samples
        'boltz_max_parallel_samples': 'boltz_max_parallel_samples',
        'boltz_use_msa': 'boltz_use_msa',
        'boltz_method': 'boltz_method',
        'boltz_use_potentials': 'boltz_use_potentials',
        'boltz_step_scale': 'boltz_step_scale',
        'boltz_anchor_target': 'boltz_anchor_target',
        'boltz_anchor_strict': 'boltz_anchor_strict',
        'boltz_target_geometry_mode': 'boltz_target_geometry_mode',
        # Boltz-2 affinity prediction (quality feature)
        'boltz_predict_affinity': 'boltz_predict_affinity',
        'boltz_sampling_steps_affinity': 'boltz_sampling_steps_affinity',
        'boltz_diffusion_samples_affinity': 'boltz_diffusion_samples_affinity',
        'boltz_affinity_mw_correction': 'boltz_affinity_mw_correction',
        # RF3 structure prediction params
        'rf3_num_recycles': 'rf3_num_recycles',
        'rf3_num_samples': 'rf3_num_samples',
        'rf3_early_stopping_plddt': 'rf3_early_stopping_plddt',
        # Protenix structure prediction params
        'protenix_model_weights': 'protenix_model_weights',
        'protenix_seeds': 'protenix_seeds',
        'protenix_n_sample': 'protenix_n_sample',
        'protenix_n_step': 'protenix_n_step',
        'protenix_n_cycle': 'protenix_n_cycle',
        'protenix_use_msa': 'protenix_use_msa',
        'protenix_msa_backend': 'protenix_msa_backend',
        'protenix_use_template': 'protenix_use_template',
        'protenix_anchor_target': 'protenix_anchor_target',
        'protenix_anchor_strict': 'protenix_anchor_strict',
        'protenix_target_geometry_mode': 'protenix_target_geometry_mode',
        'protenix_enable_cache': 'protenix_enable_cache',
        'protenix_enable_fusion': 'protenix_enable_fusion',
        'protenix_auto_oom_retry': 'protenix_auto_oom_retry',
        'protenix_oom_retry_attempts': 'protenix_oom_retry_attempts',
        # Sequence input
        'sequence': 'sequence_input',
        'sequence_name': 'sequence_name',
        # Parallelization
        'num_parallel_jobs': 'num_parallel_jobs',
        # Target complex prediction (optional upstream for antibody design)
        'target_protein_seq': 'target_protein_seq',
        'target_dna_seq': 'target_dna_seq',
        # MSA Quality Parameters (passed through to BoltzFromComplex/GenerateLocalMSA)
        'msa_preset': 'msa_preset',
        'msa_taxon_list': 'msa_taxon_list',
        'msa_evalue': 'msa_evalue',
        'msa_min_seq_id': 'msa_min_seq_id',
        'msa_min_coverage': 'msa_min_coverage',
        'msa_min_depth_warning': 'msa_min_depth_warning',
        'msa_min_depth_fail': 'msa_min_depth_fail',
        'msa_allow_empty_fallback': 'msa_allow_empty_fallback',
        'msa_force_refresh': 'msa_force_refresh',
        'msa_cache_only': 'msa_cache_only',
        'msa_provider': 'msa_provider',
        'msa_use_gpu': 'msa_use_gpu',
        'msa_local_db': 'msa_local_db',
        'msa_cache_dir': 'msa_cache_dir',
        'msa_threads': 'msa_threads',
        'colabfold_api_host': 'colabfold_api_host',
        'colabfold_api_min_interval': 'colabfold_api_min_interval',
        'colabfold_api_poll_interval': 'colabfold_api_poll_interval',
        'msa_gpu_mode': 'msa_gpu_mode',
        'msa_gpu_threshold': 'msa_gpu_threshold',
        'msa_preferred_gpus': 'msa_preferred_gpus',
        'msa_excluded_gpus': 'msa_excluded_gpus',
        'msa_gpu_server_mode': 'msa_gpu_server_mode',
        'msa_gpu_server_wait_timeout': 'msa_gpu_server_wait_timeout',
        'msa_gpu_server_db_load_mode': 'msa_gpu_server_db_load_mode',
        'msa_gpu_server_startup_wait': 'msa_gpu_server_startup_wait',
        'lock_target_chains': 'lock_target_chains',
        'lock_antibody_framework': 'lock_antibody_framework',
        'target_geometry_mode': 'target_geometry_mode',
        'target_template_threshold_angstrom': 'target_template_threshold_angstrom',
        'strict_target_rmsd': 'strict_target_rmsd',
        # NA-MPNN sequence design params (Oligo Designer)
        'nampnn_temperature': 'nampnn_temperature',
        'nampnn_num_seqs': 'nampnn_num_seqs',
        'nampnn_fixed_residues': 'nampnn_fixed_residues',
        'nampnn_chains_to_design': 'nampnn_chains_to_design',
        'nampnn_design_na_only': 'nampnn_design_na_only',
        'nampnn_seed': 'nampnn_seed',
    }
    
    # Handle complex_components specially - write JSON file for BoltzFromComplex process
    complex_components = params.pop('complex_components', None)
    sequence_batch_json_path, complex_batch_dir, complex_components = _write_sequence_batch_payloads(
        output_dir=output_dir,
        params=params,
        complex_components=complex_components,
    )
    
    # Model-specific param preprocessing: Route ntp_type and ligand_smiles to correct targets
    if model_id == 'unidock':
        params.setdefault('docking_engine', 'unidock')
        # For Uni-Dock: ntp_type -> unidock_ntp_type, ligand_smiles -> unidock_ligand_smiles
        if 'ntp_type' in params:
            params['unidock_ntp_type'] = params.pop('ntp_type')
        if 'ligand_smiles' in params and 'unidock_ligand_smiles' not in params:
            params['unidock_ligand_smiles'] = params.pop('ligand_smiles')
    elif model_id == 'diffdock':
        params.setdefault('docking_engine', 'diffdock')
        # For DiffDock: ntp_type -> diffdock_ntp_type
        if 'ntp_type' in params:
            params['diffdock_ntp_type'] = params.pop('ntp_type')
    elif model_id == 'docking':
        params.setdefault('docking_engine', 'dual')
    elif model_id == 'boltzgen':
        # For BoltzGen: Apply all BoltzGen-specific parameter mappings
        # These were previously in global param_mapping and broke other workflows!
        if 'boltzgen_binding_site_residues' not in params:
            site_alias = (
                params.get('binding_site_residues')
                or params.get('epitope_residues')
                or params.get('selected_residues')
            )
            if site_alias:
                params['boltzgen_binding_site_residues'] = site_alias
        boltzgen_mappings = {
            # Schema-native keys
            'target_pdb': 'boltzgen_target_pdb_path',
            'input_pdb': 'boltzgen_input_pdb',
            'ligand_pdb': 'boltzgen_ligand_pdb',
            'ligand_smiles': 'boltzgen_ligand_smiles',
            'ligand_description': 'boltzgen_ligand_smiles',
            'protein_sequence': 'boltzgen_protein_sequence',
            'dna_template_seq': 'boltzgen_dna_template_seq',
            'dna_primer_seq': 'boltzgen_dna_primer_seq',
            'dna_structure': 'boltzgen_dna_structure',
            'scaffold_length': 'boltzgen_scaffold_length',
            'num_designs': 'boltzgen_num_designs',
            'batch_size': 'boltzgen_batch_size',
            'ntp_type': 'boltzgen_ntp_type',
            'binding_site_residues': 'boltzgen_binding_site_residues',
            'catalytic_site': 'boltzgen_catalytic_site',
            # Filtering and protocol aliases
            'budget': 'boltzgen_budget',
            'alpha': 'boltzgen_alpha',
            'max_rmsd': 'boltzgen_max_rmsd',
            'min_plddt': 'boltzgen_min_plddt',
            'secondary_structure': 'boltzgen_secondary_structure',
            'protocol': 'boltzgen_protocol',
        }
        for src_key, dest_key in boltzgen_mappings.items():
            if src_key in params:
                params[dest_key] = params.pop(src_key)
    elif model_id == 'ppiflow':
        if not params.get('rfd_mode'):
            params['rfd_mode'] = 'ppiflow_generator'
        params.setdefault('stage_family', 'ppiflow')
        params.setdefault('stage_mode', 'generator_backbone_refine')
    elif model_id == 'protein_local_redesign':
        protein_local_mappings = {
            'input_pdb': 'plr_input_pdb',
            'model_number': 'plr_model_number',
            'design_chains': 'plr_design_chains',
            'context_chains': 'plr_context_chains',
            'region_mode': 'plr_region_mode',
            'redesign_ranges': 'plr_redesign_ranges',
            'interface_cutoff': 'plr_interface_cutoff',
            'region_padding': 'plr_region_padding',
            'num_designs': 'plr_num_designs',
            'seq_method': 'plr_seq_method',
            'fix_fixed_sidechains': 'plr_fix_fixed_sidechains',
            'run_boltz_validation': 'plr_run_boltz_validation',
            'interactive_gating': 'interactive_gating',
            'interactive_gate_stage': 'interactive_gate_stage',
            'interactive_gate_continue': 'interactive_gate_continue',
            'backbone_input_pdbs': 'plr_backbone_input_pdbs',
            'sequence_input_pdbs': 'plr_sequence_input_pdbs',
            'validation_input_pdbs': 'plr_validation_input_pdbs',
            'region_manifest': 'plr_region_manifest',
            'final_candidate_dir': 'plr_final_candidate_dir',
        }
        for src_key, dest_key in protein_local_mappings.items():
            if src_key == dest_key:
                continue
            if src_key in params:
                if dest_key not in params:
                    params[dest_key] = params[src_key]
                params.pop(src_key, None)

        if 'plr_num_designs' in params and 'rfd_num_designs' not in params:
            params['rfd_num_designs'] = params['plr_num_designs']
        if 'plr_seq_method' in params and 'seq_method' not in params:
            params['seq_method'] = params['plr_seq_method']
        if not params.get('rfd_mode'):
            params['rfd_mode'] = 'protein_local_redesign'
    elif model_id == 'protein_cad_experimental':
        protein_cad_mappings = {
            'backend': 'pcad_backend',
            'design_task': 'pcad_task',
            'num_designs': 'pcad_num_designs',
            'target_lengths': 'pcad_target_lengths',
            'laproteina_preset': 'pcad_laproteina_preset',
            'laproteina_samples_per_length': 'pcad_laproteina_samples_per_length',
            'laproteina_num_steps': 'pcad_laproteina_num_steps',
            'laproteina_motif_task_name': 'pcad_laproteina_motif_task_name',
            'laproteina_motif_pdb': 'pcad_laproteina_motif_pdb',
            'laproteina_contig_string': 'pcad_laproteina_contig_string',
            'laproteina_segment_order': 'pcad_laproteina_segment_order',
            'laproteina_atom_selection_mode': 'pcad_laproteina_atom_selection_mode',
            'laproteina_motif_min_length': 'pcad_laproteina_motif_min_length',
            'laproteina_motif_max_length': 'pcad_laproteina_motif_max_length',
            'laproteina_checkpoint_dir': 'pcad_laproteina_checkpoint_dir',
            'laproteina_data_path': 'pcad_laproteina_data_path',
            'disco_experiment': 'pcad_disco_experiment',
            'disco_effort': 'pcad_disco_effort',
            'disco_num_inference_seeds': 'pcad_disco_num_inference_seeds',
            'disco_seeds': 'pcad_disco_seeds',
            'disco_input_json_path': 'pcad_disco_input_json_path',
            'disco_ligand_sdf': 'pcad_disco_ligand_sdf',
            'disco_ligand_name': 'pcad_disco_ligand_name',
            'disco_na_sequence': 'pcad_disco_na_sequence',
            'disco_checkpoint_path': 'pcad_disco_checkpoint_path',
            'disco_use_deepspeed_evo_attention': 'pcad_disco_use_deepspeed_evo_attention',
            'disco_cutlass_path': 'pcad_disco_cutlass_path',
        }
        for src_key, dest_key in protein_cad_mappings.items():
            if src_key == dest_key:
                continue
            if src_key in params:
                if dest_key not in params:
                    params[dest_key] = params[src_key]
                params.pop(src_key, None)

        if 'pcad_num_designs' in params and 'rfd_num_designs' not in params:
            params['rfd_num_designs'] = params['pcad_num_designs']
        if not params.get('rfd_mode'):
            params['rfd_mode'] = 'protein_cad_experimental'
    elif model_id == 'caliby_experimental':
        caliby_mappings = {
            'task': 'caliby_task',
            'input_pdb_dir': 'caliby_input_pdb_dir',
            'conformer_dir': 'caliby_conformer_dir',
            'pdb_name_list': 'caliby_pdb_name_list',
            'pos_constraint_csv': 'caliby_pos_constraint_csv',
            'model_name': 'caliby_model_name',
            'packer_model_name': 'caliby_packer_model_name',
            'num_seqs_per_pdb': 'caliby_num_seqs_per_pdb',
            'batch_size': 'caliby_batch_size',
            'num_workers': 'caliby_num_workers',
            'clean_num_workers': 'caliby_clean_num_workers',
            'temperature': 'caliby_temperature',
            'omit_aas': 'caliby_omit_aas',
            'run_self_consistency_eval': 'caliby_run_self_consistency_eval',
            'self_consistency_num_models': 'caliby_self_consistency_num_models',
            'self_consistency_num_recycles': 'caliby_self_consistency_num_recycles',
            'self_consistency_use_multimer': 'caliby_self_consistency_use_multimer',
            'sampling_overrides_json': 'caliby_sampling_overrides_json',
        }
        for src_key, dest_key in caliby_mappings.items():
            if src_key == dest_key:
                continue
            if src_key in params:
                if dest_key not in params:
                    params[dest_key] = params[src_key]
                params.pop(src_key, None)

        if 'caliby_num_seqs_per_pdb' in params and 'rfd_num_designs' not in params:
            params['rfd_num_designs'] = params['caliby_num_seqs_per_pdb']
        if not params.get('rfd_mode'):
            params['rfd_mode'] = 'caliby_experimental'
    elif model_id == 'protein_hunter_experimental':
        protein_hunter_mappings = {
            'backend': 'ph_backend',
            'task': 'ph_task',
            'num_designs': 'ph_num_designs',
            'num_cycles': 'ph_num_cycles',
            'min_protein_length': 'ph_min_protein_length',
            'max_protein_length': 'ph_max_protein_length',
            'percent_x': 'ph_percent_x',
            'seed_binder_sequence': 'ph_seed_binder_sequence',
            'target_protein_sequences': 'ph_target_protein_sequences',
            'target_pdb': 'ph_target_pdb',
            'target_pdb_chain': 'ph_target_pdb_chain',
            'target_template_path': 'ph_target_template_path',
            'target_template_chain_id': 'ph_target_template_chain_id',
            'ligand_smiles': 'ph_ligand_smiles',
            'ligand_ccd': 'ph_ligand_ccd',
            'nucleic_sequence': 'ph_nucleic_sequence',
            'nucleic_type': 'ph_nucleic_type',
            'contact_residues': 'ph_contact_residues',
            'cyclic': 'ph_cyclic',
            'alanine_bias': 'ph_alanine_bias',
            'temperature': 'ph_temperature',
            'high_iptm_threshold': 'ph_high_iptm_threshold',
            'high_plddt_threshold': 'ph_high_plddt_threshold',
            'msa_mode': 'ph_msa_mode',
            'boltz_model_version': 'ph_boltz_model_version',
            'boltz_model_path': 'ph_boltz_model_path',
            'boltz_ccd_path': 'ph_boltz_ccd_path',
            'chai_hysteresis_mode': 'ph_chai_hysteresis_mode',
            'chai_num_recycles': 'ph_chai_num_recycles',
            'chai_num_diff_steps': 'ph_chai_num_diff_steps',
            'chai_repredict': 'ph_chai_repredict',
        }
        for src_key, dest_key in protein_hunter_mappings.items():
            if src_key == dest_key:
                continue
            if src_key in params:
                if dest_key not in params:
                    params[dest_key] = params[src_key]
                params.pop(src_key, None)

        if 'ph_num_designs' in params and 'rfd_num_designs' not in params:
            params['rfd_num_designs'] = params['ph_num_designs']
        if not params.get('rfd_mode'):
            params['rfd_mode'] = 'protein_hunter_experimental'
    elif model_id == 'confornets_experimental':
        confornets_mappings = {
            'task': 'cn_task',
            'sequence': 'cn_sequence',
            'chain_id': 'cn_chain_id',
            'benchmark_name': 'cn_benchmark_name',
            'test_case_name': 'cn_test_case_name',
            'reference_pdb_1': 'cn_reference_pdb_1',
            'reference_name_1': 'cn_reference_name_1',
            'reference_pdb_2': 'cn_reference_pdb_2',
            'reference_name_2': 'cn_reference_name_2',
            'checkpoint_path': 'cn_checkpoint_path',
            'config_yaml': 'cn_config_yaml',
            'confornets_repo_path': 'cn_confornets_repo_path',
            'skip_msa': 'cn_skip_msa',
            'num_runs': 'cn_num_runs',
            'k_confornets': 'cn_k_confornets',
            'num_samples': 'cn_num_samples',
            'max_steps': 'cn_max_steps',
            'save_steps': 'cn_save_steps',
            'num_recycles': 'cn_num_recycles',
            'num_diffusion_steps': 'cn_num_diffusion_steps',
            'lr': 'cn_lr',
            'grad_clip': 'cn_grad_clip',
            'compute_confidence': 'cn_compute_confidence',
            'save_full_confidence': 'cn_save_full_confidence',
            'compute_evaluation': 'cn_compute_evaluation',
            'confornet_path': 'cn_confornet_path',
            'mse_dir': 'cn_mse_dir',
            'source_test_cases': 'cn_source_test_cases',
        }
        for src_key, dest_key in confornets_mappings.items():
            if src_key == dest_key:
                continue
            if src_key in params:
                if dest_key not in params:
                    params[dest_key] = params[src_key]
                params.pop(src_key, None)

        # Launcher-only documentation/model-selection metadata. ConforNets is the
        # current backend, so do not forward doc topic fields into Nextflow.
        params.pop('workflow_model_topic', None)
        params.pop('model_documentation_topic', None)
        params.pop('mapping_model', None)

        params.setdefault('cn_task', 'diversity')
        params.setdefault('cn_chain_id', 'A')
        params.setdefault('cn_benchmark_name', 'bms_confornets')
        params.setdefault('cn_test_case_name', 'monomer_case')
        params.setdefault('cn_num_runs', 2)
        params.setdefault('cn_k_confornets', 2)
        params.setdefault('cn_num_samples', 5)
        params.setdefault('cn_max_steps', 21)
        params.setdefault('cn_save_steps', '5,10,15,20')
        params.setdefault('cn_num_recycles', 0)
        params.setdefault('cn_num_diffusion_steps', 200)
        params.setdefault('cn_lr', 0.001)
        params.setdefault('cn_grad_clip', 10.0)
        params.setdefault('cn_skip_msa', False)
        params.setdefault('cn_compute_confidence', True)
        params.setdefault('cn_save_full_confidence', False)
        params.setdefault('cn_compute_evaluation', True)
        if 'cn_num_samples' in params and 'rfd_num_designs' not in params:
            params['rfd_num_designs'] = params['cn_num_samples']
        if not params.get('rfd_mode'):
            params['rfd_mode'] = 'confornets_experimental'
    elif model_id == 'boltz_cp_experimental':
        boltz_cp_mappings = {
            'input_path': 'bcp_input_path',
            'shard_plan_id': 'bcp_shard_plan_id',
            'gpu_ids': 'bcp_gpu_ids',
            'input_format': 'bcp_input_format',
            'output_format': 'bcp_output_format',
            'write_full_pae': 'bcp_write_full_pae',
            'confidence_prediction': 'bcp_confidence_prediction',
            'recycling_steps': 'bcp_recycling_steps',
            'sampling_steps': 'bcp_sampling_steps',
            'diffusion_samples': 'bcp_diffusion_samples',
            'max_msa_seqs': 'bcp_max_msa_seqs',
            'max_parallel_samples': 'bcp_max_parallel_samples',
            'precision': 'bcp_precision',
            'seed': 'bcp_seed',
            'backend': 'bcp_backend',
            'triattn_backend': 'bcp_triattn_backend',
            'context_store_mode': 'bcp_context_store_mode',
            'context_store_root': 'bcp_context_store_root',
            'context_query_tile_tokens': 'bcp_context_query_tile_tokens',
            'context_store_logical_size_cp': 'bcp_context_store_logical_size_cp',
            'context_store_pair_tile_tokens': 'bcp_context_store_pair_tile_tokens',
            'context_store_key_tile_tokens': 'bcp_context_store_key_tile_tokens',
            'context_store_projection_cache_byte_budget': 'bcp_context_store_projection_cache_byte_budget',
            'projection_cache_byte_budget': 'bcp_context_store_projection_cache_byte_budget',
            'repo_path': 'bcp_repo_path',
        }
        for src_key, dest_key in boltz_cp_mappings.items():
            if src_key == dest_key:
                continue
            if src_key in params:
                if dest_key not in params:
                    params[dest_key] = params[src_key]
                params.pop(src_key, None)

        if 'bcp_recycling_steps' not in params and params.get('boltz_recycling_steps') not in (None, ''):
            params['bcp_recycling_steps'] = params['boltz_recycling_steps']
        if 'bcp_sampling_steps' not in params and params.get('boltz_sampling_steps') not in (None, ''):
            params['bcp_sampling_steps'] = params['boltz_sampling_steps']
        if 'bcp_diffusion_samples' not in params:
            if params.get('boltz_num_samples') not in (None, ''):
                params['bcp_diffusion_samples'] = params['boltz_num_samples']
            elif params.get('boltz_diffusion_samples') not in (None, ''):
                params['bcp_diffusion_samples'] = params['boltz_diffusion_samples']

        legacy_size_cp = params.pop('size_cp', None)
        shard_plan_id = coerce_boltz_cp_shard_plan_id(params.get('bcp_shard_plan_id'))
        if shard_plan_id is None:
            shard_plan_id = infer_boltz_cp_shard_plan_id(
                params.get('bcp_size_cp') if params.get('bcp_size_cp') not in (None, '') else legacy_size_cp,
                default=BOLTZ_CP_DEFAULT_SHARD_PLAN_ID,
            )
        params['bcp_shard_plan_id'] = shard_plan_id

        requested_size_cp = get_boltz_cp_logical_size_cp(
            shard_plan_id,
            params.get('bcp_size_cp') if params.get('bcp_size_cp') not in (None, '') else legacy_size_cp,
        )
        derived_gpu_ids, derived_size_cp = _derive_boltz_cp_gpu_launch_settings(
            pinned_gpus=params.get('pinned_gpus'),
            requested_size_cp=requested_size_cp,
            fallback_gpu_ids=params.get('bcp_gpu_ids'),
            scheduler_gpu_id=params.get('gpu_id'),
        )
        if derived_gpu_ids:
            params['bcp_gpu_ids'] = derived_gpu_ids
        params['bcp_size_cp'] = derived_size_cp

        params.setdefault('bcp_input_format', 'config_files')
        params.setdefault('bcp_output_format', 'mmcif')
        params.setdefault('bcp_write_full_pae', False)
        params.setdefault('bcp_confidence_prediction', False)
        params.setdefault('bcp_max_msa_seqs', 128)
        params.setdefault('bcp_max_parallel_samples', 1)
        params.setdefault('bcp_precision', 'BF16')
        params.setdefault('bcp_backend', 'true-distributed-context-parallel')
        params.setdefault('bcp_triattn_backend', 'reference')
        params.setdefault('bcp_context_store_mode', 'evidence-only')
        params.setdefault('bcp_context_query_tile_tokens', 512)
        params.setdefault(
            'bcp_container_path',
            str(Path(explicit_container_dir) / DEFAULT_BOLTZ_CP_COMPAT_CONTAINER),
        )

        if not params.get('bcp_input_path'):
            staged_bcp_input = _write_boltz_cp_input_yaml(
                output_dir=output_dir,
                params=params,
                complex_components=complex_components,
            )
            if staged_bcp_input is not None:
                params['bcp_input_path'] = str(staged_bcp_input)
                complex_components = None
                params.pop('sequence', None)
                params.pop('sequence_input', None)
                params.pop('primary_chain_id', None)
                params.pop('target_chains', None)
                params.pop('binder_chains', None)

        if not params.get('rfd_mode'):
            params['rfd_mode'] = 'boltz_cp_experimental'
    elif model_id == 'esmfold2_experimental':
        esmfold2_quality_presets = {
            'smoke': {'num_loops': 1, 'num_sampling_steps': 25, 'num_diffusion_samples': 1},
            'standard': {'num_loops': 3, 'num_sampling_steps': 50, 'num_diffusion_samples': 1},
            'thorough': {'num_loops': 5, 'num_sampling_steps': 100, 'num_diffusion_samples': 2},
        }
        quality_preset = str(params.pop('quality_preset', '') or '').strip().lower()
        if quality_preset in esmfold2_quality_presets:
            for preset_key, preset_value in esmfold2_quality_presets[quality_preset].items():
                params.setdefault(preset_key, preset_value)
        if not params.get('pdb_chain_ids') and params.get('target_chain'):
            # Generic StructureInput writes target_chain; ESMFold2's PDB source
            # expects pdb_chain_ids.
            params['pdb_chain_ids'] = params.get('target_chain')
        params.pop('target_chain', None)
        esmfold2_mappings = {
            'sequence': 'esmf_sequence',
            'sequence_name': 'esmf_sequence_name',
            'chain_id': 'esmf_chain_id',
            'pdb_sequence_path': 'esmf_pdb_sequence_path',
            'pdb_chain_ids': 'esmf_pdb_chain_ids',
            'pdb_include_dna_rna': 'esmf_pdb_include_dna_rna',
            'msa_path': 'esmf_msa_path',
            'msa_format': 'esmf_msa_format',
            'msa_max_sequences': 'esmf_msa_max_sequences',
            'msa_remove_insertions': 'esmf_msa_remove_insertions',
            'dna_sequence': 'esmf_dna_sequence',
            'dna_chain_id': 'esmf_dna_chain_id',
            'rna_sequence': 'esmf_rna_sequence',
            'rna_chain_id': 'esmf_rna_chain_id',
            'ligand_smiles': 'esmf_ligand_smiles',
            'ligand_ccd': 'esmf_ligand_ccd',
            'ligand_chain_id': 'esmf_ligand_chain_id',
            'complex_components_json': 'esmf_complex_components_json',
            'complex_components_file': 'esmf_complex_components_file',
            'model_variant': 'esmf_model_variant',
            'model_id_or_path': 'esmf_model_id_or_path',
            'local_files_only': 'esmf_local_files_only',
            'num_loops': 'esmf_num_loops',
            'num_sampling_steps': 'esmf_num_sampling_steps',
            'num_diffusion_samples': 'esmf_num_diffusion_samples',
            'seed': 'esmf_seed',
            'device': 'esmf_device',
        }
        ui_model_variant_present = 'model_variant' in params
        ui_model_path_present = 'model_id_or_path' in params
        for src_key, dest_key in esmfold2_mappings.items():
            if src_key in params:
                # Visible UI keys must win over stale prefixed defaults that may still
                # exist on old templates or cloned launch payloads.
                params[dest_key] = params[src_key]
                params.pop(src_key, None)

        variant = str(params.get('esmf_model_variant') or 'fast').strip().lower()
        if variant not in {'fast', 'full'}:
            variant = 'fast'
        params['esmf_model_variant'] = variant
        default_model_id = 'biohub/ESMFold2' if variant == 'full' else 'biohub/ESMFold2-Fast'
        current_model_id = str(params.get('esmf_model_id_or_path') or '').strip()
        if (
            not current_model_id
            or (
                ui_model_variant_present
                and not ui_model_path_present
                and current_model_id in {'biohub/ESMFold2-Fast', 'biohub/ESMFold2'}
                and current_model_id != default_model_id
            )
        ):
            params['esmf_model_id_or_path'] = default_model_id
        if complex_components:
            esmfold2_complex_path = Path(output_dir) / "esmfold2_complex_components.json"
            esmfold2_complex_path.parent.mkdir(parents=True, exist_ok=True)
            with esmfold2_complex_path.open("w", encoding="utf-8") as handle:
                json.dump({"components": complex_components}, handle, indent=2)
            params['esmf_complex_components_file'] = str(esmfold2_complex_path)
            # The canonical structure launcher includes the primary protein in complex_components.
            # Passing both --esmf_sequence and a components file would duplicate chain IDs in the
            # ESMFold2 runner, so component-file launches own the full input system.
            params.pop('esmf_sequence', None)
            params.pop('sequence', None)
            complex_components = None
        for stale_key in ('pred_method', 'primary_chain_id', 'target_chains', 'binder_chains'):
            params.pop(stale_key, None)
        params.setdefault('esmf_local_files_only', True)
        params.setdefault('esmf_chain_id', 'A')
        params.setdefault('esmf_pdb_chain_ids', '')
        params.setdefault('esmf_pdb_include_dna_rna', True)
        params.setdefault('esmf_msa_format', 'auto')
        params.setdefault('esmf_msa_remove_insertions', True)
        params.setdefault('esmf_dna_chain_id', 'C')
        params.setdefault('esmf_rna_chain_id', 'D')
        params.setdefault('esmf_ligand_chain_id', 'L')
        params.setdefault('esmf_num_loops', 3)
        params.setdefault('esmf_num_sampling_steps', 50)
        params.setdefault('esmf_num_diffusion_samples', 1)
        params.setdefault('esmf_device', 'auto')
        if not params.get('rfd_mode'):
            params['rfd_mode'] = 'esmfold2_experimental'
    elif model_id == 'bindcraft':
        # BindCraft YAML schema uses unprefixed keys, but Nextflow expects bindcraft_*.
        bindcraft_mappings = {
            'target_pdb': 'bindcraft_target_pdb',
            'hotspot_residues': 'bindcraft_hotspot_residues',
            'chains': 'bindcraft_chains',
            'binder_lengths': 'bindcraft_binder_lengths',
            'num_final_designs': 'bindcraft_num_final_designs',
            'design_mode': 'bindcraft_design_mode',
            'scaffold_pdb': 'bindcraft_scaffold_pdb',
            'binder_chain': 'bindcraft_binder_chain',
            'design_algorithm': 'bindcraft_design_algorithm',
            'use_multimer_design': 'bindcraft_use_multimer_design',
            'num_recycles_design': 'bindcraft_num_recycles_design',
            'num_recycles_validation': 'bindcraft_num_recycles_validation',
            'mpnn_weights': 'bindcraft_mpnn_weights',
            'num_mpnn_sequences': 'bindcraft_num_mpnn_sequences',
            'mpnn_fix_interface': 'bindcraft_mpnn_fix_interface',
            'min_iptm': 'bindcraft_min_iptm',
            'max_hotspot_rmsd': 'bindcraft_max_hotspot_rmsd',
            'min_plddt': 'bindcraft_min_plddt',
            'zip_animations': 'bindcraft_zip_animations',
            'zip_plots': 'bindcraft_zip_plots',
            'remove_unrelaxed_trajectory': 'bindcraft_remove_unrelaxed_trajectory',
            'remove_unrelaxed_complex': 'bindcraft_remove_unrelaxed_complex',
            'remove_binder_monomer': 'bindcraft_remove_binder_monomer',
            'save_trajectory_pickle': 'bindcraft_save_trajectory_pickle',
            'total_trajectories': 'bindcraft_total_trajectories',
            'trajectories_per_job': 'bindcraft_trajectories_per_job',
            'use_swa': 'bindcraft_use_swa',
            'budget': 'bindcraft_budget',
            'alpha': 'bindcraft_alpha',
            'boltz_validation': 'bindcraft_boltz_validation',
            # Advanced options used by the BindCraft workflow
            'mask_mode': 'bindcraft_mask_mode',
            'redesign_ranges': 'bindcraft_redesign_ranges',
            'rm_template_seq_design': 'bindcraft_rm_template_seq_design',
            'rm_template_sc_design': 'bindcraft_rm_template_sc_design',
            'predict_initial_guess': 'bindcraft_predict_initial_guess',
            'use_termini_distance_loss': 'bindcraft_use_termini_distance_loss',
            'cdr_sampling_enabled': 'bindcraft_cdr_sampling_enabled',
            'cdr_sampling_count': 'bindcraft_cdr_sampling_count',
            'cdr_length_mode': 'bindcraft_cdr_length_mode',
            'cdr_h1_range': 'bindcraft_cdr_h1_range',
            'cdr_h2_range': 'bindcraft_cdr_h2_range',
            'cdr_h3_range': 'bindcraft_cdr_h3_range',
        }
        for src_key, dest_key in bindcraft_mappings.items():
            if src_key in params:
                if dest_key not in params:
                    params[dest_key] = params[src_key]
                params.pop(src_key, None)

        # Ensure main.nf routes into the BindCraft workflow branch.
        if not params.get('rfd_mode'):
            params['rfd_mode'] = 'bindcraft'
    elif model_id in {'antibody_denovo', 'template_antibody_denovo'}:
        if is_antibody_pipeline_mode(mode) and not params.get('rfd_mode'):
            params['rfd_mode'] = mode

    if complex_components:
        complex_json_path = Path(output_dir) / "complex_definition.json"
        # Ensure output directory exists
        complex_json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(complex_json_path, 'w') as f:
            json.dump({"components": complex_components}, f, indent=2)
        logger.info(f"Wrote complex definition to {complex_json_path}")
        cmd.extend(["--complex_json_path", str(complex_json_path)])

    if sequence_batch_json_path:
        cmd.extend(["--sequence_batch_json_path", str(sequence_batch_json_path)])
    if complex_batch_dir:
        cmd.extend(["--complex_batch_dir", str(complex_batch_dir)])
    
    # Dynamic parameter passing
    for key, value in params.items():
        if value is not None:
            # Skip empty strings - they would become valueless flags interpreted as boolean true
            if value == '':
                continue
                
            # Use mapped param name if available
            nf_key = param_mapping.get(key, key)
            
            # Sanitize filename-sensitive parameters
            if key in ('sequence_name', 'job_name', 'name'):
                value = sanitize_filename(str(value))
            
            if isinstance(value, bool):
                cmd.extend([f"--{nf_key}", str(value).lower()])
            elif isinstance(value, list):
                # Convert list to comma-separated string for Nextflow
                cmd.extend([f"--{nf_key}", ",".join(str(v) for v in value)])
            elif isinstance(value, dict):
                # Skip dict parameters for now (handled specially like complex_components)
                logger.warning(f"Skipping dict parameter {key} - not supported in command line")
            else:
                cmd.extend([f"--{nf_key}", str(value)])
            
    return cmd


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def cancel_nextflow_job(nextflow_run_id: str, graceful_timeout_seconds: float = 5.0) -> bool:
    """Cancel a running Nextflow job, escalating to SIGKILL if it ignores SIGTERM."""
    if workflow_adapter_enabled():
        try:
            return cancel_via_workflow_adapter(str(nextflow_run_id))
        except Exception as exc:
            logger.warning("Workflow adapter cancellation failed for %r: %s", nextflow_run_id, exc)
            return False

    try:
        pid = int(nextflow_run_id)
    except (TypeError, ValueError) as exc:
        logger.warning(f"Could not parse Nextflow PID {nextflow_run_id!r}: {exc}")
        return False

    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError as exc:
        logger.warning(f"Could not find Nextflow process {pid}: {exc}")
        return False

    terminated = False
    try:
        os.killpg(pgid, signal.SIGTERM)
        logger.info(f"Sent SIGTERM to Nextflow process group led by {pid}")
        terminated = True
    except Exception as group_exc:
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info(f"Sent SIGTERM to Nextflow process {pid}")
            terminated = True
        except Exception as pid_exc:
            logger.warning(
                "Could not cancel Nextflow process %s (group error: %s, pid error: %s)",
                pid,
                group_exc,
                pid_exc,
            )
            return False

    if not terminated:
        return False

    deadline = asyncio.get_running_loop().time() + max(graceful_timeout_seconds, 0.0)
    while asyncio.get_running_loop().time() < deadline:
        if not _pid_is_alive(pid):
            return True
        await asyncio.sleep(0.25)

    try:
        os.killpg(pgid, signal.SIGKILL)
        logger.warning(f"Escalated to SIGKILL for Nextflow process group led by {pid}")
    except Exception as group_exc:
        try:
            os.kill(pid, signal.SIGKILL)
            logger.warning(f"Escalated to SIGKILL for Nextflow process {pid}")
        except Exception as pid_exc:
            logger.warning(
                "Failed to SIGKILL Nextflow process %s (group error: %s, pid error: %s)",
                pid,
                group_exc,
                pid_exc,
            )
            return False

    for _ in range(20):
        if not _pid_is_alive(pid):
            return True
        await asyncio.sleep(0.25)

    logger.warning("Nextflow process %s still appears alive after SIGKILL", pid)
    return False


def get_running_jobs() -> Dict[str, int]:
    """Get currently running job IDs and their PIDs."""
    if workflow_adapter_enabled():
        running = get_adapter_running_jobs()
        for job_id in _launching_jobs:
            running.setdefault(job_id, 0)
        return running

    running = {
        job_id: proc.pid 
        for job_id, proc in _running_processes.items() 
        if proc.returncode is None
    }
    for job_id in _launching_jobs:
        running.setdefault(job_id, 0)
    return running
