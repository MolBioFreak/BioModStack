from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from paths import get_code_root, get_container_path
from services.boltzgen_scaffolding import prepare_boltzgen_params_for_launch


router = APIRouter(prefix="/api/boltzgen", tags=["boltzgen"])


class BoltzGenPreviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    params: Dict[str, Any] = Field(default_factory=dict)
    run_check: bool = Field(True, alias="validate")


class BoltzGenPreviewResponse(BaseModel):
    yaml_text: str
    scaffold_specs: List[Dict[str, Any]] = Field(default_factory=list)
    resolved_params: Dict[str, Any] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)
    check_ok: bool = False
    check_stdout: Optional[str] = None
    check_stderr: Optional[str] = None


def _append_arg(argv: list[str], flag: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    argv.extend([flag, str(value)])


@router.post("/preview", response_model=BoltzGenPreviewResponse)
async def preview_design_spec(request: BoltzGenPreviewRequest) -> BoltzGenPreviewResponse:
    resolved_params, notes = await prepare_boltzgen_params_for_launch(request.params)
    code_root = get_code_root()
    prep_script = code_root / "scripts" / "prep_boltzgen.py"
    boltzgen_sif = get_container_path("boltzgen.sif")

    with tempfile.TemporaryDirectory(prefix="boltzgen_preview_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        output_yaml = tmp_path / "boltzgen_input.yaml"
        prep_cmd = ["python3", str(prep_script)]

        _append_arg(prep_cmd, "--ligand_smiles", resolved_params.get("boltzgen_ligand_smiles"))
        _append_arg(prep_cmd, "--ntp_type", resolved_params.get("boltzgen_ntp_type"))
        _append_arg(prep_cmd, "--scaffold_length", resolved_params.get("boltzgen_scaffold_length") or "100-135")
        _append_arg(prep_cmd, "--num_designs", resolved_params.get("boltzgen_num_designs") or 1)
        _append_arg(prep_cmd, "--binding_site_residues", resolved_params.get("boltzgen_binding_site_residues"))
        if bool(resolved_params.get("boltzgen_catalytic_site")):
            prep_cmd.append("--catalytic_site")
        _append_arg(prep_cmd, "--input_pdb", resolved_params.get("boltzgen_input_pdb"))
        _append_arg(prep_cmd, "--ligand_pdb", resolved_params.get("boltzgen_ligand_pdb"))
        _append_arg(prep_cmd, "--protein_sequence", resolved_params.get("boltzgen_protein_sequence"))
        _append_arg(prep_cmd, "--dna_template_seq", resolved_params.get("boltzgen_dna_template_seq"))
        _append_arg(prep_cmd, "--dna_primer_seq", resolved_params.get("boltzgen_dna_primer_seq"))
        _append_arg(prep_cmd, "--dna_structure", resolved_params.get("boltzgen_dna_structure"))
        _append_arg(prep_cmd, "--secondary_structure", resolved_params.get("boltzgen_secondary_structure"))
        _append_arg(prep_cmd, "--protocol", resolved_params.get("boltzgen_protocol") or "nanobody-anything")
        _append_arg(prep_cmd, "--covalent_bonds", resolved_params.get("boltzgen_covalent_bonds"))
        _append_arg(prep_cmd, "--nanobody_framework", resolved_params.get("boltzgen_nanobody_framework"))
        _append_arg(prep_cmd, "--nanobody_scaffold_specs", resolved_params.get("boltzgen_nanobody_scaffold_specs"))
        _append_arg(prep_cmd, "--cdr_h1_length", resolved_params.get("boltzgen_cdr_h1_length") or "5-8")
        _append_arg(prep_cmd, "--cdr_h2_length", resolved_params.get("boltzgen_cdr_h2_length") or "6-10")
        _append_arg(prep_cmd, "--cdr_h3_length", resolved_params.get("boltzgen_cdr_h3_length") or "12-18")
        _append_arg(prep_cmd, "--target_pdb", resolved_params.get("boltzgen_target_pdb_path"))
        _append_arg(prep_cmd, "--output_yaml", output_yaml)

        prep_result = subprocess.run(
            prep_cmd,
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        if prep_result.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "BoltzGen prep failed",
                    "stdout": prep_result.stdout[-4000:],
                    "stderr": prep_result.stderr[-4000:],
                },
            )

        yaml_text = output_yaml.read_text(encoding="utf-8")
        scaffold_specs: List[Dict[str, Any]] = []
        try:
            raw_scaffolds = resolved_params.get("boltzgen_nanobody_scaffold_specs")
            if raw_scaffolds:
                scaffold_specs = json.loads(raw_scaffolds) if isinstance(raw_scaffolds, str) else list(raw_scaffolds)
        except Exception:
            scaffold_specs = []

        if not request.run_check:
            return BoltzGenPreviewResponse(
                yaml_text=yaml_text,
                scaffold_specs=scaffold_specs,
                resolved_params=resolved_params,
                notes=notes,
                check_ok=False,
            )

        if not boltzgen_sif.exists():
            return BoltzGenPreviewResponse(
                yaml_text=yaml_text,
                scaffold_specs=scaffold_specs,
                resolved_params=resolved_params,
                notes=[*notes, f"BoltzGen container missing at {boltzgen_sif}; skipped `boltzgen check`"],
                check_ok=False,
            )

        check_out_dir = tmp_path / "check"
        check_cmd = [
            "apptainer",
            "exec",
            str(boltzgen_sif),
            "boltzgen",
            "check",
            str(output_yaml),
            "--output",
            str(check_out_dir),
        ]
        check_result = subprocess.run(
            check_cmd,
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        return BoltzGenPreviewResponse(
            yaml_text=yaml_text,
            scaffold_specs=scaffold_specs,
            resolved_params=resolved_params,
            notes=notes,
            check_ok=check_result.returncode == 0,
            check_stdout=(check_result.stdout or "").strip() or None,
            check_stderr=(check_result.stderr or "").strip() or None,
        )
