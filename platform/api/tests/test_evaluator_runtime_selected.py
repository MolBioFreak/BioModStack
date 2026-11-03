"""Controller selection matches the actual native CA-only callable default."""
from model_registry import native_checkpoint_dependencies


def test_complexa_uses_native_ca_not_la_vanilla():
    dependencies, unresolved = native_checkpoint_dependencies('RunComplexa', {'complexa_request': {
        'task': 'protein_binder', 'task_name': 'fixture', 'operation': 'evaluate',
        'evaluation': {'sequence_types': ['mpnn'], 'inverse_folding_model': 'protein_mpnn',
                       'compute_binder_metrics': False,
                       'compute_esm_metrics': False, 'compute_designability': False,
                       'compute_codesignability': False}}})
    assert not unresolved
    paths = {item.relative_path for item in dependencies}
    assert paths == {'complexa/complexa.ckpt', 'complexa/complexa_ae.ckpt',
                     'complexa/ProteinMPNN/ca_model_weights/v_48_020.pt'}
