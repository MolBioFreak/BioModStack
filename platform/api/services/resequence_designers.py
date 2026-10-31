"""Request templates for the sequence designers the resequence loop can run.

Each template is built by the designer's own model owner, so defaults stay model-owned and caller
edits are validated by the same code that validates a direct launch. Source structures and
positions are filled per design by scripts/run_resequence_designer.py.
"""
from __future__ import annotations

from typing import Any

LOOP_DESIGNERS = ("ligandmpnn", "caliby", "protonpottsmpnn")
LIGAND_MODES = ("ligand_aware", "ntp_aware", "metal_aware", "dna_aware")
CALIBY_MODES = ("ensemble_design",)


def build_template(engine: str, *, mode: str | None, design_chains: str, settings: dict[str, Any]) -> dict[str, Any]:
    settings = dict(settings or {})
    if engine == "ligandmpnn":
        from services.ligandmpnn_design import prepare_design_request
        mode = mode or "ligand_aware"
        if mode not in LIGAND_MODES:
            raise ValueError(f"LigandMPNN mode must be one of {', '.join(LIGAND_MODES)}")
        for owned in ("target_pdb", "ligand_pdb", "fixed_residues", "designed_residues", "fixed_chains", "designed_chains"):
            if owned in settings:
                raise ValueError(f"{owned} is set by the loop's source and position selection")
        return prepare_design_request(mode, {"target_pdb": "input.pdb", "write_fasta": True, "write_structures": True, **settings})
    if engine == "caliby":
        from services.caliby_native import EnsembleDesign
        if (mode or "ensemble_design") not in CALIBY_MODES:
            raise ValueError("Caliby in the loop designs sequences (ensemble_design)")
        for owned in ("ensembles", "task", "schema_version"):
            if owned in settings:
                raise ValueError(f"{owned} is set by the loop's source and position selection")
        probe = EnsembleDesign(ensembles=[{"ensemble_id": "x", "states": [{"state_id": "x", "path": "x"}]}], **settings)
        template = probe.model_dump(mode="json")
        template.pop("ensembles")
        return template
    if engine == "protonpottsmpnn":
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "lib"))
        from protonpottsmpnn_contract import CONTRACT, normalize_options
        first = str(design_chains).split(",")[0].strip()
        if not first:
            raise ValueError("ProtonPottsMPNN needs a binder chain")
        return {"contract": CONTRACT, "options": normalize_options({"binder_chain": first, **settings})}
    raise ValueError(f"{engine} is not a loop designer")
