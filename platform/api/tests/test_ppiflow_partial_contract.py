"""Selected protein contract/compiler/dependencies, no inference or live stores."""
from model_registry import ModelRegistry, selected_execution_metadata
from services.nextflow import compile_nextflow_invocation


def test_protein_compiler_defaults_and_exact_assets(tmp_path):
    params = {'pdb_paths': str(tmp_path / 'seed.pdb'), 'binder_chains': 'R', 'target_chains': 'r',
              'ppiflow_task': 'protein_binder_partial', 'maturation_flow_enabled': True}
    registry = ModelRegistry()
    assert registry.validate_job_params('binder_refinement', 'refine', params) == []
    result = compile_nextflow_invocation('binder_refinement', 'refine', params, str(tmp_path / 'out'))
    effective = result.native_parameters
    assert effective['ppiflow_start_t'] == .15
    assert effective['ppiflow_samples_per_target'] == 100
    assert effective['ppiflow_checkpoint'] == 'binder'
    assert effective['maturation_redesign_enabled'] is False
    assert 'ppiflow_retry_limit' not in effective
    metadata = selected_execution_metadata('binder_refinement', 'refine', effective, 'workflows/binder_refinement.nf')
    assert {c.component_key for c in metadata.static_components} == {'PrepareBinderRefinementRegions', 'RunProteinPartialFlow', 'PublishBinderRefinement'}
    paths = {d.relative_path for d in metadata.dependencies}
    assert 'ppiflow/binder.ckpt' in paths
    assert 'ppiflow.sif' in paths
    assert 'fampnn' not in paths and 'ppiflow' not in paths
    assert 'scripts/run_ppiflow_protein_partial.py' in paths
    assert 'platform/api/config/models/binder_refinement.yaml' in paths


def test_registry_keeps_native_discovery_metadata():
    model = ModelRegistry().get_internal_model_definition('binder_refinement')
    by_name = {p.name: p.model_dump() for p in model.params}
    assert by_name['ppiflow_start_t']['protein_native_cli'] == 'start_t'
    assert by_name['ppiflow_start_t']['protein_default'] == .15
    assert by_name['ppiflow_protein_rotation_corrupt']['protein_native_path'] == 'interpolant.rots.corrupt'
    assert by_name['ppiflow_region_mode']['applicability']['value'] == 'protein_binder_partial'
