#!/usr/bin/env python3
"""BioModStack Environment Validation Script.

Validates all BMS_* environment variables, checks directory existence,
and reports missing weights/containers. Run before first job submission.
"""

from __future__ import annotations

import os
import sys
import argparse
from pathlib import Path

# Import from platform if available, fallback to inline definitions
try:
    from platform.api.paths import (
        get_code_root,
        get_data_root,
        get_container_dir,
        get_weights_root,
        get_rfd_models_dir,
        get_colabfold_db,
        get_msa_cache_dir,
        get_sabdab_cache_dir,
        get_db_path,
        get_results_dir,
    )
except ImportError:
    # Fallback definitions for standalone use
    def _resolve(val: str) -> Path:
        return Path(os.path.expanduser(val)).resolve()

    def _default_root() -> Path:
        return Path.home() / ".biomodstack"

    def get_code_root() -> Path:
        env = os.getenv("BMS_HOME")
        return _resolve(env) if env else Path(__file__).resolve().parents[1]

    def get_data_root() -> Path:
        env = os.getenv("BMS_DATA")
        return _resolve(env) if env else get_code_root()

    def get_container_dir() -> Path:
        env = os.getenv("BMS_CONTAINER_DIR")
        return _resolve(env) if env else get_data_root() / "apptainer"

    def get_weights_root() -> Path:
        env = os.getenv("BMS_WEIGHTS")
        return _resolve(env) if env else _default_root() / "weights"

    def get_rfd_models_dir() -> Path:
        env = os.getenv("BMS_RFD_MODELS")
        if env:
            return _resolve(env)
        weights_root = get_weights_root()
        default_dir = weights_root / "rfd"
        if default_dir.exists():
            return default_dir
        rfantibody_dir = weights_root / "rfantibody" / "rfantibody_repo" / "weights"
        if (rfantibody_dir / "RFdiffusion_Ab.pt").exists():
            return rfantibody_dir
        return default_dir

    def get_colabfold_db() -> Path:
        env = os.getenv("BMS_COLABFOLD_DB")
        return _resolve(env) if env else _default_root() / "colabfold_db"

    def get_msa_cache_dir() -> Path:
        env = os.getenv("BMS_MSA_CACHE")
        return _resolve(env) if env else _default_root() / "msa_cache"

    def get_sabdab_cache_dir() -> Path:
        env = os.getenv("BMS_SABDAB_CACHE")
        return _resolve(env) if env else _default_root() / "sabdab_cache"

    def get_db_path() -> Path:
        env = os.getenv("BMS_DB_PATH")
        return _resolve(env) if env else get_data_root() / "biomodstack.db"

    def get_results_dir() -> Path:
        return get_data_root() / "bms_results"


# Required weight subdirectories for core workflows
REQUIRED_WEIGHTS = [
    "alphafold/params",
    "boltz",
]

# Optional weight directories (warn but don't fail)
OPTIONAL_WEIGHTS = [
    "ppiflow",
    "rfdpoly",
    "frustrampnn",
]

# Optional container images to verify in container_dir (warn-only)
OPTIONAL_CONTAINERS = [
    "af2.sif",
    "boltz2.sif",
    "boltzgen.sif",
    "antibody_tools.sif",
    "stability_tools.sif",
    "iggm.sif",
    "frustrampnn.sif",
]


def check_env_var(name: str) -> tuple[bool, str]:
    """Check if env var is set and return its value."""
    val = os.getenv(name)
    return (True, val) if val else (False, "(not set)")


def check_directory(path: Path, create: bool = False) -> tuple[bool, str]:
    """Check if directory exists, optionally create it."""
    if path.exists():
        if path.is_dir():
            return (True, "✓ exists")
        return (False, "✗ exists but is not a directory")
    if create:
        try:
            path.mkdir(parents=True, exist_ok=True)
            return (True, "✓ created")
        except PermissionError:
            return (False, "✗ cannot create (permission denied)")
    return (False, "✗ missing")


def has_rfd_weights(path: Path) -> bool:
    """
    Check whether an RFdiffusion-compatible checkpoint set is present.
    Supports either classic RFdiffusion ckpts or RFantibody checkpoint naming.
    """
    if not path.exists() or not path.is_dir():
        return False

    classic_ckpts = (
        "Base_ckpt.pt",
        "Complex_base_ckpt.pt",
        "Complex_Fold_base_ckpt.pt",
        "InpaintSeq_ckpt.pt",
    )
    if any((path / name).exists() for name in classic_ckpts):
        return True

    if (path / "RFdiffusion_Ab.pt").exists():
        return True

    return any(path.glob("*.pt"))


def requires_rfantibody_assets(workflow: str) -> bool:
    workflow_key = (workflow or "all").strip().lower()
    antibody_workflows = {"antibody", "rfantibody_backbone"}
    return workflow_key in antibody_workflows


def main(create_dirs: bool = False, workflow: str = "all") -> int:
    """Run validation checks and report results."""
    print("=" * 60)
    print("BioModStack Environment Validation")
    print("=" * 60)

    errors = 0
    warnings = 0

    # 1. Environment Variables
    print("\n[1] Environment Variables")
    print("-" * 40)
    env_vars = [
        ("BMS_HOME", get_code_root()),
        ("BMS_DATA", get_data_root()),
        ("BMS_CONTAINER_DIR", get_container_dir()),
        ("BMS_WEIGHTS", get_weights_root()),
        ("BMS_RFD_MODELS", get_rfd_models_dir()),
        ("BMS_COLABFOLD_DB", get_colabfold_db()),
        ("BMS_MSA_CACHE", get_msa_cache_dir()),
        ("BMS_SABDAB_CACHE", get_sabdab_cache_dir()),
        ("DATABASE_URL", None),
    ]

    for name, resolved in env_vars:
        is_set, value = check_env_var(name)
        status = "SET" if is_set else "fallback"
        resolved_str = f" → {resolved}" if resolved else ""
        print(f"  {name}: [{status}] {value}{resolved_str}")

    # 2. Core Directories
    print("\n[2] Core Directories")
    print("-" * 40)
    core_dirs = [
        ("Code Root", get_code_root()),
        ("Data Root", get_data_root()),
        ("Results Dir", get_results_dir()),
        ("Container Dir", get_container_dir()),
        ("Weights Root", get_weights_root()),
        ("ColabFold DB", get_colabfold_db()),
        ("MSA Cache", get_msa_cache_dir()),
        ("SAbDab Cache", get_sabdab_cache_dir()),
    ]

    for name, path in core_dirs:
        exists, status = check_directory(path, create=create_dirs)
        if not exists:
            warnings += 1
        print(f"  {name}: {path} [{status}]")

    # 3. Weight Directories
    print("\n[3] Required Weights")
    print("-" * 40)
    weights_root = get_weights_root()
    for subdir in REQUIRED_WEIGHTS:
        path = weights_root / subdir
        exists, status = check_directory(path)
        if not exists:
            errors += 1
            status = "✗ MISSING (required)"
        print(f"  {subdir}: {status}")

    # RFdiffusion checkpoints (supports classic or RFantibody layouts)
    rfd_models_dir = get_rfd_models_dir()
    rfd_status = "✓ exists" if has_rfd_weights(rfd_models_dir) else "✗ MISSING (required for RFdiffusion workflows)"
    if rfd_status.startswith("✗"):
        errors += 1
    print(f"  rfd_models: {rfd_status} ({rfd_models_dir})")

    print("\n[4] Optional Weights")
    print("-" * 40)
    for subdir in OPTIONAL_WEIGHTS:
        path = weights_root / subdir
        exists, status = check_directory(path)
        if not exists:
            warnings += 1
            status = "⚠ missing (optional)"
        print(f"  {subdir}: {status}")

    # RFantibody checkpoint (required for antibody workflows)
    if requires_rfantibody_assets(workflow):
        expected_ckpt = weights_root / "rfantibody" / "rfantibody_repo" / "weights" / "RFdiffusion_Ab.pt"
        print("  rfantibody/RFdiffusion_Ab.pt: ", end="")
        if expected_ckpt.exists():
            print(f"✓ exists ({expected_ckpt})")
        else:
            errors += 1
            print(f"✗ MISSING (required for workflow={workflow}; expected at {expected_ckpt})")

    # 5. Container images
    print("\n[5] Containers")
    print("-" * 40)
    container_dir = get_container_dir()

    required_containers = []
    if requires_rfantibody_assets(workflow):
        required_containers.append("rfantibody.sif")

    for image in required_containers:
        image_path = container_dir / image
        if image_path.exists():
            print(f"  {image}: ✓ exists (required for workflow={workflow})")
        else:
            errors += 1
            print(f"  {image}: ✗ MISSING (required for workflow={workflow})")

    print("  -- optional --")
    for image in OPTIONAL_CONTAINERS:
        image_path = container_dir / image
        if image_path.exists():
            print(f"  {image}: ✓ exists")
        else:
            warnings += 1
            print(f"  {image}: ⚠ missing (optional)")

    # 6. Database
    print("\n[6] Database")
    print("-" * 40)
    db_path = get_db_path()
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        print(f"  Path: {db_path}")
        print(f"  Size: {size_mb:.2f} MB")
        print(f"  Status: ✓ exists")
    else:
        print(f"  Path: {db_path}")
        print(f"  Status: ⚠ not created yet (will be auto-created on first run)")
        warnings += 1

    # Summary
    print("\n" + "=" * 60)
    if errors == 0 and warnings == 0:
        print("✓ All checks passed!")
        return 0
    else:
        print(f"Summary: {errors} errors, {warnings} warnings")
        if errors > 0:
            print("✗ Fix required errors before running workflows")
            return 1
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate BioModStack environment and assets")
    parser.add_argument(
        "--create",
        "-c",
        action="store_true",
        help="Create missing core directories when possible",
    )
    parser.add_argument(
        "--workflow",
        default="all",
        choices=["all", "antibody", "rfantibody_backbone"],
        help="Enable workflow-specific required checks",
    )
    args = parser.parse_args()
    sys.exit(main(create_dirs=args.create, workflow=args.workflow))
