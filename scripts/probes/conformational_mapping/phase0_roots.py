"""Installation roots for the phase-0 probes, resolved the way the product resolves them."""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _paths() -> dict[str, object]:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from biomodstack_runtime_profile import resolve_runtime_paths
    return resolve_runtime_paths(project_root=REPO_ROOT)


def container_dir() -> Path:
    return Path(str(_paths()["container_dir"]))


def weights_root() -> Path:
    return Path(str(_paths()["weights_root"]))


def results_dir() -> Path:
    return Path(str(_paths()["results_dir"]))


def build_root() -> Path:
    return Path(str(_paths()["data_root"])) / "build"


def extra_evidence_roots() -> list[Path]:
    """Optional additional approved evidence location (BMS_CM_PHASE0_EVIDENCE_ROOT)."""
    configured = os.environ.get("BMS_CM_PHASE0_EVIDENCE_ROOT", "").strip()
    return [Path(configured)] if configured else []
