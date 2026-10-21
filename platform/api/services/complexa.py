"""Complexa discovery/binding leaf for ordinary Job and portable input owners."""
from __future__ import annotations
import json
from pathlib import Path
from scripts.lib.complexa_contract import MODEL_ID, PROFILES, Request, normalize_request, parameter_schema

SUPPORTED_MODES = frozenset({"generate", "evaluate"})


def normalize_job_request(mode: str, params: dict) -> dict:
    request = Request.model_validate(params)
    expected = "evaluate" if request.operation in ("evaluate", "analysis") else "generate"
    if mode != expected:
        raise ValueError(f"Complexa operation {request.operation} belongs to mode {expected}")
    return normalize_request(params)


def discovery() -> dict:
    profiles = json.loads((Path(__file__).resolve().parents[3] / "scripts/bms_complexa/profiles.json").read_text())
    return {"model_id": MODEL_ID, "modes": sorted(SUPPORTED_MODES), "request_key": "complexa_request",
            "native_profile_defaults": profiles,
            "schema": parameter_schema(), "profiles": {
                task: {"native_config": profile, "checkpoint_pair": pair,
                       "native_generation": branch + "_generate", "native_evaluation": branch + "_evaluate"}
                for task, (profile, pair, branch) in PROFILES.items()},
            "defaults": {"protein_binder": {"search": "best-of-n", "reward": "af2folding"},
                         "ligand_binder": {"search": "best-of-n", "reward": "rf3folding"},
                         "AME": {"search": "single-pass", "reward": None},
                         "monomer_motif_indexed": {"search": "single-pass", "reward": None},
                         "monomer_motif_unindexed": {"search": "single-pass", "reward": None}},
            "limitations": ["No external structure remodeling/partial diffusion API in this released adapter.",
                            "gradient_ascent is commented but not implemented upstream.",
                            "TMOL does not support ligand/AME complexes upstream.",
                            "Binder evaluation implements colabdesign and RF3 only; docstring Boltz2/Protenix options are not implemented.",
                            "Motif AME-pair architecture modes require installed native qualification."]}


def write_request(mode: str, params: dict, destination: Path) -> Path:
    """Pure request serialization; caller binds document_id via existing source custody."""
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "request.json"
    path.write_text(json.dumps(normalize_job_request(mode, params), indent=2))
    return path
