"""
System administration routes for cache cleanup and maintenance tasks
"""
from fastapi import APIRouter
from pydantic import BaseModel
from pathlib import Path
import subprocess
import shutil

router = APIRouter(prefix="/system", tags=["system"])

# Get project root (two levels up from api directory)
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


class CleanupResult(BaseModel):
    success: bool
    message: str
    files_before: int
    files_after: int
    space_freed: str


class DiskUsage(BaseModel):
    work_dir_size: str
    work_dir_files: int
    pdj_results_size: str
    pdj_results_files: int


@router.get("/disk-usage", response_model=DiskUsage)
async def get_disk_usage():
    """Get disk usage for pipeline directories"""
    work_dir = PROJECT_ROOT / "work"
    results_dir = PROJECT_ROOT / "pdj_results"
    
    def get_dir_stats(path: Path) -> tuple:
        if not path.exists():
            return "0B", 0
        try:
            # Get file count
            file_count = sum(1 for _ in path.rglob("*") if _.is_file())
            # Get size using du
            result = subprocess.run(
                ["du", "-sh", str(path)],
                capture_output=True, text=True, timeout=60
            )
            size = result.stdout.split()[0] if result.returncode == 0 else "?"
            return size, file_count
        except Exception:
            return "?", 0
    
    work_size, work_files = get_dir_stats(work_dir)
    results_size, results_files = get_dir_stats(results_dir)
    
    return DiskUsage(
        work_dir_size=work_size,
        work_dir_files=work_files,
        pdj_results_size=results_size,
        pdj_results_files=results_files
    )


@router.post("/cleanup-work", response_model=CleanupResult)
async def cleanup_work_directory(days: int = 30):
    """
    Clean up Nextflow work directory.
    
    Args:
        days: Delete files older than this many days. Use 0 for full purge.
    """
    work_dir = PROJECT_ROOT / "work"
    
    if not work_dir.exists():
        return CleanupResult(
            success=True,
            message="Work directory does not exist",
            files_before=0,
            files_after=0,
            space_freed="0B"
        )
    
    # Count files before
    files_before = sum(1 for _ in work_dir.rglob("*") if _.is_file())
    
    # Get size before
    try:
        result = subprocess.run(
            ["du", "-sh", str(work_dir)],
            capture_output=True, text=True, timeout=60
        )
        size_before = result.stdout.split()[0] if result.returncode == 0 else "?"
    except Exception:
        size_before = "?"
    
    try:
        if days == 0:
            # Full purge - delete all contents
            for item in work_dir.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            message = "Full purge completed"
        else:
            # Delete files older than N days
            subprocess.run(
                ["find", str(work_dir), "-type", "f", "-mtime", f"+{days}", "-delete"],
                timeout=300
            )
            # Clean up empty directories
            subprocess.run(
                ["find", str(work_dir), "-type", "d", "-empty", "-delete"],
                timeout=60
            )
            message = f"Deleted files older than {days} days"
        
        # Count files after
        files_after = sum(1 for _ in work_dir.rglob("*") if _.is_file())
        
        # Get size after
        try:
            result = subprocess.run(
                ["du", "-sh", str(work_dir)],
                capture_output=True, text=True, timeout=60
            )
            size_after = result.stdout.split()[0] if result.returncode == 0 else "?"
        except Exception:
            size_after = "?"
        
        return CleanupResult(
            success=True,
            message=message,
            files_before=files_before,
            files_after=files_after,
            space_freed=f"{size_before} → {size_after}"
        )
        
    except Exception as e:
        return CleanupResult(
            success=False,
            message=f"Cleanup failed: {str(e)}",
            files_before=files_before,
            files_after=files_before,
            space_freed="0B"
        )
