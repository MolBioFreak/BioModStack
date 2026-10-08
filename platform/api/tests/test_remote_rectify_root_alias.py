"""R05 boundary probes: native routing is not complete root admission authority.

These deliberately retain the unresolved result-contract/availability blockers.
Actual producer HTTP regressions live in test_remote_rectify_admission.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from antibody_pipeline_contract import ANTIBODY_DENOVO_PIPELINE, ANTIBODY_REFINEMENT_PIPELINE
from component_runtime import canonical_bytes
from model_registry import get_registry
from routers.jobs import normalize_job_request
from schemas import JobCreate
from services import nextflow
from services.result_contracts import resolve_result_contract
from tests.test_remote_rectify_admission import admission


@pytest.mark.parametrize('mode', [ANTIBODY_DENOVO_PIPELINE, ANTIBODY_REFINEMENT_PIPELINE])
def test_full_root_route_has_no_existing_result_contract(mode):
    for model in ('antibody_denovo', 'template_antibody_denovo'):
        assert nextflow.MODEL_MODE_WORKFLOW_ENTRYPOINTS[model, mode] == 'workflows/antibody_denovo.nf'
        assert resolve_result_contract(model_type=model, stage_mode=mode).contract_source == 'unsupported'
    errors = get_registry().validate_job_params('template_antibody_denovo', mode, {},
        native_entrypoint='workflows/antibody_denovo.nf')
    assert errors == ['Unknown model: template_antibody_denovo']
    mismatch = get_registry().validate_job_params('template_antibody_denovo', mode, {},
        native_entrypoint='workflows/maturation_child.nf')
    assert mismatch == ['Native model/mode does not match canonical compiler routing']


@pytest.mark.parametrize('model,mode', [
    ('unknown', 'predict'), ('template_unknown', ANTIBODY_REFINEMENT_PIPELINE),
    ('template_antibody_denovo', 'maturation_child'), ('rfantibody_child', 'antibody_backbone'),
    ('fampnn_child', 'sequence_design'), ('boltzgen', 'antibody'), ('ppiflow', 'maturation'),
])
def test_untrusted_root_does_not_admit_private_or_unknown_identity(model, mode):
    error = ('FA-MPNN child requires trusted parent declaration' if model == 'fampnn_child'
             else 'supported typed model and mode')
    with pytest.raises(ValueError, match=error):
        nextflow.compile_workflow_provision_request(JobCreate(name='boundary', model_id=model, mode=mode, params={}))


@pytest.mark.asyncio
async def test_real_native_root_plan_retains_identity_and_missing_authority(admission, tmp_path):
    # admission supplies only isolated DB/source identity infrastructure. Neither
    # normalization nor either native compiler/metadata resolver is replaced.
    params = {'selected_input_dir': str(tmp_path), 'skip_rfantibody': True,
              'run_structure_validation': True, 'structure_validator': 'boltz2',
              'boltz_use_msa': False, 'run_frustrampnn': False,
              'seq_design_fampnn': False, 'seq_design_antifold': False,
              'seq_design_proteinmpnn': False}
    request = JobCreate(name='boundary', model_id='template_antibody_denovo',
                        mode=ANTIBODY_REFINEMENT_PIPELINE, params=params)
    original = deepcopy(request.params)
    normalized = normalize_job_request(request)
    output = str(tmp_path / 'never-created')
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    def compile_payload(values):
        job = SimpleNamespace(id='root-boundary', model_id=request.model_id, mode=request.mode,
            params=values, provenance={'core_protein_requested_params': original},
            execution_source_revision=None, execution_source_tree=None, output_dir=output)
        return nextflow.compile_job_nextflow_invocation(job, values, output)
    compiled = compile_payload(normalized.params)
    assert compiled.requested_json == canonical_bytes(original)
    assert request.params == original
    plan = compiled.execution_plan
    assert plan is not None and plan.entrypoint == 'workflows/antibody_denovo.nf'
    assert not plan.complete
    assert {'availability', 'result_contract'} <= {row.field for row in plan.metadata.blockers}
    changed = compile_payload({**normalized.params, 'boltz_use_msa': True})
    assert compiled.effective_json != changed.effective_json
    assert compiled.execution_plan.plan_sha256 != changed.execution_plan.plan_sha256
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
    assert not (tmp_path / 'never-created').exists()
