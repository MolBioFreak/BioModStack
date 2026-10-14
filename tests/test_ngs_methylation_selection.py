"""Actual selected metadata predicates for the native methylation graph."""
import pytest
from test_ngs_native_components import native, metadata


@pytest.mark.parametrize('input_key', ['pod5_dir', 'bam_path'])
@pytest.mark.parametrize('reference,modkit', [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize('realign', [False, True])
def test_methylation_selected_native_predicates(native, input_key, reference, modkit, realign):
    params = {input_key: '/reads', 'run_modkit': modkit, 'bam_force_realign': realign}
    if reference:
        params['reference_fasta'] = '/reference.fasta'
    graph, deps, _, blockers = metadata(native, 'ont_methylation_analysis', params)
    assert not blockers
    assert ('DoradoAlign' in graph) == (input_key == 'pod5_dir' and modkit)
    assert ('PrepareBamForAnalysis' in graph) == (input_key == 'bam_path')
    assert ('ValidateMappedBam' in graph) == (input_key == 'bam_path' and reference)
    assert ('PrepareReferenceForIGV' in graph) == reference
    for name in ('ValidateModifiedBaseBam', 'ModkitPileup', 'ModkitSummary'):
        assert (name in graph) == modkit
