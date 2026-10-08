"""
Nextflow job launcher service.

Handles launching and managing Nextflow pipeline processes.
"""

import asyncio
import subprocess
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# Track running processes
_running_processes: Dict[str, subprocess.Popen] = {}

# Project root (parent of platform directory)
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


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
    gpu_id = params.get('gpu_id', 0)
    reference_sequence = params.get('reference_sequence', '')
    
    # Build batch_msa.py command
    script_path = PROJECT_ROOT / "scripts" / "batch_msa.py"
    cmd = [
        "python3", str(script_path),
        "--sequences", sequences_json,
        "--output_dir", output_dir,
        "--db_path", "/mnt/BioModStack/colabfold_db",
        "--cache_dir", "/mnt/BioModStack/msa_cache",
        "--gpu_id", str(gpu_id),
    ]
    if reference_sequence:
        cmd.extend(["--reference_sequence", reference_sequence])
    
    logger.info(f"[MSA BATCH] Command: {' '.join(cmd[:6])}...")
    
    try:
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
                
                # Unlock child inference jobs
                await session.commit()
                await unlock_child_inference_jobs(job_id, job.msa_manifest_path)
            else:
                job.status = JobStatus.FAILED.value
                job.queue_status = 'failed'
                job.error_message = f"MSA batch failed with exit code {exit_code}"
                logger.error(f"[MSA BATCH] Job {job_id} failed: exit code {exit_code}")
                await session.commit()
    
    except Exception as e:
        logger.error(f"[MSA BATCH] Job {job_id} error: {e}")
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
                    msa_paths[seq_info.get("name", "")] = seq_info.get("msa_path")
        except Exception as e:
            logger.warning(f"[MSA COMPLETE] Could not parse manifest: {e}")
        
        # Update each child job
        for job in child_jobs:
            job.queue_status = 'queued'  # Now ready for inference!
            logger.info(f"[MSA COMPLETE] Unlocked {job.name} for inference")
        
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
    
    # ═══════════════════════════════════════════════════════════════════════════
    # MSA BATCH JOBS: Run batch_msa.py directly (not Nextflow)
    # ═══════════════════════════════════════════════════════════════════════════
    if model_id == 'msa_batch':
        await launch_msa_batch_job(job_id, params, output_dir)
        return
    
    # Build Nextflow command
    cmd = build_nextflow_command(model_id, mode, params, output_dir)
    logger.info(f"Nextflow command: {' '.join(cmd)}")
    
    async with async_session() as session:
        # Update job to running
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        
        if not job:
            logger.error(f"Job {job_id} not found in database")
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
            gpu_id = params.get('gpu_id')
            
            # Build environment with GPU pinning
            env = {**os.environ, "NXF_ANSI_LOG": "false"}
            if gpu_id is not None:
                env["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
                logger.info(f"[GPU] Job {job_id} pinned to GPU {gpu_id} via CUDA_VISIBLE_DEVICES")
            else:
                logger.warning(f"[GPU] Job {job_id} has no gpu_id - using default GPU selection")
            
            # Run Nextflow with stdout piped for real-time monitoring
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(PROJECT_ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env
            )
            
            # Store process reference for potential cancellation
            _running_processes[job_id] = process
            
            # Store the Nextflow run ID (PID for now)
            job.nextflow_run_id = str(process.pid)
            await session.commit()
            
            # ═══════════════════════════════════════════════════════════════════
            # STREAM OUTPUT & MONITOR PROGRESS
            # ═══════════════════════════════════════════════════════════════════
            full_log = []
            import re
            # Regex to capture process name: "[... ] process > PROCESS_NAME (tag) [ 10%]"
            # We want "PROCESS_NAME"
            process_regex = re.compile(r"process >\s+([^(\[]+)")
            
            last_stage = None
            
            # Read line by line
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                    
                line_str = line.decode('utf-8', errors='replace')
                full_log.append(line_str)
                
                # Check for stage update
                # Example: "[4b/123456] process > NF_CORE:FAMPNN (1) [ 0%]"
                match = process_regex.search(line_str)
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
                    elif 'proteinmpnn' in stage_clean or 'mpnn' in stage_clean:
                        stage = 'proteinmpnn'
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
                                    await update_session.commit()
                        except Exception as db_err:
                            logger.warning(f"Failed to update stage for {job_id}: {db_err}")

            # Wait for process to fully exit
            exit_code = await process.wait()
            
            # Save Nextflow execution log to output directory
            try:
                log_path = Path(output_dir) / "nextflow.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(log_path, "w") as f:
                    f.writelines(full_log)
            except Exception as log_err:
                logger.warning(f"Failed to save nextflow.log: {log_err}")
            
            # Remove from running processes
            _running_processes.pop(job_id, None)
            
            # Update final status
            result = await session.execute(select(Job).where(Job.id == job_id))
            job = result.scalar_one_or_none()
            
            if job:
                # Refresh status to see if it was cancelled by API while we waited
                await session.refresh(job)
                
                if job.status == JobStatus.CANCELLED.value:
                    logger.info(f"Job {job_id} was cancelled, keeping CANCELLED status")
                    
                elif job.status == JobStatus.RUNNING.value:
                    if exit_code == 0:
                        job.status = JobStatus.COMPLETED.value
                        job.current_stage = "Complete" # Clear stage
                        
                        # Ingest results into Design table
                        try:
                            from services.result_ingester import ingest_job_results
                            design_count = await ingest_job_results(job_id, output_dir, session)
                            logger.info(f"Ingested {design_count} designs for job {job_id}")
                        except Exception as ingest_err:
                            logger.warning(f"Result ingestion failed: {ingest_err}")
                            
                    # Check for cancellation exit codes (SIGTERM=15/-15/143, SIGKILL=9/-9/137)
                    elif exit_code in (-15, -9, 143, 137):
                        job.status = JobStatus.CANCELLED.value
                        job.error_message = "Job cancelled by user"
                        logger.info(f"Job {job_id} exit code {exit_code} interpreted as CANCELLED")
                        
                    else:
                        job.status = JobStatus.FAILED.value
                        job.error_message = f"Nextflow exited with code {exit_code}"
                        logger.error(f"Nextflow failed for job {job_id} with code {exit_code}")
                        
                        # Log last few lines
                        logger.error(f"Tail of log:\n{''.join(full_log[-20:])}")
                
                job.completed_at = datetime.utcnow()
                await session.commit()
                
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
                    job.error_message = str(e)
                    job.completed_at = datetime.utcnow()
                    await session.commit()


def build_nextflow_command(
    model_id: str,
    mode: str,
    params: Dict[str, Any],
    output_dir: str
) -> list:
    """
    Build the Nextflow command line dynamically.
    
    Converts all params to --key value flags.
    """
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
        # Mutagenesis batch workflow - routes to boltz for structure prediction
        ('mutagenesis', 'batch_predict'): 'boltz',
        # Antibody workflows use boltz profile (Boltz2 is the structure predictor)
        ('antibody_denovo', 'antibody_denovo_pipeline'): 'boltz',
        ('antibody_denovo', 'default'): 'boltz',
        # Batch validation jobs (spawned by antibody_denovo logic)
        ('antibody_child', 'validation_batch'): 'boltz',
    }
    
    # Determine profile based on model and mode
    if (model_id, mode) in model_mode_to_profile:
        effective_profile = model_mode_to_profile[(model_id, mode)]
    elif mode in mode_to_profile:
        effective_profile = mode_to_profile[mode]
    else:
        effective_profile = mode
    
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
    else:
        cmd = [
            "nextflow", "run", "main.nf",
            "-profile", profile,
            "--out_dir", output_dir,
        ]
    
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
        # RF3 structure prediction params
        'rf3_num_recycles': 'rf3_num_recycles',
        'rf3_num_samples': 'rf3_num_samples',
        'rf3_early_stopping_plddt': 'rf3_early_stopping_plddt',
        # Sequence input
        'sequence': 'sequence_input',
        'sequence_name': 'sequence_name',
        # Parallelization
        'num_parallel_jobs': 'num_parallel_jobs',
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
            'target_pdb': 'boltzgen_target_pdb',
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
        }
        for src_key, dest_key in boltzgen_mappings.items():
            if src_key in params:
                params[dest_key] = params.pop(src_key)
    
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
        os.kill(pid, 15)  # SIGTERM
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
        if proc.poll() is None
    }
