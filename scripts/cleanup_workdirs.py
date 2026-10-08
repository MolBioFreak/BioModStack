#!/usr/bin/env python3
"""
Cleanup Nextflow work directories older than 72 hours.
Usage: python scripts/cleanup_workdirs.py
"""

import os
import time
import shutil
from pathlib import Path
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
PROJECT_ROOT = Path(__file__).parent.parent.absolute()
WORK_DIR = PROJECT_ROOT / "work"
MAX_AGE_HOURS = 72
MAX_AGE_SECONDS = MAX_AGE_HOURS * 3600

def cleanup_work_directories():
    """Delete Nextflow task directories older than MAX_AGE_HOURS."""
    if not WORK_DIR.exists():
        logger.info(f"Work directory {WORK_DIR} does not exist. Nothing to clean.")
        return

    logger.info(f"Starting cleanup of {WORK_DIR} (retention: {MAX_AGE_HOURS} hours)")
    
    now = time.time()
    deleted_count = 0
    retained_count = 0
    
    # Nextflow work structure: work/xx/yyyyyyyyyy...
    # Iterate through first level (2-char hex prefix)
    for prefix_dir in WORK_DIR.iterdir():
        if not prefix_dir.is_dir():
            continue
            
        # Iterate through second level (task hash)
        for task_dir in prefix_dir.iterdir():
            if not task_dir.is_dir():
                continue
                
            try:
                # Check modification time
                stats = task_dir.stat()
                mtime = stats.st_mtime
                age_hours = (now - mtime) / 3600
                
                if age_hours > MAX_AGE_HOURS:
                    logger.info(f"Deleting {task_dir.relative_to(PROJECT_ROOT)} (Age: {age_hours:.1f}h)")
                    shutil.rmtree(task_dir)
                    deleted_count += 1
                    
                    # If prefix dir empty, remove it too? 
                    # Usually safe, Nextflow recreates.
                else:
                    retained_count += 1
                    
            except Exception as e:
                logger.error(f"Error checking/deleting {task_dir}: {e}")

    # Cleanup empty prefix directories
    for prefix_dir in WORK_DIR.iterdir():
        if prefix_dir.is_dir() and not any(prefix_dir.iterdir()):
            try:
                prefix_dir.rmdir()
            except Exception as e:
                pass

    logger.info(f"Cleanup complete. Deleted: {deleted_count}, Retained: {retained_count}")

if __name__ == "__main__":
    cleanup_work_directories()
