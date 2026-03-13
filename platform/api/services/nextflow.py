"""
Nextflow job launcher service.

Handles launching and managing Nextflow pipeline processes.
"""

import asyncio
import subprocess
import os
import signal
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
import logging

logger = logging.getLogger(__name__)

# Track running processes
_running_processes: Dict[str, asyncio.subprocess.Process] = {}

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

# Project root (parent of platform directory)
PROJECT_ROOT = get_code_root()


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
    Auto-run ANARCII CDR annotation after antibody jobs complete.
    Runs in a background thread and updates the DB directly.
    """
    if job.parent_job_id:
        return
    if not _is_antibody_job(job):
        return

    try:
        from database import Design, Job as JobModel
        from sqlalchemy import select
        from services.cdr_annotation_tasks import annotate_and_update_designs

        # Include child jobs (exploration mode)
        child_result = await session.execute(select(JobModel.id).where(JobModel.parent_job_id == job.id))
        child_job_ids = [row[0] for row in child_result.all()]
        all_job_ids = [job.id] + child_job_ids

        designs_result = await session.execute(
            select(Design).where(Design.job_id.in_(all_job_ids))
        )
        designs = designs_result.scalars().all()
        pdb_paths = [d.pdb_path for d in designs if d.pdb_path]
        design_ids = [d.id for d in designs if d.pdb_path]

        if not pdb_paths:
            logger.info(f"[CDR AUTO] No PDBs found for job {job.id}, skipping ANARCII")
            return

        logger.info(f"[CDR AUTO] Starting ANARCII for {len(pdb_paths)} designs (job {job.id})")
        asyncio.create_task(
            annotate_and_update_designs(pdb_paths, design_ids, job_id=str(job.id))
        )
    except Exception as e:
        logger.warning(f"[CDR AUTO] Failed to start ANARCII: {e}")


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
                "frustrampnn", "predict",
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
                            await session.commit()
                            logger.info(f"[FRUST BATCH] Updated frustration for {design.name}")
        
        logger.info(f"[FRUST BATCH] Completed FrustraMPNN batch for {len(pdb_paths)} PDBs")
        
    except Exception as e:
        logger.error(f"[FRUST BATCH] Error running batch FrustraMPNN: {e}", exc_info=True)


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
    
    # Get sequences JSON and GPU ID
    sequences_json = params.get('sequences_json', '[]')
    if isinstance(sequences_json, (list, dict)):
        import json as _json
        sequences_json = _json.dumps(sequences_json)
    elif not isinstance(sequences_json, str):
        sequences_json = '[]'
    raw_gpu_id = params.get('gpu_id')
    try:
        gpu_id = int(raw_gpu_id) if raw_gpu_id is not None else None
    except (TypeError, ValueError):
        gpu_id = None
    reference_sequence = params.get('reference_sequence', '')
    force_refresh = params.get('msa_force_refresh', False)
    msa_use_gpu_raw = params.get('msa_use_gpu', True)
    if isinstance(msa_use_gpu_raw, str):
        msa_use_gpu = msa_use_gpu_raw.strip().lower() not in {"0", "false", "no", "off"}
    else:
        msa_use_gpu = bool(msa_use_gpu_raw)
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
    
    # Build batch_msa.py command
    from paths import get_colabfold_db, get_msa_cache_dir
    db_path = str(get_colabfold_db())
    cache_dir = str(get_msa_cache_dir())
    script_path = PROJECT_ROOT / "scripts" / "batch_msa.py"
    cmd = [
        "python3", str(script_path),
        "--sequences", sequences_json,
        "--output_dir", output_dir,
        "--db_path", db_path,
        "--cache_dir", cache_dir,
        "--preset", msa_preset,
    ]
    if gpu_id is not None:
        cmd.extend(["--gpu_id", str(gpu_id)])
    if reference_sequence:
        cmd.extend(["--reference_sequence", reference_sequence])
    if force_refresh:
        cmd.append("--force_refresh")
    if msa_use_gpu is False:
        cmd.append("--cpu-only")
    if msa_max_seqs is not None:
        cmd.extend(["--max-seqs", str(msa_max_seqs)])
    if msa_use_expand is not None:
        cmd.extend(["--use-expand", "1" if _coerce_bool(msa_use_expand) else "0"])
    if msa_use_env is not None:
        cmd.extend(["--use-env", "1" if _coerce_bool(msa_use_env) else "0"])
    if msa_num_iterations is not None:
        cmd.extend(["--num-iterations", str(msa_num_iterations)])
    if msa_evalue is not None:
        cmd.extend(["--evalue", str(msa_evalue)])
    if msa_min_seq_id is not None:
        cmd.extend(["--min-seq-id", str(msa_min_seq_id)])
    if msa_min_coverage is not None:
        cmd.extend(["--min-coverage", str(msa_min_coverage)])
    if msa_taxon_list:
        cmd.extend(["--taxon-list", str(msa_taxon_list)])
    if msa_min_depth_warning is not None:
        cmd.extend(["--min-depth-warning", str(msa_min_depth_warning)])
    if msa_min_depth_fail is not None:
        cmd.extend(["--min-depth-fail", str(msa_min_depth_fail)])
    if msa_gpu_mode:
        cmd.extend(["--gpu-mode", str(msa_gpu_mode)])
    if msa_gpu_threshold is not None:
        cmd.extend(["--gpu-threshold", str(msa_gpu_threshold)])
    if msa_preferred_gpus:
        if isinstance(msa_preferred_gpus, list):
            preferred = ",".join(str(v) for v in msa_preferred_gpus if str(v).strip())
        else:
            preferred = str(msa_preferred_gpus).strip()
        if preferred:
            cmd.extend(["--preferred-gpus", preferred])
    if msa_excluded_gpus:
        if isinstance(msa_excluded_gpus, list):
            excluded = ",".join(str(v) for v in msa_excluded_gpus if str(v).strip())
        else:
            excluded = str(msa_excluded_gpus).strip()
        if excluded:
            cmd.extend(["--excluded-gpus", excluded])
    if msa_gpu_server_mode:
        cmd.extend(["--gpu-server-mode", str(msa_gpu_server_mode)])
    if msa_gpu_server_wait_timeout is not None:
        cmd.extend(["--gpu-server-wait-timeout", str(msa_gpu_server_wait_timeout)])
    if msa_gpu_server_db_load_mode is not None:
        cmd.extend(["--gpu-server-db-load-mode", str(msa_gpu_server_db_load_mode)])
    if msa_gpu_server_startup_wait is not None:
        cmd.extend(["--gpu-server-startup-wait", str(msa_gpu_server_startup_wait)])
    
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
    output_dir: str
) -> None:
    """
    Launch a Nextflow pipeline job.
    
    This runs in a background task and updates the database with status.
    """
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

    preflight_notes: List[str] = []
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

        if job.status == JobStatus.RUNNING.value and job.started_at is not None:
            logger.warning(f"Job {job_id} is already marked running; skipping duplicate launcher entry")
            return
        
        # Check if job was cancelled while queued
        if job.status == JobStatus.CANCELLED.value:
            logger.info(f"Job {job_id} was cancelled before starting, aborting launch")
            return
        
        job.status = JobStatus.RUNNING.value
        job.started_at = datetime.utcnow()
        await session.commit()
        
        # Re-check cancellation status right before spawning (minimize race window)
        await session.refresh(job)
        if job.status == JobStatus.CANCELLED.value:
            logger.info(f"Job {job_id} was cancelled just before spawn, aborting")
            return
        
        try:
            # ═══════════════════════════════════════════════════════════════
            # GPU ASSIGNMENT: Set CUDA_VISIBLE_DEVICES from orchestrator
            # ═══════════════════════════════════════════════════════════════
            # Extract gpu_id from params (set by orchestrator)
            gpu_id = launch_params.get('gpu_id')

            # Build environment with GPU pinning
            env = {**os.environ, "NXF_ANSI_LOG": "false"}
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
                            job.queue_status = 'paused'
                            job.current_stage = job.awaiting_stage or job.current_stage or "Awaiting Input"
                        else:
                            job.status = JobStatus.COMPLETED.value
                            job.queue_status = 'completed'
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
                            await maybe_auto_annotate_cdrs(job, session)
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
                        logger.error(f"Nextflow failed for job {job_id} with code {exit_code}")
                        
                        # Log last few lines
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
                if job.status != JobStatus.CANCELLED.value:
                    job.status = JobStatus.FAILED.value
                    job.queue_status = 'failed'  # Update queue_status so job leaves the queue UI
                    job.error_message = str(e)
                    job.completed_at = datetime.utcnow()
                    await session.commit()


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
        logger.warning("complex_components NOT in params - ligands will not be used!")
    
    # Mode to profile mapping for modes that need translation
    mode_to_profile = {
        # structure_validation and structure_prediction use pred_method
        'structure_validation': params.get('pred_method', 'boltz'),
        'structure_prediction': params.get('pred_method', 'boltz'),
        # DNA polymerase template
        'dna_polymerase': 'fampnn_predict',
    }
    
    # Model + mode to profile mapping (for API-driven jobs)
    model_mode_to_profile = {
        ('boltz2', 'predict'): 'boltz',
        ('boltz2', 'complex'): 'boltz',
        ('rf3', 'predict'): 'rf3',
        ('af2', 'predict'): 'af2',
        # BindCraft API modes route to the binder profile; workflow is selected via rfd_mode
        ('bindcraft', 'minibinder'): 'binder_denovo',
        ('bindcraft', 'peptide'): 'binder_denovo',
        # Mutagenesis batch workflow - routes to boltz for structure prediction
        ('mutagenesis', 'batch_predict'): 'boltz',
        # Antibody workflows use boltz profile (Boltz2 is the structure predictor)
        ('antibody_denovo', 'antibody_denovo_pipeline'): 'boltz',
        ('antibody_denovo', 'default'): 'boltz',
        ('template_antibody_denovo', 'antibody_denovo_pipeline'): 'boltz',
        ('template_antibody_denovo', 'default'): 'boltz',
        # Batch validation jobs (spawned by antibody_denovo logic)
        ('antibody_child', 'validation_batch'): 'boltz',
        # RFantibody child jobs (backbone generation - spawned by orchestrator)
        ('rfantibody_child', 'antibody_backbone'): 'antibody_backbone',  # Uses antibody_backbone profile which sets rfd_mode correctly
        # FAMPNN child jobs (sequence design - spawned by orchestrator)
        ('fampnn_child', 'sequence_design'): 'fampnn_predict',
        # Oligo Designer (RFDpoly multi-polymer design)
        ('oligo_design', 'oligo_design'): 'oligo_design',
        # Nanopore basecalling + methylation analysis
        ('nanopore', 'methylation_analysis'): 'nanopore_methylation',
        # Protenix structure prediction
        ('protenix', 'predict'): 'protenix',
        ('protenix', 'complex'): 'protenix',
    }

    def resolve_antibody_validation_profile(default_profile: str) -> str:
        antibody_modes = {
            ('antibody_denovo', 'antibody_denovo_pipeline'),
            ('antibody_denovo', 'default'),
            ('template_antibody_denovo', 'antibody_denovo_pipeline'),
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

    # Resolve runtime roots explicitly so Nextflow doesn't fall back to stale defaults.
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
            "nextflow", "run", "main.nf",
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
            "nextflow", "run", "main.nf",
            "-profile", profile,
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

        if params.get("msa_preferred_gpus") in (None, ""):
            preferred_ids = []
            preferred_source = None
            raw_pinned_gpu = msa_server_settings.get("pinned_gpu_id")
            if raw_pinned_gpu not in (None, ""):
                try:
                    preferred_ids = [int(raw_pinned_gpu)]
                except (TypeError, ValueError):
                    preferred_ids = []
                if preferred_ids:
                    preferred_source = "persisted MSA server settings"
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
        'boltz_use_msa': 'boltz_use_msa',
        'boltz_method': 'boltz_method',
        'boltz_use_potentials': 'boltz_use_potentials',
        'boltz_step_scale': 'boltz_step_scale',
        'boltz_anchor_target': 'boltz_anchor_target',
        'boltz_anchor_strict': 'boltz_anchor_strict',
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
    
    # Model-specific param preprocessing: Route ntp_type and ligand_smiles to correct targets
    if model_id == 'unidock':
        # For Uni-Dock: ntp_type -> unidock_ntp_type, ligand_smiles -> unidock_ligand_smiles
        if 'ntp_type' in params:
            params['unidock_ntp_type'] = params.pop('ntp_type')
        if 'ligand_smiles' in params and 'unidock_ligand_smiles' not in params:
            params['unidock_ligand_smiles'] = params.pop('ligand_smiles')
    elif model_id == 'diffdock':
        # For DiffDock: ntp_type -> diffdock_ntp_type
        if 'ntp_type' in params:
            params['diffdock_ntp_type'] = params.pop('ntp_type')
    elif model_id == 'boltzgen':
        # For BoltzGen: Apply all BoltzGen-specific parameter mappings
        # These were previously in global param_mapping and broke other workflows!
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
    
    if complex_components:
        import json
        complex_json_path = Path(output_dir) / "complex_definition.json"
        # Ensure output directory exists
        complex_json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(complex_json_path, 'w') as f:
            json.dump({"components": complex_components}, f, indent=2)
        logger.info(f"Wrote complex definition to {complex_json_path}")
        cmd.extend(["--complex_json_path", str(complex_json_path)])
    
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


async def cancel_nextflow_job(nextflow_run_id: str) -> bool:
    """Cancel a running Nextflow job."""
    try:
        pid = int(nextflow_run_id)
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            logger.info(f"Sent SIGTERM to Nextflow process group led by {pid}")
        except Exception:
            os.kill(pid, signal.SIGTERM)
            logger.info(f"Sent SIGTERM to Nextflow process {pid}")
        return True
    except (ValueError, ProcessLookupError) as e:
        logger.warning(f"Could not cancel Nextflow process: {e}")
        return False


def get_running_jobs() -> Dict[str, int]:
    """Get currently running job IDs and their PIDs."""
    return {
        job_id: proc.pid 
        for job_id, proc in _running_processes.items() 
        if proc.returncode is None
    }
