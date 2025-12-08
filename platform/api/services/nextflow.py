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


async def launch_nextflow_job(
    job_id: str,
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
    
    logger.info(f"Launching job {job_id} with mode {mode}")
    
    # Build Nextflow command
    cmd = build_nextflow_command(mode, params, output_dir)
    
    async with async_session() as session:
        # Update job to running
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        
        if not job:
            logger.error(f"Job {job_id} not found in database")
            return
        
        job.status = JobStatus.RUNNING.value
        job.started_at = datetime.utcnow()
        await session.commit()
        
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
            
            if job and job.status == JobStatus.RUNNING.value:
                if process.returncode == 0:
                    job.status = JobStatus.COMPLETED.value
                    # TODO: Parse results and populate designs table
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
                job.status = JobStatus.FAILED.value
                job.error_message = str(e)
                job.completed_at = datetime.utcnow()
                await session.commit()


def build_nextflow_command(
    mode: str,
    params: Dict[str, Any],
    output_dir: str
) -> list:
    """Build the Nextflow command line."""
    cmd = [
        "nextflow", "run", "main.nf",
        "-profile", f"{mode},workstation_ryzen7960x",
        "--out_dir", output_dir,
    ]
    
    # Add additional parameters
    param_mapping = {
        "rfd_num_designs": "--rfd_num_designs",
        "seqs_per_design": "--seqs_per_design",
        "rfd_contigs": "--rfd_contigs",
        "rfd_input_pdb": "--rfd_input_pdb",
        "rfd_hotspots": "--rfd_hotspots",
        "seq_method": "--seq_method",
        "pred_method": "--pred_method",
    }
    
    for param_key, cli_flag in param_mapping.items():
        if param_key in params and params[param_key] is not None:
            cmd.extend([cli_flag, str(params[param_key])])
    
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
