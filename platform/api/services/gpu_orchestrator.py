"""
GPU Orchestrator Service

A background service that manages GPU job scheduling using bin-packing.
Runs alongside the FastAPI server and polls for pending jobs.

Key Features:
- VRAM-aware bin-packing (First Fit Decreasing)
- Multi-job per GPU (packs until target fill reached)
- User-configurable target VRAM fill via slider
- GPU enable/disable support
- OOM detection and retry handling
"""

import asyncio
import json
import logging
import math
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, replace

# Will be imported when integrated with FastAPI
# from database import Job, async_session
# from routers.gpu import get_gpu_stats
# from services.nextflow import launch_nextflow_job

logger = logging.getLogger(__name__)

from services.gpu_config import read_scheduler_config, write_scheduler_config
from services.gpu_metadata import GPU_CAPABILITIES
from services.gpu_stage_activity import job_uses_assigned_gpu
from services.stage_review import has_stage_gate, nextflow_history_status


@dataclass
class GPUState:
    """Snapshot of a single GPU's state."""
    index: int
    name: str
    memory_used_mb: int
    memory_total_mb: int
    memory_free_mb: int
    utilization: int
    temperature: int


@dataclass
class JobInfo:
    """Minimal job info for packing algorithm."""
    id: str
    name: str
    model_type: str
    vram_estimate_mb: int
    sequence_length: int
    priority: int
    pinned_gpu: Optional[int]
    created_at: datetime
    batch_id: Optional[str] = None  # For GPU locking - all jobs in a batch share exclusive GPU access
    pinned_gpus: Optional[List[int]] = None  # Multi-GPU allowlist for parallel distribution
    scheduler_reservation_mb: Optional[int] = None


# ═══════════════════════════════════════════════════════════════════════════════
# VRAM ESTIMATION PROFILES
# ═══════════════════════════════════════════════════════════════════════════════

# Base VRAM + scaling factor per model
# Formula: base + scale * (sequence_length / 100)^2
# NOTE: These estimates are tuned for actual observed VRAM usage.
# Boltz-2 typically uses 6-8GB even for larger proteins.
VRAM_PROFILES = {
    'boltz': {'base': 5000, 'scale': 20},       # Boltz-2 structure prediction (~6-8GB observed)
    'boltz_batch': {'base': 6000, 'scale': 25}, # Boltz-2 batch mode
    'boltzgen': {'base': 5000, 'scale': 20},    # BoltzGen design
    'rf3': {'base': 10000, 'scale': 55},        # RosettaFold3 (high VRAM)
    'af2': {'base': 12000, 'scale': 65},        # AlphaFold2 (highest VRAM)
    'rfdiffusion': {'base': 5000, 'scale': 25}, # RFdiffusion
    'rfantibody': {'base': 6000, 'scale': 25},  # RFantibody antibody generation
    'rfantibody_child': {'base': 6000, 'scale': 25},  # RFantibody child (same as parent)
    # ──────────────────────────────────────────────────────────────────────────
    # MPNN VARIANTS - All share similar lightweight architecture (~2-4GB)
    # ──────────────────────────────────────────────────────────────────────────
    'fampnn': {'base': 3000, 'scale': 10},      # FAMPNN sequence design (CDR-focused)
    'fampnn_child': {'base': 3000, 'scale': 10},# FAMPNN child jobs
    'proteinmpnn': {'base': 2000, 'scale': 5},  # ProteinMPNN (vanilla)
    'mpnn': {'base': 2000, 'scale': 5},         # Alias for ProteinMPNN
    'ligandmpnn': {'base': 2500, 'scale': 8},   # LigandMPNN (ligand-aware sequence design)
    'thermompnn': {'base': 2000, 'scale': 5},   # ThermoMPNN (stability-focused)
    'frustrampnn': {'base': 2500, 'scale': 8},  # FrustraMPNN (frustration analysis)
    # ──────────────────────────────────────────────────────────────────────────
    # PROTENIX VARIANTS - measured from docs: 6GB@500tok → 78GB@4000tok
    # ──────────────────────────────────────────────────────────────────────────
    'protenix': {'base': 4000, 'scale': 55},          # Protenix base (MSA mode)
    'protenix_esm': {'base': 6000, 'scale': 60},      # Protenix ESM2-3B (no-MSA, heavier)
    'protenix_mini_esm': {'base': 5000, 'scale': 50}, # Protenix mini ESM (lighter no-MSA)
    # ──────────────────────────────────────────────────────────────────────────
    'diffdock': {'base': 4000, 'scale': 12},    # DiffDock
    'unidock': {'base': 3000, 'scale': 8},      # Uni-Dock
    'msa_batch': {'base': 3000, 'scale': 2},    # MSA Generation (GPU streaming, LOW VRAM)
    'antibody_child': {'base': 6000, 'scale': 25},  # Antibody validation (Boltz + scoring) ~6-8GB
    'maturation_child': {'base': 18500, 'scale': 25},  # PPIFlow child jobs need one-per-24/32GB GPU, not startup VRAM
    'antibody_denovo': {'base': 6000, 'scale': 25},  # Full antibody pipeline
    'oligo_design': {'base': 7000, 'scale': 20},     # Oligo Designer (RFDpoly + NA-MPNN)
    'default': {'base': 6000, 'scale': 25},     # Conservative fallback
}

# Models that need heavy GPUs (exclude 5060 Ti)
HEAVY_MODELS = {'af2', 'rfdiffusion', 'rf3', 'maturation_child'}
PROTENIX_MODELS = {'protenix', 'protenix_esm', 'protenix_mini_esm'}

# Scheduler-side packing should follow observed live VRAM plus a modest surge
# allowance, not reserve worst-case peak estimates for every running job.
#
# Keys:
# - startup_reserve_mb: provisional reservation for a just-launched or queued job
#   before live attribution settles
# - live_surge_mb: extra headroom to add on top of live VRAM for a running job
# - startup_grace_seconds: time window after launch where startup_reserve_mb is used
SCHEDULER_RESERVATION_PROFILES: Dict[str, Dict[str, int]] = {
    'rfantibody': {
        'startup_reserve_mb': 2200,
        'live_surge_mb': 1800,
        'startup_grace_seconds': 45,
    },
    'rfantibody_child': {
        'startup_reserve_mb': 2200,
        'live_surge_mb': 1800,
        'startup_grace_seconds': 45,
    },
    'fampnn': {
        'startup_reserve_mb': 1200,
        'live_surge_mb': 900,
        'startup_grace_seconds': 30,
    },
    'fampnn_child': {
        'startup_reserve_mb': 1200,
        'live_surge_mb': 900,
        'startup_grace_seconds': 30,
    },
    'antibody_child': {
        'startup_reserve_mb': 3500,
        'live_surge_mb': 2500,
        'startup_grace_seconds': 60,
    },
    'maturation_child': {
        'startup_reserve_mb': 3200,
        'live_surge_mb': 3500,
        'startup_grace_seconds': 75,
    },
}


def _coerce_bool(value: Any, default: bool = False) -> bool:
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


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_successful_child_wait_result(work_dir: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Detect a successful wait-for-children task from a Nextflow work directory.

    This is used during reconciliation when the parent launcher/API lost the
    live Nextflow process but the wait task itself already completed and wrote a
    child_outputs.json file.
    """
    if not work_dir:
        return None

    task_dir = Path(work_dir)
    if not task_dir.exists():
        return None

    exitcode_path = task_dir / ".exitcode"
    child_outputs_path = task_dir / "child_outputs.json"
    if not exitcode_path.exists() or not child_outputs_path.exists():
        return None

    try:
        if exitcode_path.read_text(errors="ignore").strip() != "0":
            return None
        payload = json.loads(child_outputs_path.read_text())
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    status = str(payload.get("status", "")).strip().lower()
    total = _coerce_int(payload.get("total"), 0)
    completed = _coerce_int(payload.get("completed"), 0)
    failed = _coerce_int(payload.get("failed"), 0)
    cancelled = _coerce_int(payload.get("cancelled"), 0)
    if status != "complete":
        return None
    if completed <= 0:
        return None
    if failed != 0 or cancelled != 0:
        return None
    if total > 0 and completed < total:
        return None

    output_dirs = payload.get("child_output_dirs")
    if not isinstance(output_dirs, list):
        output_dirs = []

    return {
        "total": total,
        "completed": completed,
        "output_dirs": output_dirs,
        "mtime": child_outputs_path.stat().st_mtime,
    }


def _normalize_protenix_profile(params: Dict[str, Any]) -> str:
    """Map Protenix params to the closest VRAM profile."""
    use_msa = _coerce_bool(params.get("protenix_use_msa", True), default=True)
    model_name = str(params.get("protenix_model_weights", "")).strip().lower()

    if "mini" in model_name and ("esm" in model_name or "ism" in model_name):
        return "protenix_mini_esm"
    if "esm" in model_name or "ism" in model_name:
        return "protenix_esm"
    if not use_msa:
        # Pipeline auto-switches to mini ESM when MSA is disabled on a base model.
        return "protenix_mini_esm"
    return "protenix"


def _normalize_msa_preset(value: Any) -> str:
    preset = str(value).strip().lower() if value is not None else "fast"
    if preset in {"maximum", "max"}:
        return "maximum"
    if preset in {"balanced", "balance", "medium"}:
        return "balanced"
    return "fast"


def estimate_protenix_tokens(params: Any, fallback_length: int = 300) -> int:
    """
    Estimate Protenix total-token load from input payload.

    Protenix memory scales with total tokens across all entities in the complex.
    """
    payload: Dict[str, Any] = {}
    if isinstance(params, dict):
        payload = params
    elif isinstance(params, str):
        try:
            import json
            loaded = json.loads(params)
            if isinstance(loaded, dict):
                payload = loaded
        except Exception:
            payload = {}

    total_tokens = 0
    components = payload.get("complex_components")
    if isinstance(components, list):
        for comp in components:
            if not isinstance(comp, dict):
                continue
            comp_type = str(comp.get("type", "")).strip().lower()
            count = max(1, _coerce_int(comp.get("count", 1), 1))

            if comp_type in {"protein", "peptide", "dna", "rna"}:
                sequence = comp.get("sequence")
                if isinstance(sequence, str) and sequence:
                    total_tokens += len(sequence) * count
                continue

            # Ligands/ions are much smaller; approximate using atom count if available.
            if comp_type in {"ligand", "ion"}:
                atom_count = _coerce_int(comp.get("atom_count"), 0)
                if atom_count <= 0:
                    smiles = comp.get("smiles")
                    if isinstance(smiles, str) and smiles:
                        atom_count = max(1, len(re.findall(r"[A-Z][a-z]?", smiles)))
                    elif comp.get("ccd") or comp.get("ion") or comp.get("element"):
                        atom_count = 1
                if atom_count > 0:
                    total_tokens += atom_count * count

    if total_tokens > 0:
        return total_tokens

    for key in ("sequence_input", "sequence"):
        sequence = payload.get(key)
        if isinstance(sequence, str) and sequence:
            return len(sequence)

    return max(1, _coerce_int(fallback_length, 300))


def _protenix_runtime_multiplier(params: Dict[str, Any]) -> float:
    """
    Apply conservative multipliers for Protenix runtime knobs that increase memory.
    """
    n_sample = max(1, _coerce_int(params.get("protenix_n_sample", 5), 5))
    n_cycle = max(1, _coerce_int(params.get("protenix_n_cycle", 10), 10))
    n_step = max(1, _coerce_int(params.get("protenix_n_step", 200), 200))
    use_msa = _coerce_bool(params.get("protenix_use_msa", True), default=True)
    msa_preset = _normalize_msa_preset(params.get("msa_preset", "fast"))
    msa_max_seqs = _coerce_int(params.get("msa_max_seqs"), 0)
    use_template = _coerce_bool(params.get("protenix_use_template", False), default=False)

    multiplier = 1.0
    if n_sample > 1:
        multiplier += min(0.60, 0.08 * (n_sample - 1))
    if n_cycle > 8:
        multiplier += min(0.35, 0.03 * (n_cycle - 8))
    if n_step > 200:
        multiplier += min(0.20, 0.0005 * (n_step - 200))
    if use_msa:
        if msa_preset == "balanced":
            multiplier += 0.10
        elif msa_preset == "maximum":
            multiplier += 0.20
        if msa_max_seqs > 8000:
            multiplier += 0.12
        elif msa_max_seqs > 4000:
            multiplier += 0.06
    if use_template:
        multiplier += 0.08

    return max(1.0, min(2.5, multiplier))


def estimate_vram(model_type: str, sequence_length: int, params: Optional[Any] = None) -> int:
    """
    Estimate VRAM required for a job.
    
    Uses quadratic scaling: base + scale * (length/100)^2
    """
    normalized_model = (model_type or "default").strip().lower()
    effective_length = max(1, _coerce_int(sequence_length, 300))
    runtime_multiplier = 1.0
    profile_key = normalized_model

    if normalized_model == "protenix":
        protenix_params: Dict[str, Any] = {}
        if isinstance(params, dict):
            protenix_params = params
        elif isinstance(params, str):
            try:
                import json
                loaded = json.loads(params)
                if isinstance(loaded, dict):
                    protenix_params = loaded
            except Exception:
                protenix_params = {}
        effective_length = estimate_protenix_tokens(protenix_params, effective_length)
        profile_key = _normalize_protenix_profile(protenix_params)
        runtime_multiplier = _protenix_runtime_multiplier(protenix_params)

    profile = VRAM_PROFILES.get(profile_key, VRAM_PROFILES['default'])
    base = profile['base']
    scale = profile['scale']
    
    # Quadratic scaling for attention-based models
    length_factor = (effective_length / 100) ** 2
    estimated = int((base + scale * length_factor) * runtime_multiplier)
    
    return estimated


def _scheduler_profile(model_type: str) -> Dict[str, int]:
    normalized_model = (model_type or "default").strip().lower()
    return SCHEDULER_RESERVATION_PROFILES.get(normalized_model, {})


def _effective_job_model_type(job: Any) -> str:
    mode = str(getattr(job, "mode", "") or "").strip().lower()
    if mode == "maturation_child":
        return "maturation_child"
    return str(getattr(job, "model_id", None) or "default").strip().lower()


def _pending_job_reservation_mb(job: "JobInfo", observed_live_by_model: Dict[str, List[int]]) -> int:
    """
    Return the scheduler packing reservation for a queued job.

    This uses live observations when available plus a model-specific surge buffer,
    falling back to a smaller startup reserve rather than the peak estimate.
    """
    profile = _scheduler_profile(job.model_type)
    peak_estimate = max(1, int(job.vram_estimate_mb or 1))
    startup_reserve = int(profile.get("startup_reserve_mb", peak_estimate))
    live_surge = int(profile.get("live_surge_mb", 0))
    observed = observed_live_by_model.get(job.model_type, [])
    if observed:
        median_live = _median(observed) or 0
        upper_live = _upper_quantile(observed, 0.75) or median_live
        observed_live = max(median_live, upper_live)
        return max(1, min(peak_estimate, max(startup_reserve, observed_live + live_surge)))
    return max(1, min(peak_estimate, startup_reserve))


def _running_job_reservation_mb(job: Any, live_vram_mb: Optional[int]) -> int:
    """
    Return the scheduler reservation to account for an already-running job.

    Prefer live attribution plus a surge buffer. Only use the startup reserve
    while the job is very new or has no live attribution yet.
    """
    peak_estimate = max(1, int(getattr(job, "vram_estimate_mb", 0) or 1))
    profile = _scheduler_profile(_effective_job_model_type(job))
    startup_reserve = int(profile.get("startup_reserve_mb", peak_estimate))
    live_surge = int(profile.get("live_surge_mb", 0))
    startup_grace = int(profile.get("startup_grace_seconds", 30))

    started_at = getattr(job, "started_at", None)
    if live_vram_mb is not None and live_vram_mb > 0:
        return max(1, min(peak_estimate, int(live_vram_mb) + live_surge))

    if started_at is not None:
        try:
            age_seconds = max(0.0, (datetime.utcnow() - started_at).total_seconds())
        except Exception:
            age_seconds = 0.0
        if age_seconds <= startup_grace:
            return max(1, min(peak_estimate, startup_reserve))

    return max(1, min(peak_estimate, startup_reserve))


def collect_live_vram_by_job(running_jobs: List[Any], gpu_stats: List[Any]) -> Dict[str, int]:
    """
    Attribute live GPU memory to running jobs.

    Exact process attribution is preferred. When multiple jobs share a GPU and
    only some processes can be matched exactly, the remaining GPU memory is
    distributed across the unmatched jobs proportionally to their current
    scheduler reservation. This keeps queue display and live-aware packing from
    falling back to peak estimates whenever same-model jobs share a device.
    """
    runnable = [
        job for job in running_jobs
        if getattr(job, "queue_status", None) == "running" and job_uses_assigned_gpu(job)
    ]
    if not runnable:
        return {}

    processes_by_gpu = {gpu.index: getattr(gpu, "processes", []) for gpu in gpu_stats}
    ancestor_cache: Dict[int, set[int]] = {}
    cmdline_cache: Dict[int, str] = {}
    live_vram_by_job: Dict[str, int] = {}
    jobs_by_gpu: Dict[int, List[Any]] = {}

    for job in runnable:
        jobs_by_gpu.setdefault(job.assigned_gpu, []).append(job)

    for gpu_idx, gpu_jobs in jobs_by_gpu.items():
        gpu_processes = processes_by_gpu.get(gpu_idx, [])
        if not gpu_processes:
            continue
        matched_by_job: Dict[str, int] = {}

        for job in gpu_jobs:
            launcher_pid = None
            try:
                launcher_pid = int(str(getattr(job, "nextflow_run_id", "")).strip())
            except (TypeError, ValueError):
                launcher_pid = None

            matched_vram = 0
            for proc in gpu_processes:
                matched = False
                proc_pid = getattr(proc, "pid", None)

                if launcher_pid is not None and proc_pid is not None:
                    if proc_pid in ancestor_cache:
                        ancestors = ancestor_cache[proc_pid]
                    else:
                        ancestors = set()
                        try:
                            import psutil
                            ps_proc = psutil.Process(proc_pid)
                            while ps_proc is not None:
                                ancestors.add(ps_proc.pid)
                                ps_proc = ps_proc.parent()
                        except Exception:
                            pass
                        ancestor_cache[proc_pid] = ancestors
                    if launcher_pid in ancestors:
                        matched = True

                if not matched and proc_pid is not None:
                    if proc_pid in cmdline_cache:
                        cmdline = cmdline_cache[proc_pid]
                    else:
                        cmdline = ""
                        try:
                            import psutil
                            cmdline = " ".join(psutil.Process(proc_pid).cmdline()).lower()
                        except Exception:
                            cmdline = ""
                        cmdline_cache[proc_pid] = cmdline

                    candidate_tokens = [str(job.id).lower()]
                    if getattr(job, "name", None):
                        candidate_tokens.extend([
                            str(job.name).lower(),
                            str(job.name).lower().replace(" ", "."),
                        ])
                    if any(token and token in cmdline for token in candidate_tokens):
                        matched = True

                if matched:
                    matched_vram += max(getattr(proc, "memory_mb", 0), 0)

            if matched_vram > 0:
                matched_by_job[job.id] = matched_vram

        total_gpu_vram = sum(max(getattr(proc, "memory_mb", 0), 0) for proc in gpu_processes)
        matched_total = sum(matched_by_job.values())
        unmatched_jobs = [job for job in gpu_jobs if matched_by_job.get(job.id, 0) <= 0]

        if not unmatched_jobs:
            for job_id, matched_vram in matched_by_job.items():
                live_vram_by_job[job_id] = matched_vram
            continue

        if len(gpu_jobs) == 1 and matched_total <= 0:
            live_vram_by_job[gpu_jobs[0].id] = total_gpu_vram
            continue

        remaining_vram = max(0, total_gpu_vram - matched_total)
        if remaining_vram > 0:
            weighted_jobs: List[Tuple[Any, int]] = []
            for job in unmatched_jobs:
                weight = _running_job_reservation_mb(job, None)
                weighted_jobs.append((job, max(1, weight)))

            total_weight = sum(weight for _job, weight in weighted_jobs) or len(weighted_jobs)
            remaining_assignments: Dict[str, int] = {}
            fractional: List[Tuple[float, str]] = []
            allocated = 0
            for job, weight in weighted_jobs:
                raw_share = (remaining_vram * weight) / total_weight
                share = int(math.floor(raw_share))
                if share > 0:
                    remaining_assignments[job.id] = share
                    allocated += share
                fractional.append((raw_share - share, job.id))

            remainder = remaining_vram - allocated
            for _fraction, job_id in sorted(fractional, reverse=True):
                if remainder <= 0:
                    break
                remaining_assignments[job_id] = remaining_assignments.get(job_id, 0) + 1
                remainder -= 1

            for job_id, matched_vram in matched_by_job.items():
                live_vram_by_job[job_id] = matched_vram
            for job_id, shared_vram in remaining_assignments.items():
                live_vram_by_job[job_id] = live_vram_by_job.get(job_id, 0) + shared_vram
            continue

        for job_id, matched_vram in matched_by_job.items():
            live_vram_by_job[job_id] = matched_vram

    return live_vram_by_job


def _collect_live_vram_by_job_for_scheduler(running_jobs: List[Any], gpu_stats: List[Any]) -> Dict[str, int]:
    return collect_live_vram_by_job(running_jobs, gpu_stats)


def _normalize_job_params(raw_params: Any) -> Dict[str, Any]:
    """Ensure job params are a dict, handling JSON-encoded strings safely."""
    if raw_params is None:
        return {}
    if isinstance(raw_params, dict):
        return raw_params
    if isinstance(raw_params, str):
        try:
            import json
            loaded = json.loads(raw_params)
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}
    return {}


def _normalize_pinned_gpus(raw_value: Any) -> Optional[List[int]]:
    """Normalize pinned_gpus to a list of ints, or None if invalid."""
    if raw_value is None:
        return None
    values = []
    if isinstance(raw_value, list):
        values = raw_value
    elif isinstance(raw_value, str):
        values = [v.strip() for v in raw_value.split(",") if v.strip()]
    else:
        return None

    normalized = []
    for value in values:
        try:
            normalized.append(int(value))
        except (TypeError, ValueError):
            continue
    return normalized or None


def _normalize_gpu_id_list(raw_value: Any) -> Optional[List[int]]:
    """Normalize scheduler-config GPU list (list or comma string) to sorted ints."""
    if raw_value is None:
        return None
    values = []
    if isinstance(raw_value, list):
        values = raw_value
    elif isinstance(raw_value, str):
        values = [v.strip() for v in raw_value.split(",") if v.strip()]
    else:
        return None

    normalized = []
    for value in values:
        try:
            normalized.append(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(set(normalized)) or None


def _median(values: List[int]) -> Optional[int]:
    if not values:
        return None
    sorted_vals = sorted(values)
    mid = len(sorted_vals) // 2
    if len(sorted_vals) % 2 == 1:
        return sorted_vals[mid]
    return int((sorted_vals[mid - 1] + sorted_vals[mid]) / 2)


def _upper_quantile(values: List[int], quantile: float = 0.75) -> Optional[int]:
    if not values:
        return None
    sorted_vals = sorted(values)
    quantile = max(0.0, min(1.0, quantile))
    index = int(math.ceil((len(sorted_vals) - 1) * quantile))
    return sorted_vals[index]


def _read_nextflow_history_statuses(job_ids: List[str]) -> Dict[str, Tuple[str, str]]:
    """
    Return latest Nextflow history status for each requested job id.

    Output map: {job_id: (status_token, duration)} where status_token is typically
    "OK", "ERR", or "-".
    """
    if not job_ids:
        return {}

    try:
        from paths import get_code_root
        history_path = get_code_root() / ".nextflow" / "history"
    except Exception:
        return {}

    if not history_path.exists():
        return {}

    target_ids = {str(jid) for jid in job_ids}
    found: Dict[str, Tuple[str, str]] = {}
    job_id_pattern = re.compile(r"--job_id\s+([0-9a-fA-F-]{36})")

    try:
        lines = history_path.read_text(errors="ignore").splitlines()
    except Exception as history_err:
        logger.debug(f"[COMPLETION] Could not read Nextflow history: {history_err}")
        return {}

    # Scan newest-first so we capture the latest status for each job.
    for line in reversed(lines):
        if len(found) >= len(target_ids):
            break
        if "--job_id" not in line:
            continue

        match = job_id_pattern.search(line)
        if not match:
            continue

        job_id = match.group(1)
        if job_id not in target_ids or job_id in found:
            continue

        parts = line.split("\t")
        status_token = parts[3].strip().upper() if len(parts) > 3 else ""
        duration = parts[1].strip() if len(parts) > 1 else ""
        found[job_id] = (status_token, duration)

    return found


def _compute_auto_limit(
    model_id: str,
    job_infos: List["JobInfo"],
    gpu_stats: List[Any],
    config: Dict[str, Any],
    running_jobs_per_gpu: Optional[Dict[int, int]] = None,
) -> int:
    """Compute a VRAM-based concurrency cap for a model."""
    reservations = [
        (j.scheduler_reservation_mb if j.scheduler_reservation_mb is not None else j.vram_estimate_mb)
        for j in job_infos if j.model_type == model_id
    ]
    reservation_estimate = _median([max(1, int(v)) for v in reservations if v is not None])
    if reservation_estimate is None:
        fallback_estimate = estimate_vram(model_id, 300)
        probe_job = JobInfo(
            id="auto-limit-probe",
            name="auto-limit-probe",
            model_type=model_id,
            vram_estimate_mb=fallback_estimate,
            sequence_length=300,
            priority=0,
            pinned_gpu=None,
            created_at=datetime.utcnow(),
            scheduler_reservation_mb=fallback_estimate,
        )
        reservation_estimate = _pending_job_reservation_mb(probe_job, {})
    reservation_estimate = max(1, int(reservation_estimate))

    running_jobs_per_gpu = running_jobs_per_gpu or {}
    limit = 0
    for gpu in gpu_stats:
        if is_gpu_disabled(gpu.index, config) and not _gpu_force_available(gpu.index, config):
            continue
        if model_id in HEAVY_MODELS:
            gpu_caps = GPU_CAPABILITIES.get(gpu.index, {'supports_heavy': True})
            if not gpu_caps.get('supports_heavy', True):
                continue

        safety_margin = _gpu_safety_margin_mb(gpu.index, config)
        target_fill = _gpu_target_fill(gpu.index, config)
        available = (gpu.memory_total_mb * target_fill) - gpu.memory_used_mb - safety_margin
        if available <= 0:
            continue

        slot_limit = math.floor(available / reservation_estimate)
        gpu_slot_cap = _gpu_max_concurrent_jobs(gpu.index, config)
        if gpu_slot_cap is not None:
            remaining_slots = max(0, gpu_slot_cap - running_jobs_per_gpu.get(gpu.index, 0))
            slot_limit = min(slot_limit, remaining_slots)

        limit += max(0, slot_limit)

    return max(0, limit)


def is_gpu_disabled(gpu_index: int, config: Dict[str, Any]) -> bool:
    """Check if a GPU is disabled in config."""
    overrides = config.get("overrides", {})
    gpu_override = overrides.get(str(gpu_index), {})
    return gpu_override.get("disabled", False)


def _gpu_override(gpu_index: int, config: Dict[str, Any]) -> Dict[str, Any]:
    return config.get("overrides", {}).get(str(gpu_index), {})


def _gpu_target_fill(gpu_index: int, config: Dict[str, Any]) -> float:
    global_fill = float(config.get("global", {}).get("target_vram_fill", 0.85))
    override_fill = _gpu_override(gpu_index, config).get("threshold")
    try:
        fill = float(override_fill if override_fill is not None else global_fill)
    except (TypeError, ValueError):
        fill = global_fill
    return max(0.05, min(0.99, fill))


def _gpu_safety_margin_mb(gpu_index: int, config: Dict[str, Any]) -> int:
    try:
        return max(0, int(_gpu_override(gpu_index, config).get("vram_safety_margin_mb", 500)))
    except (TypeError, ValueError):
        return 500


def _gpu_max_concurrent_jobs(gpu_index: int, config: Dict[str, Any]) -> Optional[int]:
    raw_value = _gpu_override(gpu_index, config).get("max_concurrent_jobs")
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _gpu_force_available(gpu_index: int, config: Dict[str, Any]) -> bool:
    return bool(_gpu_override(gpu_index, config).get("force_available", False))


def _gpu_quick_enable(gpu_index: int, config: Dict[str, Any]) -> bool:
    return bool(_gpu_override(gpu_index, config).get("quick_enable", False))


def get_workflow_pin(job_model_type: str, config: Dict[str, Any]) -> Optional[int]:
    """
    Check if a workflow type has a workflow-level GPU pin.
    
    Workflow pins are set via the API to route all jobs of a specific model_type
    (e.g., 'boltz', 'fampnn', 'rfantibody') to a specific GPU.
    
    Returns the pinned GPU index, or None if no workflow pin exists.
    """
    workflow_pins = config.get("workflow_pins", {})
    if job_model_type in workflow_pins:
        return workflow_pins[job_model_type]
    return None


def is_gpu_locked(gpu_index: int, batch_id: Optional[str], config: Dict[str, Any]) -> bool:
    """
    Check if a GPU is locked for exclusive use by a different batch.
    
    GPU locks are used when a user pins all child jobs in a batch to a single GPU
    AND locks that GPU from other workflows.
    
    Returns True if the GPU is locked by a DIFFERENT batch, False otherwise.
    """
    gpu_locks = config.get("gpu_locks", {})
    for locked_batch, locked_gpu in gpu_locks.items():
        if locked_gpu == gpu_index:
            # GPU is locked - only allow if this job is from the same batch
            if batch_id and batch_id == locked_batch:
                return False  # Same batch, allowed
            return True  # Different batch, blocked
    return False


def get_batch_lock_gpu(batch_id: Optional[str], config: Dict[str, Any]) -> Optional[int]:
    """
    Get the GPU that a batch has locked for exclusive use.
    
    Returns the locked GPU index, or None if no lock exists.
    """
    if not batch_id:
        return None
    gpu_locks = config.get("gpu_locks", {})
    return gpu_locks.get(batch_id)


# ═══════════════════════════════════════════════════════════════════════════════
# BIN-PACKING ALGORITHM
# ═══════════════════════════════════════════════════════════════════════════════

def pack_jobs_to_gpus(
    jobs: List[JobInfo],
    gpus: List[GPUState],
    target_fill: float,
    config: Dict[str, Any],
    running_jobs_per_gpu: Optional[Dict[int, int]] = None,
    gpu_last_launch_at: Optional[Dict[int, datetime]] = None,
) -> List[Tuple[JobInfo, int]]:
    """
    First Fit Decreasing bin-packing for GPU job assignment.
    
    Returns list of (job, gpu_index) tuples for jobs that can launch now.
    """
    if not jobs or not gpus:
        return []
    
    # ═══════════════════════════════════════════════════════════════════════
    # 1. FILTER GPUS - Exclude disabled GPUs
    # ═══════════════════════════════════════════════════════════════════════
    active_gpus = [
        g for g in gpus
        if (not is_gpu_disabled(g.index, config)) or _gpu_force_available(g.index, config) or _gpu_quick_enable(g.index, config)
    ]
    active_gpu_ids = {g.index for g in active_gpus}
    global_config = config.get("global", {})
    msa_preferred = _normalize_gpu_id_list(global_config.get("msa_preferred_gpu_ids")) or []
    msa_preferred_active = [gpu_id for gpu_id in msa_preferred if gpu_id in active_gpu_ids]
    msa_avoid_heavy = bool(global_config.get("msa_avoid_heavy_gpus", False))
    busy_threshold = max(0.0, min(1.0, float(global_config.get("busy_threshold", 0.5) or 0.0)))
    cooldown_ms = max(0, int(global_config.get("cooldown_ms", 0) or 0))
    cooldown_delta = timedelta(milliseconds=cooldown_ms)
    running_jobs_per_gpu = running_jobs_per_gpu or {}
    gpu_last_launch_at = gpu_last_launch_at or {}
    quick_enable_tokens: Dict[int, int] = {
        g.index: (1 if _gpu_quick_enable(g.index, config) else 0)
        for g in active_gpus
    }
    non_heavy_active_ids = {
        g.index for g in active_gpus
        if not GPU_CAPABILITIES.get(g.index, {'supports_heavy': True}).get('supports_heavy', True)
    }
    
    if not active_gpus:
        logger.warning("[PACK] No active GPUs available (all disabled)")
        return []
    if msa_preferred and not msa_preferred_active:
        logger.warning(f"[PACK] MSA preferred GPUs {msa_preferred} are unavailable; using general GPU selection")
    
    # ═══════════════════════════════════════════════════════════════════════
    # 2. SORT JOBS - Priority first, then VRAM descending, then age
    # ═══════════════════════════════════════════════════════════════════════
    sorted_jobs = sorted(
        jobs,
        key=lambda j: (
            -j.priority,
            -(j.scheduler_reservation_mb if j.scheduler_reservation_mb is not None else j.vram_estimate_mb),
            -j.vram_estimate_mb,
            j.created_at,
        )
    )
    
    # ═══════════════════════════════════════════════════════════════════════
    # 3. BUILD GPU STATE - Track projected VRAM usage
    # ═══════════════════════════════════════════════════════════════════════
    projected = {g.index: g.memory_used_mb for g in active_gpus}
    capacity = {g.index: g.memory_total_mb for g in active_gpus}
    projected_jobs_per_gpu = {g.index: running_jobs_per_gpu.get(g.index, 0) for g in active_gpus}
    
    # ═══════════════════════════════════════════════════════════════════════
    # 4. THE PACKING LOOP
    # ═══════════════════════════════════════════════════════════════════════
    assignments = []
    
    for job in sorted_jobs:
        best_gpu = None
        best_score = -float('inf')

        if job.model_type in PROTENIX_MODELS:
            has_protenix_gpu = any(
                GPU_CAPABILITIES.get(g.index, {'supports_protenix': True}).get('supports_protenix', True)
                for g in active_gpus
            )
            if not has_protenix_gpu:
                logger.warning(f"[PACK] {job.name}: No Protenix-compatible GPU available")
                continue
        
        # Log if this job has a multi-GPU allowlist
        if job.pinned_gpus is not None and len(job.pinned_gpus) > 0:
            filtered_allowlist = [g for g in job.pinned_gpus if g in active_gpu_ids]
            if not filtered_allowlist:
                logger.warning(f"[PACK] {job.name}: GPU allowlist has no active GPUs; skipping")
                continue
            if filtered_allowlist != job.pinned_gpus:
                logger.info(f"[PACK] {job.name}: GPU allowlist filtered to active GPUs {filtered_allowlist}")
            job = replace(job, pinned_gpus=filtered_allowlist)
        
        # ═══════════════════════════════════════════════════════════════════
        # PRE-CHECK: Determine forced GPU assignment from pins/locks
        # Priority: batch_lock > job.pinned_gpu > workflow_pin
        # ═══════════════════════════════════════════════════════════════════
        forced_gpu = None
        
        # Check 0a: Batch lock (highest priority - exclusive GPU for batch)
        batch_lock_gpu = get_batch_lock_gpu(getattr(job, 'batch_id', None), config)
        if batch_lock_gpu is not None:
            if batch_lock_gpu in active_gpu_ids:
                forced_gpu = batch_lock_gpu
                logger.debug(f"[PACK] {job.name}: Batch lock forces GPU {forced_gpu}")
            else:
                logger.warning(f"[PACK] {job.name}: Batch lock targets inactive GPU {batch_lock_gpu}")
                continue
        
        # Check 0b: Job-level pinning (user explicitly chose GPU for this job)
        elif job.pinned_gpu is not None:
            if job.pinned_gpu in active_gpu_ids:
                forced_gpu = job.pinned_gpu
                logger.debug(f"[PACK] {job.name}: Job pin forces GPU {forced_gpu}")
            else:
                logger.warning(f"[PACK] {job.name}: Job pin targets inactive GPU {job.pinned_gpu}")
                continue
        
        # Check 0c: Workflow-level pin (all jobs of this model_type go to specific GPU)
        else:
            workflow_pin = get_workflow_pin(job.model_type, config)
            if workflow_pin is not None:
                if workflow_pin in active_gpu_ids:
                    forced_gpu = workflow_pin
                    logger.debug(f"[PACK] {job.name}: Workflow pin ({job.model_type}) forces GPU {forced_gpu}")
                else:
                    logger.warning(f"[PACK] {job.name}: Workflow pin targets inactive GPU {workflow_pin}")
                    continue
        
        for gpu in active_gpus:
            gpu_caps = GPU_CAPABILITIES.get(gpu.index, {'supports_heavy': True})
            force_available = _gpu_force_available(gpu.index, config)
            quick_available = quick_enable_tokens.get(gpu.index, 0) > 0
            availability_override = force_available or quick_available
            
            # Check 1: Respect forced GPU assignment (pins/locks)
            if forced_gpu is not None:
                if forced_gpu != gpu.index:
                    continue
            
            # Check 1b: Multi-GPU allowlist (if specified, only use these GPUs)
            if job.pinned_gpus is not None and len(job.pinned_gpus) > 0:
                if gpu.index not in job.pinned_gpus:
                    continue  # This GPU is not in the allowlist

            # Check 1c: MSA preferred GPU allowlist from scheduler config (if active)
            if job.model_type == 'msa_batch' and msa_preferred_active:
                if gpu.index not in msa_preferred_active:
                    continue
            
            # Check 2: GPU lock exclusion (skip GPUs locked by other batches)
            job_batch_id = getattr(job, 'batch_id', None)
            if is_gpu_locked(gpu.index, job_batch_id, config):
                continue  # GPU is locked by a different batch

            # Check 2b: Disabled / busy / cooldown / per-GPU concurrent caps
            if is_gpu_disabled(gpu.index, config) and not availability_override:
                continue

            max_jobs = _gpu_max_concurrent_jobs(gpu.index, config)
            if max_jobs is not None and projected_jobs_per_gpu.get(gpu.index, 0) >= max_jobs and not availability_override:
                continue

            if busy_threshold > 0 and (gpu.utilization / 100.0) >= busy_threshold and not availability_override:
                continue

            last_launch_at = gpu_last_launch_at.get(gpu.index)
            if cooldown_ms > 0 and last_launch_at is not None and not availability_override:
                try:
                    if datetime.utcnow() - last_launch_at < cooldown_delta:
                        continue
                except Exception:
                    pass
            
            # Check 3: Model compatibility (heavy models skip 5060 Ti)
            if job.model_type in HEAVY_MODELS:
                if not gpu_caps.get('supports_heavy', True):
                    continue

            # Check 3b: Protenix compatibility (skip unsupported GPUs, e.g. Blackwell until stack update)
            if job.model_type in PROTENIX_MODELS:
                if not gpu_caps.get('supports_protenix', True):
                    continue
            
            # Check 4: VRAM availability (with per-GPU safety margin)
            gpu_override = _gpu_override(gpu.index, config)
            safety_margin = _gpu_safety_margin_mb(gpu.index, config)
            gpu_fill_target = _gpu_target_fill(gpu.index, config)
            available = (capacity[gpu.index] * gpu_fill_target) - projected[gpu.index] - safety_margin
            
            required = job.scheduler_reservation_mb if job.scheduler_reservation_mb is not None else job.vram_estimate_mb
            if required > available:
                continue  # Doesn't fit
            
            # ═══════════════════════════════════════════════════════════════
            # SCORING: Configurable weights for GPU preference
            # ═══════════════════════════════════════════════════════════════
            # Read weights from config
            capacity_weight = global_config.get("capacity_weight", 3.0)
            emptiness_weight = global_config.get("emptiness_weight", 5.0)
            
            # Check for per-GPU priority tier override
            priority_tier = gpu_override.get("priority_tier")
            
            current_utilization = projected[gpu.index] / capacity[gpu.index]
            
            # Calculate score
            if priority_tier is not None:
                # User-defined priority tier (higher = preferred)
                base_tier = priority_tier * 10
            else:
                # Auto-calculate from capacity
                # 5090 (32GB) → 9.6, 3090 (24GB) → 7.2, 5060 Ti (16GB) → 4.8
                base_tier = (capacity[gpu.index] / 10000) * capacity_weight
            
            emptiness_bonus = (1.0 - current_utilization) * emptiness_weight
            
            score = (
                base_tier
                + emptiness_bonus
                - gpu.index * 0.001  # Tie-breaker: prefer GPU 0
            )

            # Keep MSA lightweight by favoring non-heavy GPUs when requested.
            if job.model_type == 'msa_batch' and msa_avoid_heavy and non_heavy_active_ids:
                if gpu.index in non_heavy_active_ids:
                    score += 1000.0
                else:
                    score -= 1000.0
            
            if score > best_score:
                best_score = score
                best_gpu = gpu.index
        
        # ═══════════════════════════════════════════════════════════════════
        # ASSIGN IF FOUND
        # ═══════════════════════════════════════════════════════════════════
        if best_gpu is not None:
            assignments.append((job, best_gpu))
            projected[best_gpu] += job.scheduler_reservation_mb if job.scheduler_reservation_mb is not None else job.vram_estimate_mb
            projected_jobs_per_gpu[best_gpu] = projected_jobs_per_gpu.get(best_gpu, 0) + 1
            if quick_enable_tokens.get(best_gpu, 0) > 0:
                quick_enable_tokens[best_gpu] = 0
            
            logger.info(
                f"[PACK] {job.name} → GPU {best_gpu} | "
                f"reserve={job.scheduler_reservation_mb if job.scheduler_reservation_mb is not None else job.vram_estimate_mb}MB "
                f"(peak_est={job.vram_estimate_mb}MB) | "
                f"Projected: {projected[best_gpu]}/{capacity[best_gpu]}MB "
                f"({projected[best_gpu]/capacity[best_gpu]*100:.1f}%)"
            )
    
    # ═══════════════════════════════════════════════════════════════════════
    # SUMMARY LOGGING
    # ═══════════════════════════════════════════════════════════════════════
    if assignments:
        logger.info(f"[PACK COMPLETE] Assigned {len(assignments)} jobs this cycle")
        for gpu in active_gpus:
            util = projected[gpu.index] / capacity[gpu.index] * 100
            logger.info(f"  GPU {gpu.index}: {util:.1f}% projected")
    
    return assignments


def _summarize_queue_blockers(reason_buckets: Dict[str, List[int]]) -> List[str]:
    messages: List[str] = []
    for reason, gpu_ids in reason_buckets.items():
        unique_gpu_ids = sorted(set(gpu_ids))
        if unique_gpu_ids:
            gpu_list = ",".join(str(gpu_id) for gpu_id in unique_gpu_ids)
            messages.append(f"{reason} on GPU {gpu_list}")
        else:
            messages.append(reason)
    return messages


def build_queue_scheduler_diagnostics(
    queued_jobs: List[Any],
    running_jobs: List[Any],
    gpu_stats: List[Any],
    config: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """
    Replay scheduler packing logic for queued jobs and explain blockers.

    Returns per-job diagnostics:
    - scheduler_required_mb
    - scheduler_candidate_gpus
    - scheduler_ready
    - scheduler_blockers
    """
    diagnostics: Dict[str, Dict[str, Any]] = {}
    if not queued_jobs or not gpu_stats:
        return diagnostics

    job_infos: List[JobInfo] = []
    for job in queued_jobs:
        job_params = _normalize_job_params(getattr(job, "params", None))
        vram = getattr(job, "vram_estimate_mb", None)
        if vram is None:
            seq_len = getattr(job, "sequence_length", None) or 300
            vram = estimate_vram(getattr(job, "model_id", None) or "default", seq_len, job_params)
        job_infos.append(
            JobInfo(
                id=job.id,
                name=job.name,
                model_type=_effective_job_model_type(job),
                vram_estimate_mb=vram,
                sequence_length=getattr(job, "sequence_length", None) or 300,
                priority=getattr(job, "priority", None) or 0,
                pinned_gpu=job.pinned_gpu if isinstance(getattr(job, "pinned_gpu", None), int) else None,
                created_at=getattr(job, "created_at", None) or datetime.utcnow(),
                batch_id=getattr(job, "batch_id", None),
                pinned_gpus=_normalize_pinned_gpus(job_params.get("pinned_gpus")),
                scheduler_reservation_mb=vram,
            )
        )

    live_vram_by_job = collect_live_vram_by_job(running_jobs, gpu_stats)
    observed_live_by_model: Dict[str, List[int]] = {}
    running_jobs_per_gpu: Dict[int, int] = {}
    running_by_model: Dict[str, int] = {}
    gpu_last_launch_at: Dict[int, datetime] = {}
    for running_job in running_jobs:
        effective_model = _effective_job_model_type(running_job)
        running_by_model[effective_model] = running_by_model.get(effective_model, 0) + 1
        assigned_gpu = getattr(running_job, "assigned_gpu", None)
        if assigned_gpu is not None and job_uses_assigned_gpu(running_job):
            running_jobs_per_gpu[assigned_gpu] = running_jobs_per_gpu.get(assigned_gpu, 0) + 1
            started_at = getattr(running_job, "started_at", None)
            if isinstance(started_at, datetime):
                previous = gpu_last_launch_at.get(assigned_gpu)
                if previous is None or started_at > previous:
                    gpu_last_launch_at[assigned_gpu] = started_at
        live_vram = live_vram_by_job.get(running_job.id)
        if live_vram and live_vram > 0:
            observed_live_by_model.setdefault(effective_model, []).append(live_vram)

    for job_info in job_infos:
        job_info.scheduler_reservation_mb = _pending_job_reservation_mb(job_info, observed_live_by_model)
        diagnostics[job_info.id] = {
            "scheduler_required_mb": job_info.scheduler_reservation_mb,
            "scheduler_candidate_gpus": [],
            "scheduler_ready": False,
            "scheduler_blockers": [],
        }

    reservation_shortfall_by_gpu: Dict[int, int] = {}
    for running_job in running_jobs:
        gpu_idx = getattr(running_job, "assigned_gpu", None)
        if gpu_idx is None or not job_uses_assigned_gpu(running_job):
            continue
        live_vram = live_vram_by_job.get(running_job.id)
        reservation = _running_job_reservation_mb(running_job, live_vram)
        shortfall = reservation if not live_vram else max(0, reservation - live_vram)
        if shortfall > 0:
            reservation_shortfall_by_gpu[gpu_idx] = reservation_shortfall_by_gpu.get(gpu_idx, 0) + shortfall

    gpu_states = []
    for gpu in gpu_stats:
        effective_used = gpu.memory_used_mb + reservation_shortfall_by_gpu.get(gpu.index, 0)
        gpu_states.append(
            GPUState(
                index=gpu.index,
                name=gpu.name,
                memory_used_mb=effective_used,
                memory_total_mb=gpu.memory_total_mb,
                memory_free_mb=gpu.memory_total_mb - effective_used,
                utilization=gpu.utilization,
                temperature=gpu.temperature,
            )
        )

    global_config = config.get("global", {})
    active_gpus = [
        g for g in gpu_states
        if (not is_gpu_disabled(g.index, config)) or _gpu_force_available(g.index, config) or _gpu_quick_enable(g.index, config)
    ]
    active_gpu_ids = {g.index for g in active_gpus}
    msa_preferred = _normalize_gpu_id_list(global_config.get("msa_preferred_gpu_ids")) or []
    msa_preferred_active = [gpu_id for gpu_id in msa_preferred if gpu_id in active_gpu_ids]
    msa_avoid_heavy = bool(global_config.get("msa_avoid_heavy_gpus", False))
    busy_threshold = max(0.0, min(1.0, float(global_config.get("busy_threshold", 0.5) or 0.0)))
    cooldown_ms = max(0, int(global_config.get("cooldown_ms", 0) or 0))
    cooldown_delta = timedelta(milliseconds=cooldown_ms)
    quick_enable_tokens: Dict[int, int] = {
        g.index: (1 if _gpu_quick_enable(g.index, config) else 0)
        for g in active_gpus
    }
    projected = {g.index: g.memory_used_mb for g in active_gpus}
    capacity = {g.index: g.memory_total_mb for g in active_gpus}
    projected_jobs_per_gpu = {g.index: running_jobs_per_gpu.get(g.index, 0) for g in active_gpus}
    non_heavy_active_ids = {
        g.index for g in active_gpus
        if not GPU_CAPABILITIES.get(g.index, {'supports_heavy': True}).get('supports_heavy', True)
    }

    concurrency_limits = config.get("concurrency_limits", {})
    auto_limits: Dict[str, int] = {}
    for model_id, limit in concurrency_limits.items():
        if isinstance(limit, str) and limit.lower() == "auto":
            auto_limits[model_id] = _compute_auto_limit(
                model_id,
                job_infos,
                gpu_states,
                config,
                running_jobs_per_gpu=running_jobs_per_gpu,
            )

    sorted_jobs = sorted(
        job_infos,
        key=lambda j: (
            -j.priority,
            -(j.scheduler_reservation_mb if j.scheduler_reservation_mb is not None else j.vram_estimate_mb),
            -j.vram_estimate_mb,
            j.created_at,
        )
    )

    projected_by_model: Dict[str, int] = {}
    assignments: List[Tuple[JobInfo, int]] = []

    if not active_gpus:
        for job in sorted_jobs:
            diagnostics[job.id]["scheduler_blockers"] = ["no active GPUs available"]
        return diagnostics

    for job in sorted_jobs:
        job_diag = diagnostics[job.id]
        reason_buckets: Dict[str, List[int]] = {}
        candidate_gpu_scores: List[Tuple[float, int]] = []

        limit = concurrency_limits.get(job.model_type)
        if isinstance(limit, str) and limit.lower() == "auto":
            limit = auto_limits.get(job.model_type)
        if isinstance(limit, str):
            limit = None
        if limit is not None:
            current_for_model = running_by_model.get(job.model_type, 0) + projected_by_model.get(job.model_type, 0)
            if current_for_model >= limit:
                job_diag["scheduler_blockers"] = [f"model concurrency limit reached ({current_for_model}/{limit})"]
                continue

        if job.model_type in PROTENIX_MODELS:
            has_protenix_gpu = any(
                GPU_CAPABILITIES.get(g.index, {'supports_protenix': True}).get('supports_protenix', True)
                for g in active_gpus
            )
            if not has_protenix_gpu:
                job_diag["scheduler_blockers"] = ["no Protenix-compatible GPU available"]
                continue

        if job.pinned_gpus is not None and len(job.pinned_gpus) > 0:
            filtered_allowlist = [gpu_id for gpu_id in job.pinned_gpus if gpu_id in active_gpu_ids]
            if not filtered_allowlist:
                job_diag["scheduler_blockers"] = ["GPU allowlist has no active GPUs"]
                continue
            if filtered_allowlist != job.pinned_gpus:
                job = replace(job, pinned_gpus=filtered_allowlist)

        forced_gpu = None
        batch_lock_gpu = get_batch_lock_gpu(getattr(job, 'batch_id', None), config)
        if batch_lock_gpu is not None:
            if batch_lock_gpu in active_gpu_ids:
                forced_gpu = batch_lock_gpu
            else:
                job_diag["scheduler_blockers"] = [f"batch lock targets inactive GPU {batch_lock_gpu}"]
                continue
        elif job.pinned_gpu is not None:
            if job.pinned_gpu in active_gpu_ids:
                forced_gpu = job.pinned_gpu
            else:
                job_diag["scheduler_blockers"] = [f"pinned GPU {job.pinned_gpu} is inactive"]
                continue
        else:
            workflow_pin = get_workflow_pin(job.model_type, config)
            if workflow_pin is not None:
                if workflow_pin in active_gpu_ids:
                    forced_gpu = workflow_pin
                else:
                    job_diag["scheduler_blockers"] = [f"workflow pin targets inactive GPU {workflow_pin}"]
                    continue

        for gpu in active_gpus:
            gpu_caps = GPU_CAPABILITIES.get(gpu.index, {'supports_heavy': True, 'supports_protenix': True})
            force_available = _gpu_force_available(gpu.index, config)
            quick_available = quick_enable_tokens.get(gpu.index, 0) > 0
            availability_override = force_available or quick_available

            if forced_gpu is not None and forced_gpu != gpu.index:
                continue

            if job.pinned_gpus is not None and len(job.pinned_gpus) > 0 and gpu.index not in job.pinned_gpus:
                continue

            if job.model_type == 'msa_batch' and msa_preferred_active and gpu.index not in msa_preferred_active:
                reason_buckets.setdefault("not in MSA preferred GPU set", []).append(gpu.index)
                continue

            if is_gpu_locked(gpu.index, getattr(job, 'batch_id', None), config):
                reason_buckets.setdefault("locked by another batch", []).append(gpu.index)
                continue

            if is_gpu_disabled(gpu.index, config) and not availability_override:
                reason_buckets.setdefault("disabled", []).append(gpu.index)
                continue

            max_jobs = _gpu_max_concurrent_jobs(gpu.index, config)
            if max_jobs is not None and projected_jobs_per_gpu.get(gpu.index, 0) >= max_jobs and not availability_override:
                reason_buckets.setdefault("max concurrent jobs reached", []).append(gpu.index)
                continue

            if busy_threshold > 0 and (gpu.utilization / 100.0) >= busy_threshold and not availability_override:
                reason_buckets.setdefault("busy threshold reached", []).append(gpu.index)
                continue

            last_launch_at = gpu_last_launch_at.get(gpu.index)
            if cooldown_ms > 0 and last_launch_at is not None and not availability_override:
                try:
                    if datetime.utcnow() - last_launch_at < cooldown_delta:
                        reason_buckets.setdefault("launch cooldown active", []).append(gpu.index)
                        continue
                except Exception:
                    pass

            if job.model_type in HEAVY_MODELS and not gpu_caps.get('supports_heavy', True):
                reason_buckets.setdefault("requires heavy-capable GPU", []).append(gpu.index)
                continue

            if job.model_type in PROTENIX_MODELS and not gpu_caps.get('supports_protenix', True):
                reason_buckets.setdefault("requires Protenix-compatible GPU", []).append(gpu.index)
                continue

            safety_margin = _gpu_safety_margin_mb(gpu.index, config)
            gpu_fill_target = _gpu_target_fill(gpu.index, config)
            available = (capacity[gpu.index] * gpu_fill_target) - projected[gpu.index] - safety_margin
            required = job.scheduler_reservation_mb if job.scheduler_reservation_mb is not None else job.vram_estimate_mb
            if required > available:
                reason_buckets.setdefault("VRAM fill cap reached", []).append(gpu.index)
                continue

            gpu_override = _gpu_override(gpu.index, config)
            capacity_weight = global_config.get("capacity_weight", 3.0)
            emptiness_weight = global_config.get("emptiness_weight", 5.0)
            priority_tier = gpu_override.get("priority_tier")
            current_utilization = projected[gpu.index] / capacity[gpu.index]
            if priority_tier is not None:
                base_tier = priority_tier * 10
            else:
                base_tier = (capacity[gpu.index] / 10000) * capacity_weight
            emptiness_bonus = (1.0 - current_utilization) * emptiness_weight
            score = base_tier + emptiness_bonus - gpu.index * 0.001
            if job.model_type == 'msa_batch' and msa_avoid_heavy and non_heavy_active_ids:
                if gpu.index in non_heavy_active_ids:
                    score += 1000.0
                else:
                    score -= 1000.0
            candidate_gpu_scores.append((score, gpu.index))

        if not candidate_gpu_scores:
            job_diag["scheduler_blockers"] = _summarize_queue_blockers(reason_buckets) or ["no eligible GPU at current packing state"]
            continue

        candidate_gpu_scores.sort(reverse=True)
        best_gpu = candidate_gpu_scores[0][1]
        candidate_gpu_ids = [gpu_id for _score, gpu_id in candidate_gpu_scores]
        job_diag["scheduler_candidate_gpus"] = candidate_gpu_ids

        assignments.append((job, best_gpu))
        projected[best_gpu] += job.scheduler_reservation_mb if job.scheduler_reservation_mb is not None else job.vram_estimate_mb
        projected_jobs_per_gpu[best_gpu] = projected_jobs_per_gpu.get(best_gpu, 0) + 1
        projected_by_model[job.model_type] = projected_by_model.get(job.model_type, 0) + 1
        if quick_enable_tokens.get(best_gpu, 0) > 0:
            quick_enable_tokens[best_gpu] = 0

    max_launches_per_cycle = int(global_config.get("max_launches_per_cycle", 3) or 3)
    for assignment_index, (job, gpu_id) in enumerate(assignments):
        job_diag = diagnostics[job.id]
        if assignment_index < max_launches_per_cycle:
            job_diag["scheduler_ready"] = True
            job_diag["scheduler_blockers"] = []
        else:
            job_diag["scheduler_ready"] = False
            job_diag["scheduler_blockers"] = [f"waiting for launch burst limit ({max_launches_per_cycle}/cycle)"]
        if gpu_id not in job_diag["scheduler_candidate_gpus"]:
            job_diag["scheduler_candidate_gpus"] = [gpu_id] + list(job_diag["scheduler_candidate_gpus"])

    return diagnostics


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR LOOP
# ═══════════════════════════════════════════════════════════════════════════════

class GPUOrchestrator:
    """
    Background service that manages GPU job scheduling.
    
    Usage:
        orchestrator = GPUOrchestrator(db_session_factory, get_gpu_stats_fn, launch_job_fn)
        await orchestrator.start()
    """
    
    def __init__(
        self,
        db_session_factory,
        get_gpu_stats_fn,
        launch_nextflow_job_fn,
        poll_interval: float = 3.0
    ):
        self.db_session_factory = db_session_factory
        self.get_gpu_stats = get_gpu_stats_fn
        self.launch_nextflow_job = launch_nextflow_job_fn
        self.poll_interval = poll_interval
        self._running = False
        self._task = None
    
    async def start(self):
        """Start the orchestrator loop."""
        if self._running:
            logger.warning("Orchestrator already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("[ORCHESTRATOR] Started")
    
    async def stop(self):
        """Stop the orchestrator loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[ORCHESTRATOR] Stopped")
    
    async def _run_loop(self):
        """Main orchestrator loop."""
        while self._running:
            try:
                await self._process_cycle()
                # Poll progress for running jobs (updates stage_progress in DB)
                await self.update_running_job_progress()
                # Check for completed/failed jobs and update their status
                await self.check_job_completions()
            except Exception as e:
                logger.error(f"[ORCHESTRATOR] Error in cycle: {e}", exc_info=True)
            
            await asyncio.sleep(self.poll_interval)
    
    async def _process_cycle(self):
        """Single orchestrator cycle: check queue, pack jobs, launch."""
        # 1. Read config
        config = read_scheduler_config()
        
        if not config.get("global", {}).get("enabled", True):
            return  # Orchestrator disabled
        
        target_fill = config.get("global", {}).get("target_vram_fill", 0.75)
        
        # 2. Get pending jobs from database
        async with self.db_session_factory() as session:
            from sqlalchemy import select, and_, or_, func
            from database import Job
            
            # ═══════════════════════════════════════════════════════════════════
            # CRITICAL: MSA job limiting - only ONE MSA batch at a time
            # Multiple MSA jobs in parallel can cause DRAM pressure.
            # Concurrency is configurable via scheduler global.msa_concurrency_limit.
            # ═══════════════════════════════════════════════════════════════════
            running_msa_count_result = await session.execute(
                select(func.count()).where(
                    Job.model_id == 'msa_batch',
                    Job.queue_status == 'running'
                )
            )
            running_msa_count = running_msa_count_result.scalar() or 0
            msa_concurrency_limit = config.get("global", {}).get("msa_concurrency_limit", 1)
            try:
                msa_concurrency_limit = max(1, int(msa_concurrency_limit))
            except (TypeError, ValueError):
                msa_concurrency_limit = 1
            
            # Build the base query for queued jobs
            base_conditions = [
                Job.queue_status == "queued",
                Job.paused == False,
                Job.vram_estimate_mb.isnot(None),
                # CRITICAL: Exclude jobs waiting for parent MSA job to complete
                # Jobs with parent_job_id are linked to an MSA batch job
                # They get their queue_status changed from pending_msa -> queued only after MSA completes
                or_(Job.parent_job_id.is_(None), Job.queue_status != "pending_msa")
            ]
            
            # If MSA concurrency limit is reached, exclude additional MSA jobs.
            if running_msa_count >= msa_concurrency_limit:
                base_conditions.append(Job.model_id != 'msa_batch')
                logger.debug(
                    "[ORCHESTRATOR] MSA concurrency reached "
                    f"({running_msa_count}/{msa_concurrency_limit}); blocking additional MSA jobs"
                )
            
            result = await session.execute(
                select(Job).where(
                    and_(*base_conditions)
                ).order_by(
                    Job.priority.desc(),
                    Job.created_at
                )
            )
            pending_jobs = result.scalars().all()
            
            
            if not pending_jobs:
                return  # Nothing to do
            
            # ═══════════════════════════════════════════════════════════════════════
            # CPU-ONLY FAST PATH: Launch vram_estimate_mb == 0 jobs directly
            # These jobs (e.g. FASTQ-only nanopore) don't need GPU allocation.
            # ═══════════════════════════════════════════════════════════════════════
            cpu_only_jobs = [j for j in pending_jobs if (j.vram_estimate_mb or 0) == 0]
            gpu_jobs = [j for j in pending_jobs if (j.vram_estimate_mb or 0) > 0]
            
            for job in cpu_only_jobs:
                try:
                    await self.launch_nextflow_job(
                        job_id=job.id,
                        model_id=job.model_id,
                        mode=job.mode,
                        params={**job.params},  # No gpu_id injected
                        output_dir=job.output_dir
                    )
                    job.queue_status = "running"
                    job.assigned_gpu = None
                    job.started_at = datetime.utcnow()
                    logger.info(f"[LAUNCH CPU] {job.name} (no GPU, vram_estimate=0)")
                except Exception as e:
                    logger.error(f"[LAUNCH CPU FAILED] {job.name}: {e}")
                    job.queue_status = "failed"
                    job.error_message = str(e)
            
            if cpu_only_jobs:
                await session.commit()
            
            pending_jobs = gpu_jobs
            if not pending_jobs:
                return  # Only CPU jobs were queued
            
            # Concurrency limits applied later after GPU stats are available
            
            # Convert to JobInfo for packing
            job_infos = []
            for job in pending_jobs:
                job_params = _normalize_job_params(job.params)

                # Estimate VRAM if not set
                vram = job.vram_estimate_mb
                if vram is None:
                    seq_len = job.sequence_length or 300
                    model = job.model_id or 'default'
                    vram = estimate_vram(model, seq_len, job_params)
                
                # Extract pinned_gpus from job params if present
                pinned_gpus = _normalize_pinned_gpus(job_params.get('pinned_gpus'))
                
                effective_model = _effective_job_model_type(job)

                job_infos.append(JobInfo(
                    id=job.id,
                    name=job.name,
                    model_type=effective_model,
                    vram_estimate_mb=vram,
                    sequence_length=job.sequence_length or 300,
                    priority=job.priority or 0,
                    pinned_gpu=job.pinned_gpu if isinstance(job.pinned_gpu, int) else None,
                    created_at=job.created_at,
                    batch_id=getattr(job, 'batch_id', None),  # For GPU locking
                    pinned_gpus=pinned_gpus,  # Multi-GPU allowlist
                    scheduler_reservation_mb=vram,
                ))
            
            # 3. Get GPU state
            gpu_stats = self.get_gpu_stats()
            if not gpu_stats:
                logger.warning("[ORCHESTRATOR] No GPU stats available; skipping scheduling cycle")
                return

            # ═══════════════════════════════════════════════════════════════════════
            # Live-aware VRAM projection and running-job metadata
            # ═══════════════════════════════════════════════════════════════════════
            running_jobs_result = await session.execute(
                select(Job).where(
                    Job.queue_status == 'running',
                    Job.assigned_gpu.isnot(None),
                    Job.vram_estimate_mb.isnot(None)
                )
            )
            running_jobs = running_jobs_result.scalars().all()

            live_vram_by_job = _collect_live_vram_by_job_for_scheduler(running_jobs, gpu_stats)
            observed_live_by_model: Dict[str, List[int]] = {}
            running_jobs_per_gpu: Dict[int, int] = {}
            running_by_model: Dict[str, int] = {}
            gpu_last_launch_at: Dict[int, datetime] = {}
            for rj in running_jobs:
                effective_model = _effective_job_model_type(rj)
                running_by_model[effective_model] = running_by_model.get(effective_model, 0) + 1
                if rj.assigned_gpu is not None and job_uses_assigned_gpu(rj):
                    running_jobs_per_gpu[rj.assigned_gpu] = running_jobs_per_gpu.get(rj.assigned_gpu, 0) + 1
                    started_at = getattr(rj, "started_at", None)
                    if isinstance(started_at, datetime):
                        previous = gpu_last_launch_at.get(rj.assigned_gpu)
                        if previous is None or started_at > previous:
                            gpu_last_launch_at[rj.assigned_gpu] = started_at
                live_vram = live_vram_by_job.get(rj.id)
                if live_vram and live_vram > 0:
                    observed_live_by_model.setdefault(effective_model, []).append(live_vram)

            for job_info in job_infos:
                job_info.scheduler_reservation_mb = _pending_job_reservation_mb(job_info, observed_live_by_model)

            reservation_shortfall_by_gpu: Dict[int, int] = {}
            for rj in running_jobs:
                gpu_idx = rj.assigned_gpu
                if gpu_idx is None or not job_uses_assigned_gpu(rj):
                    continue
                live_vram = live_vram_by_job.get(rj.id)
                reservation = _running_job_reservation_mb(rj, live_vram)
                shortfall = reservation if not live_vram else max(0, reservation - live_vram)
                if shortfall > 0:
                    reservation_shortfall_by_gpu[gpu_idx] = reservation_shortfall_by_gpu.get(gpu_idx, 0) + shortfall

            if reservation_shortfall_by_gpu:
                logger.debug(f"[ORCHESTRATOR] Reservation shortfall by GPU: {reservation_shortfall_by_gpu}")

            gpu_states = []
            for g in gpu_stats:
                effective_used = g.memory_used_mb + reservation_shortfall_by_gpu.get(g.index, 0)
                gpu_states.append(GPUState(
                    index=g.index,
                    name=g.name,
                    memory_used_mb=effective_used,
                    memory_total_mb=g.memory_total_mb,
                    memory_free_mb=g.memory_total_mb - effective_used,
                    utilization=g.utilization,
                    temperature=g.temperature
                ))

            # ═══════════════════════════════════════════════════════════════════════
            # CONCURRENCY LIMITS: Filter jobs by per-model concurrent limits
            # ═══════════════════════════════════════════════════════════════════════
            concurrency_limits = config.get("concurrency_limits", {})
            if concurrency_limits:
                # Precompute auto limits
                auto_limits = {}
                for model_id, limit in concurrency_limits.items():
                    if isinstance(limit, str) and limit.lower() == "auto":
                        auto_limit = _compute_auto_limit(
                            model_id,
                            job_infos,
                            gpu_states,
                            config,
                            running_jobs_per_gpu=running_jobs_per_gpu,
                        )
                        auto_limits[model_id] = auto_limit

                # Filter pending jobs based on limits
                pending_by_id = {job.id: job for job in pending_jobs}
                filtered_infos = []
                skipped_by_limit = {}
                for job_info in job_infos:
                    limit = concurrency_limits.get(job_info.model_type)
                    if isinstance(limit, str) and limit.lower() == "auto":
                        limit = auto_limits.get(job_info.model_type)
                    if isinstance(limit, str):
                        limit = None
                    if limit is not None:
                        running_count = running_by_model.get(job_info.model_type, 0)
                        if running_count >= limit:
                            skipped_by_limit[job_info.model_type] = skipped_by_limit.get(job_info.model_type, 0) + 1
                            continue
                        filtered_for_model = len([j for j in filtered_infos if j.model_type == job_info.model_type])
                        if running_count + filtered_for_model >= limit:
                            skipped_by_limit[job_info.model_type] = skipped_by_limit.get(job_info.model_type, 0) + 1
                            continue
                    filtered_infos.append(job_info)

                if skipped_by_limit:
                    logger.info(f"[CONCURRENCY] Skipped jobs due to limits: {skipped_by_limit}")

                job_infos = filtered_infos
                pending_jobs = [pending_by_id[j.id] for j in job_infos]

                if not job_infos:
                    return  # All jobs blocked by concurrency limits
            
            # 4. Run bin-packing
            assignments = pack_jobs_to_gpus(
                job_infos,
                gpu_states,
                target_fill,
                config,
                running_jobs_per_gpu=running_jobs_per_gpu,
                gpu_last_launch_at=gpu_last_launch_at,
            )
            
            if not assignments:
                return  # No jobs could be packed
            
            # ═══════════════════════════════════════════════════════════════════════
            # CRITICAL: Limit launches per cycle to prevent RAM/VRAM overload
            # When many jobs queue at once (e.g., 40 FAMPNN children), launching them
            # all simultaneously causes system RAM exhaustion before GPU allocation.
            # Limit to 3 per cycle (~9 seconds), allowing VRAM to allocate before more.
            # ═══════════════════════════════════════════════════════════════════════
            max_launches_per_cycle = config.get("global", {}).get("max_launches_per_cycle", 3)
            assignments = assignments[:max_launches_per_cycle]
            
            if len(assignments) < len(job_infos):
                logger.info(f"[ORCHESTRATOR] Throttled to {len(assignments)}/{len(job_infos)} launches this cycle")

            # 5. Launch assigned jobs with stagger to prevent GPU initialization OOM
            used_quick_enable_gpu_ids = set()
            for i, (job_info, gpu_id) in enumerate(assignments):
                # Find the actual Job object
                job = next((j for j in pending_jobs if j.id == job_info.id), None)
                if not job:
                    continue
                
                # Stagger launches to prevent GPU memory racing (0.5 second delay between)
                # Reduced from 2.0s for faster throughput
                if i > 0:
                    await asyncio.sleep(0.5)
                
                try:
                    # Launch Nextflow with GPU assignment
                    await self.launch_nextflow_job(
                        job_id=job.id,
                        model_id=job.model_id,
                        mode=job.mode,
                        params={**job.params, 'gpu_id': gpu_id},
                        output_dir=job.output_dir
                    )
                    
                    # Update job status
                    job.queue_status = "running"
                    job.assigned_gpu = gpu_id
                    job.started_at = datetime.utcnow()
                    job.vram_estimate_mb = job_info.vram_estimate_mb

                    if _gpu_quick_enable(gpu_id, config):
                        used_quick_enable_gpu_ids.add(gpu_id)
                    
                    logger.info(f"[LAUNCH] {job.name} on GPU {gpu_id}")
                    
                except Exception as e:
                    logger.error(f"[LAUNCH FAILED] {job.name}: {e}")
                    job.queue_status = "failed"
                    job.error_message = str(e)

            if used_quick_enable_gpu_ids:
                for gpu_id in used_quick_enable_gpu_ids:
                    gpu_override = config.setdefault("overrides", {}).setdefault(str(gpu_id), {})
                    gpu_override["quick_enable"] = False
                write_scheduler_config(config)
            
            await session.commit()
    
    async def check_job_completions(self):
        """
        Check running jobs for completion or failure.
        
        Uses multiple detection strategies:
        1. PID check (if tracked)
        2. Job name in running processes
        3. Model-type processes on assigned GPU
        """
        import subprocess
        
        try:
            async with self.db_session_factory() as session:
                from sqlalchemy import select, func
                from database import Job, Design
                
                # Get jobs that are currently running
                result = await session.execute(
                    select(Job).where(
                        Job.queue_status == "running"
                    )
                )
                running_jobs = result.scalars().all()
                
                if not running_jobs:
                    return

                # Snapshot terminal status from Nextflow history. This lets us
                # reconcile jobs even when API-side launch monitoring was interrupted.
                history_status_by_job = _read_nextflow_history_statuses(
                    [str(job.id) for job in running_jobs]
                )

                # Cross-check against launcher-tracked Nextflow processes.
                # This prevents stale "completed" reconciliation when ps parsing
                # misses a still-active process.
                active_launch_jobs = set()
                try:
                    from services.nextflow import get_running_jobs
                    active_launch_jobs = set(get_running_jobs().keys())
                except Exception as proc_err:
                    logger.debug(f"[COMPLETION] Could not query launcher running jobs: {proc_err}")
                
                # Get all running processes once (expensive operation)
                try:
                    ps_result = subprocess.run(
                        ['ps', 'aux'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    all_processes = ps_result.stdout if ps_result.returncode == 0 else ""
                except Exception as e:
                    logger.debug(f"[COMPLETION] Failed to get process list: {e}")
                    all_processes = ""
                
                # Count FAMPNN/Nextflow processes per GPU
                gpu_has_activity = {}
                for line in all_processes.split('\n'):
                    if 'seq_design.py' in line or 'nextflow' in line:
                        # Try to extract gpu_id from the process args
                        gpu_match = re.search(r'gpu_id[=\s]+(\d+)', line)
                        if gpu_match:
                            gpu_id = int(gpu_match.group(1))
                            gpu_has_activity[gpu_id] = gpu_has_activity.get(gpu_id, 0) + 1
                
                stale_fail_after_seconds = 300
                try:
                    stale_fail_after_seconds = max(
                        60,
                        int(os.getenv("BMS_STALE_RECONCILE_FAIL_SECONDS", "300")),
                    )
                except Exception:
                    stale_fail_after_seconds = 300

                reconciled = 0
                for job in running_jobs:
                    job_is_running = False

                    # Method 0: Trust launcher's active process registry.
                    if job.id in active_launch_jobs:
                        job_is_running = True
                    
                    # Method 1: Check if job ID appears in any process (Nextflow uses --job_id UUID)
                    # Also check job name as fallback for non-Nextflow processes
                    if job.id in all_processes:
                        job_is_running = True
                    elif job.name in all_processes or job.name.replace(' ', '.') in all_processes:
                        job_is_running = True
                    
                    # Method 2: Check if there's any activity on the job's assigned GPU
                    if not job_is_running and job.assigned_gpu is not None:
                        if job.assigned_gpu in gpu_has_activity:
                            # GPU has activity - job might be running
                            # But we need to be more specific for FAMPNN
                            if 'fampnn' in job.name.lower() and 'seq_design' not in all_processes:
                                # No FAMPNN processes at all - job is done
                                job_is_running = False
                            else:
                                job_is_running = True
                    
                    # Method 3: If job has been running > 1 minute and no process found, reconcile state
                    if not job_is_running and job.started_at:
                        age_seconds = (datetime.utcnow() - job.started_at).total_seconds()
                        if age_seconds > 60:  # Only mark complete if running > 1 min
                            history_status = history_status_by_job.get(str(job.id))
                            history_outcome = None
                            history_note = None
                            child_wait_success = None
                            if history_status:
                                status_token, duration = history_status
                                duration_suffix = f", duration {duration}" if duration else ""
                                if status_token == "ERR":
                                    history_outcome = "failed"
                                    history_note = f"Reconciled from .nextflow/history (ERR{duration_suffix})"
                                elif status_token == "OK":
                                    history_outcome = "completed"
                                    history_note = f"Reconciled from .nextflow/history (OK{duration_suffix})"

                            if history_outcome != "failed":
                                current_stage_name = (job.current_stage or "").lower()
                                if "waitfor" in current_stage_name and "children" in current_stage_name:
                                    child_wait_success = _read_successful_child_wait_result(job.stage_work_dir)

                            failure_reason = history_note if history_outcome == "failed" else None
                            if failure_reason is None and job.error_message:
                                failure_reason = str(job.error_message)
                            elif failure_reason is None and history_outcome != "completed" and job.output_dir:
                                try:
                                    out_dir = Path(job.output_dir)
                                    nf_log = out_dir / "nextflow.log"
                                    if not nf_log.exists():
                                        nf_log = out_dir / ".nextflow.log"
                                    if nf_log.exists():
                                        tail = nf_log.read_text(errors="ignore")[-8000:]
                                        error_markers = (
                                            "ERROR ~ Error executing process",
                                            "terminated with an error exit status",
                                            "Nextflow exited with code",
                                            "CUDA out of memory",
                                        )
                                        for marker in error_markers:
                                            if marker in tail:
                                                failure_reason = f"Reconciled from nextflow.log: {marker}"
                                                break
                                except Exception as log_err:
                                    logger.debug(f"[COMPLETION] Could not inspect nextflow log for {job.name}: {log_err}")

                            interrupted_after_child_wait_reason = None
                            if failure_reason is None and history_outcome != "completed" and child_wait_success:
                                completed = child_wait_success["completed"]
                                total = child_wait_success["total"]
                                child_summary = f"{completed}/{total}" if total > 0 else str(completed)
                                interrupted_after_child_wait_reason = (
                                    "Launcher/API interruption after successful child aggregation: "
                                    f"{child_summary} child workflows completed in "
                                    f"{job.stage_work_dir}; resume is recommended."
                                )

                            if failure_reason:
                                if job.status == "running":
                                    job.status = "failed"
                                job.queue_status = "failed"
                                job.error_message = failure_reason
                                job.completed_at = datetime.utcnow()
                                logger.warning(
                                    f"[COMPLETION] {job.name} reconciled as failed "
                                    f"(no process found, age: {age_seconds:.0f}s): {failure_reason}"
                                )
                            elif history_outcome == "completed":
                                job.queue_status = "completed"
                                if job.status == "running":
                                    job.status = "completed"
                                job.current_stage = "Complete"
                                job.stage_progress = None
                                job.error_message = None
                                job.completed_at = datetime.utcnow()

                                try:
                                    # Best-effort safety net: if a top-level workflow completed but
                                    # launch task finalization was missed, ingest outputs so Data Viewer
                                    # is populated instead of showing an empty completed job.
                                    if job.parent_job_id is None and job.output_dir:
                                        existing_designs = (
                                            await session.execute(
                                                select(func.count(Design.id)).where(Design.job_id == job.id)
                                            )
                                        ).scalar() or 0
                                        if existing_designs == 0:
                                            from services.result_ingester import ingest_job_results

                                            created = await ingest_job_results(str(job.id), job.output_dir, session)
                                            logger.info(
                                                f"[COMPLETION] Ingested {created} designs "
                                                f"for reconciled top-level job {job.name}"
                                            )
                                    from services.nextflow import (
                                        maybe_trigger_batch_frustrampnn,
                                        maybe_trigger_mutation_seed_refinement,
                                    )
                                    await maybe_trigger_batch_frustrampnn(job, session)
                                    await maybe_trigger_mutation_seed_refinement(job, session)
                                except Exception as ingest_err:
                                    logger.warning(
                                        f"[COMPLETION] Reconcile ingestion failed for {job.name}: {ingest_err}"
                                    )
                                if history_note:
                                    logger.info(
                                        f"[COMPLETION] {job.name} completed (no process found, "
                                        f"age: {age_seconds:.0f}s): {history_note}"
                                    )
                                else:
                                    logger.info(
                                        f"[COMPLETION] {job.name} completed "
                                        f"(no process found, age: {age_seconds:.0f}s)"
                                    )
                            elif interrupted_after_child_wait_reason:
                                if job.status == "running":
                                    job.status = "failed"
                                job.queue_status = "failed"
                                job.error_message = interrupted_after_child_wait_reason
                                job.completed_at = datetime.utcnow()
                                logger.warning(
                                    f"[COMPLETION] {job.name} reconciled as interrupted after "
                                    f"successful child aggregation (age: {age_seconds:.0f}s): "
                                    f"{interrupted_after_child_wait_reason}"
                                )
                            else:
                                history_status = nextflow_history_status(job)
                                gate_present = has_stage_gate(job)
                                if history_status == "OK" or gate_present or job.awaiting_input:
                                    job.status = "awaiting_input"
                                    job.queue_status = "completed"
                                    job.paused = False
                                    job.assigned_gpu = None
                                    job.error_message = None
                                    if job.awaiting_stage:
                                        job.current_stage = job.awaiting_stage
                                    logger.info(
                                        f"[COMPLETION] {job.name} reconciled as awaiting input "
                                        f"(no process found, age: {age_seconds:.0f}s, "
                                        f"history_status={history_status or 'n/a'}, gate={gate_present})"
                                    )
                                elif age_seconds >= stale_fail_after_seconds:
                                    if job.status == "running":
                                        unresolved_reason = (
                                            "Reconciled as failed: no active process and no terminal "
                                            ".nextflow/history status (expected OK/ERR)"
                                        )
                                        job.status = "failed"
                                        job.queue_status = "failed"
                                        job.error_message = unresolved_reason
                                        job.completed_at = datetime.utcnow()
                                        logger.warning(
                                            f"[COMPLETION] {job.name} reconciled as failed "
                                            f"(no process found, age: {age_seconds:.0f}s): {unresolved_reason}"
                                        )
                                else:
                                    logger.info(
                                        f"[COMPLETION] {job.name} remains running while waiting "
                                        f"for terminal .nextflow/history state "
                                        f"(age: {age_seconds:.0f}s, threshold: {stale_fail_after_seconds}s)"
                                    )
                                    continue

                            reconciled += 1
                
                if reconciled > 0:
                    await session.commit()
                    logger.info(f"[COMPLETION] Reconciled {reconciled} stale running jobs")
                
        except Exception as e:
            logger.error(f"[COMPLETION] Error checking completions: {e}", exc_info=True)
    
    async def handle_oom_failures(self):
        """Handle OOM failures based on job's oom_tolerance setting."""
        # TODO: Implement by parsing Nextflow logs for OOM errors
        pass
    
    async def update_running_job_progress(self):
        """
        Poll stage progress for all running jobs.
        
        Reads work directory logs to extract granular progress (e.g., "5/30 designs").
        This runs every orchestrator cycle (~3 seconds) to provide live progress updates.
        """
        from services.nextflow import parse_stage_progress
        
        try:
            async with self.db_session_factory() as session:
                from sqlalchemy import select
                from database import Job
                
                # Get jobs that are currently running and have a work directory
                result = await session.execute(
                    select(Job).where(
                        Job.queue_status == "running",
                        Job.stage_work_dir.isnot(None)
                    )
                )
                running_jobs = result.scalars().all()
                
                for job in running_jobs:
                    if not job.current_stage or not job.stage_work_dir:
                        continue
                    
                    # Parse progress from work directory log
                    total_designs = None
                    job_params = _normalize_job_params(job.params)
                    if job_params:
                        total_designs = job_params.get('rfantibody_num_designs') or job_params.get('num_designs')
                    
                    progress = parse_stage_progress(
                        job.stage_work_dir,
                        job.current_stage,
                        total_designs
                    )
                    
                    if progress and progress != job.stage_progress:
                        job.stage_progress = progress
                        logger.debug(f"[ORCHESTRATOR] Job {job.name} progress: {progress}")
                
                await session.commit()
                
        except Exception as e:
            logger.debug(f"[ORCHESTRATOR] Error updating progress: {e}")
    
# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE TESTING
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Quick test of packing algorithm
    logging.basicConfig(level=logging.INFO)
    
    # Mock data
    test_gpus = [
        GPUState(0, "RTX 5090", 5000, 32607, 27607, 10, 45),
        GPUState(1, "RTX 5060 Ti", 2000, 16311, 14311, 5, 40),
        GPUState(2, "RTX 3090", 8000, 24576, 16576, 15, 50),
        GPUState(3, "RTX 3090", 3000, 24576, 21576, 8, 48),
    ]
    
    test_jobs = [
        JobInfo("1", "boltz_job_1", "boltz", 12000, 500, 0, None, datetime.now()),
        JobInfo("2", "rf3_job_1", "rf3", 14000, 600, 0, None, datetime.now()),
        JobInfo("3", "mpnn_job_1", "mpnn", 2000, 200, 0, None, datetime.now()),
        JobInfo("4", "mpnn_job_2", "mpnn", 2000, 200, 0, None, datetime.now()),
        JobInfo("5", "mpnn_job_3", "mpnn", 2000, 200, 0, None, datetime.now()),
        JobInfo("6", "fampnn_job_1", "fampnn", 4000, 300, 0, None, datetime.now()),
    ]
    
    config = {"global": {"target_vram_fill": 0.80, "enabled": True}, "overrides": {}}
    
    print("\n=== BIN PACKING TEST ===\n")
    assignments = pack_jobs_to_gpus(test_jobs, test_gpus, 0.80, config)
    
    print(f"\nTotal assigned: {len(assignments)} / {len(test_jobs)} jobs")
