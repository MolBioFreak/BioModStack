#!/usr/bin/env python3
"""
Cleanup Nextflow work directories older than a retention window.
Usage: python scripts/cleanup_workdirs.py [--days N | --hours N]
"""

import argparse
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

PROJECT_ROOT = Path(__file__).parent.parent.absolute()
WORK_DIR = PROJECT_ROOT / "work"


def parse_args():
    parser = argparse.ArgumentParser(description="Cleanup Nextflow work directories.")
    parser.add_argument("--days", type=float, default=None, help="Delete tasks older than N days")
    parser.add_argument("--hours", type=float, default=72, help="Delete tasks older than N hours (default: 72)")
    return parser.parse_args()

def cleanup_work_directories(max_age_hours: float):
    """Delete Nextflow task directories older than max_age_hours."""
    if not WORK_DIR.exists():
        logger.info(f"Work directory {WORK_DIR} does not exist. Nothing to clean.")
        return

    logger.info(f"Starting cleanup of {WORK_DIR} (retention: {max_age_hours} hours)")
    
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
                
                if age_hours > max_age_hours:
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
    args = parse_args()
    max_age_hours = args.hours if args.days is None else args.days * 24
    cleanup_work_directories(max_age_hours)
