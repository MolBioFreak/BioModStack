"""CPU native external-evaluation composition at the selected installed pin."""
import os
from pathlib import Path
import pytest
from scripts.lib.complexa_contract import Request
from scripts.bms_complexa.run import run


@pytest.mark.parametrize('operation,stages', [('evaluate', ['evaluate']), ('analysis', ['evaluate', 'analyze'])])
def test_native_evaluation_directory_and_explicit_stages(tmp_path, operation, stages):
    source = os.environ.get('COMPLEXA_TEST_SOURCE')
    if not source:
        pytest.skip('Pinned installed Complexa source required')
    evaluation = tmp_path / 'evaluation'
    evaluation.mkdir()
    (evaluation / '0000.pdb').write_text('REMARK retained transport fixture\nEND\n')
    request = Request(task='protein_binder', task_name='selected', operation=operation,
                      evaluation_slots=['exact'], reward=None)
    receipt = run(request, upstream=Path(source), weights=tmp_path / 'not-loaded-weights',
                  output=tmp_path / 'prepared', evaluation_directory=evaluation, prepare_only=True)
    assert receipt['execution'] == 'not_started'
    assert [command[1] for command in receipt['commands']] == stages
    assert receipt['effective_config']['input_mode'] == 'pdb_dir'
    assert receipt['effective_config']['sample_storage_path'] == str(evaluation)
    assert receipt['effective_config']['dataset']['task_name'] == 'selected'
    assert receipt['request']['evaluation_slots'] == ['exact']


def test_compressed_ligand_source_retains_native_parser_format(tmp_path):
    if not os.environ.get('COMPLEXA_TEST_SOURCE'):
        pytest.skip('Pinned installed Complexa source required')
    import numpy as np
    from biotite.structure import AtomArray
    from atomworks.io.utils.io_utils import to_cif_file, load_any
    from scripts.lib.complexa_contract import Source
    from scripts.bms_complexa.inputs import prepare_source
    atoms = AtomArray(1)
    atoms.coord = np.array([[1.0, 2.0, 3.0]])
    atoms.chain_id = np.array(['L'])
    atoms.res_id = np.array([1])
    atoms.res_name = np.array(['LIG'])
    atoms.atom_name = np.array(['C1'])
    atoms.element = np.array(['C'])
    atoms.hetero = np.array([True])
    source = tmp_path / 'ligand.cif.gz'
    to_cif_file(atoms, source)
    prepared = prepare_source(Source(document_id='exact', ligand='LIG'), source,
                              tmp_path / 'prepared-source', ame=False)
    native = Path(prepared['native_path'])
    assert native.name == 'original.cif.gz'
    assert native.read_bytes() == source.read_bytes()
    readback = load_any(native)[0]
    assert readback.atom_name.tolist() == ['C1']
    np.testing.assert_allclose(readback.coord, atoms.coord)
