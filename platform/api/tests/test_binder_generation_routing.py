"""Initial-generation routing boundaries, not model-execution acceptance."""
from pathlib import Path
import sys

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.nextflow import resolve_nextflow_entrypoint


@pytest.mark.parametrize("model,mode,expected", [
    ("boltzgen", "protein_binder", "workflows/boltzgen_generation.nf"),
    ("boltzgen", "peptide_binder", "workflows/boltzgen_generation.nf"),
    ("boltzgen", "nanobody_binder", "workflows/boltzgen_generation.nf"),
    ("ppiflow", "protein_binder", "workflows/ppiflow_generation.nf"),
    ("ppiflow", "antibody_binder", "workflows/ppiflow_generation.nf"),
    ("ppiflow", "nanobody_binder", "workflows/ppiflow_generation.nf"),
])
@pytest.mark.parametrize("profile", ["boltz", "protein_binder", "workstation_ryzen7960x"])
def test_initial_generator_identity_selects_its_own_native_route(model, mode, expected, profile):
    assert resolve_nextflow_entrypoint(
        effective_profile=profile, model_id=model, mode=mode, params={}
    ) == expected


@pytest.mark.parametrize("model,mode", [
    ("boltzgen", ""), ("boltzgen", "design"), ("boltzgen", "protein_bindr"),
    ("ppiflow", ""), ("ppiflow", "default"), ("ppiflow", "generator_backbone_refine"),
])
def test_unknown_initial_mode_does_not_fall_back_to_another_workflow(model, mode):
    with pytest.raises(ValueError, match="Unsupported generation mode"):
        resolve_nextflow_entrypoint(effective_profile="boltz", model_id=model, mode=mode)


@pytest.mark.parametrize("model,mode,expected", [
    ("antibody_denovo", "generator_backbone_refine", "workflows/ppiflow_generator_design.nf"),
    ("antibody_denovo", "nanobody_binder", "workflows/protein_design.nf"),
    ("binder_refinement", "refine", "workflows/binder_refinement.nf"),
    ("template_antibody_denovo", "maturation_child", "workflows/maturation_child.nf"),
])
def test_new_generation_does_not_reinterpret_existing_refinement_routes(model, mode, expected):
    assert resolve_nextflow_entrypoint(effective_profile="boltz", model_id=model, mode=mode) == expected


def test_new_generator_routes_do_not_restore_retired_bindcraft():
    with pytest.raises(ValueError, match="retired workflow"):
        resolve_nextflow_entrypoint(effective_profile="boltz", model_id="bindcraft", mode="campaign")
