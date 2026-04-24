"""Provider package with lazy exports.

Keep provider imports side-effect-light so callers that only need one provider do
not eagerly import the others during the staged package split.
"""

from __future__ import annotations

from typing import Any

__all__ = ["run_colabfold_api_msa_workflow", "run_colabfold_msa_workflow"]


def __getattr__(name: str) -> Any:
    if name == "run_colabfold_api_msa_workflow":
        from .colabfold_api import run_colabfold_api_msa_workflow

        return run_colabfold_api_msa_workflow
    if name == "run_colabfold_msa_workflow":
        from .local_mmseqs import run_colabfold_msa_workflow

        return run_colabfold_msa_workflow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
