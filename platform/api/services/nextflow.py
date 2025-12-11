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
            # Run Nextflow
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(PROJECT_ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "NXF_ANSI_LOG": "false"}
            )
            
            # Store process reference for potential cancellation
            _running_processes[job_id] = process
            
            # Store the Nextflow run ID (PID for now)
            job.nextflow_run_id = str(process.pid)
            await session.commit()
            
            # Wait for completion
            stdout, _ = await process.communicate()
            
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
                    if process.returncode == 0:
                        job.status = JobStatus.COMPLETED.value
                        
                        # Ingest results into Design table
                        try:
                            from services.result_ingester import ingest_job_results
                            design_count = await ingest_job_results(job_id, output_dir, session)
                            logger.info(f"Ingested {design_count} designs for job {job_id}")
                        except Exception as ingest_err:
                            logger.warning(f"Result ingestion failed: {ingest_err}")
                            
                    # Check for cancellation exit codes (SIGTERM=15/-15/143, SIGKILL=9/-9/137)
                    elif process.returncode in (-15, -9, 143, 137):
                        job.status = JobStatus.CANCELLED.value
                        job.error_message = "Job cancelled by user"
                        logger.info(f"Job {job_id} exit code {process.returncode} interpreted as CANCELLED")
                        
                    else:
                        job.status = JobStatus.FAILED.value
                        job.error_message = f"Nextflow exited with code {process.returncode}"
                
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
    
    # Special case: BoltzGen standalone uses 'boltzgen' profile
    if model_id == 'boltzgen':
        profile = "boltzgen,workstation_ryzen7960x"

    
    # Base command
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
        # BoltzGen param mapping
        'target_pdb': 'boltzgen_target_pdb',
        'ligand_description': 'boltzgen_ligand_smiles',
        # Boltz-2 structure prediction params
        'boltz_recycling_steps': 'boltz_recycling_steps',
        'boltz_sampling_steps': 'boltz_sampling_steps',
        'boltz_num_samples': 'boltz_num_samples',
        'boltz_use_msa': 'boltz_use_msa',
        'boltz_method': 'boltz_method',
        # RF3 structure prediction params
        'rf3_num_recycles': 'rf3_num_recycles',
        'rf3_num_samples': 'rf3_num_samples',
        'rf3_early_stopping_plddt': 'rf3_early_stopping_plddt',
        # Sequence input
        'sequence': 'sequence_input',
        'sequence_name': 'sequence_name',
    }
    
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
