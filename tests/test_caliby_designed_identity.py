"""Inert Caliby sample records through the real CLI and CIF output reader."""
import hashlib
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def pdb(sequence_name, chain):
    return f'ATOM      1  CA  {sequence_name} {chain}  17       0.000   0.000   0.000  1.00 50.00           C\n'


@pytest.mark.parametrize('count', [None, 2])
def test_caliby_cli_native_output_identity_not_input_sequence(tmp_path, monkeypatch, count):
    gemmi = pytest.importorskip('gemmi', reason='Caliby image dependency; install pinned image version for this fixture')
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    runner = importlib.import_module('run_caliby_sequence_design')
    inputs = tmp_path/'inputs'; inputs.mkdir()
    source = inputs/'explicit_candidate.pdb'
    source.write_text(pdb('ALA','Z') + pdb('SER','B') + 'END\n')
    output = tmp_path/'output'
    seen = {}

    def sample(paths, **kwargs):
        seen.update(kwargs)
        folder = Path(kwargs['out_dir']); folder.mkdir(parents=True)
        records = dict(example_id=[], out_pdb=[], seq=[], input_seq=[], U=[])
        for index in range(kwargs['num_seqs_per_pdb']):
            native = folder/f'native-result-{index}.cif'
            structure = gemmi.read_pdb_string(pdb('GLY' if index == 0 else 'VAL','Z') + pdb('SER','B') + 'END\n')
            structure.make_mmcif_document().write_file(str(native))
            records['example_id'].append('explicit_candidate')
            records['out_pdb'].append(str(native))
            records['seq'].append('GS' if index == 0 else 'VS')
            records['input_seq'].append('AS')
            records['U'].append(float(index))
        return records

    monkeypatch.setattr(runner, 'preflight_caliby_runtime', lambda **kw: None)
    monkeypatch.setattr(runner, 'maybe_clean_inputs', lambda **kw: kw['pdb_paths'])
    monkeypatch.setattr(runner, 'load_caliby_model', lambda name: SimpleNamespace(sample=sample))
    args=['run_caliby_sequence_design.py', '--input-dir', str(inputs), '--output-dir', str(output),
          '--binder-chains', 'Z', '--target-chains', 'B', '--temperature', '0.4', '--omit-aas', '',
          '--sampling-overrides-json', '{"verbose":false,"potts_sweeps":0}']
    if count is not None:
        args += ['--num-seqs-per-pdb',str(count)]
    monkeypatch.setattr(sys, 'argv', args)
    runner.main()
    expected_count = 4 if count is None else count
    assert seen['num_seqs_per_pdb'] == expected_count
    assert seen['temperature'] == 0.4
    assert seen['sampling_overrides'] == {'verbose': False, 'potts_sweeps': 0}
    manifest=json.loads((output/'caliby_manifest.json').read_text())
    assert len(manifest)==expected_count
    assert len({m['native_output_structure']['path'] for m in manifest})==expected_count
    for index,item in enumerate(manifest):
        record=json.loads(Path(item['metadata_path']).read_text())
        expected='G' if index==0 else 'V'
        assert record['source_structure_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
        assert record['source_residue_mapping'] == [
            dict(source=dict(chain_id=c,auth_seq_id=17,insertion_code=''),
                 output=dict(chain_id=c,auth_seq_id=17,insertion_code='')) for c in ['B','Z']]
        assert record['input_sequence']=='AS'
        assert record['sequence']==expected+'S'
        assert record['designed_chain_sequences']=={'Z':expected}
        assert record['chain_sequences']=={'Z':expected,'B':'S'}
        assert record['native_output_structure']['example_id']=='explicit_candidate'
        native=Path(record['native_output_structure']['path'])
        assert native.suffix=='.cif'
        assert record['native_output_structure']['sha256']==hashlib.sha256(native.read_bytes()).hexdigest()
        assert native == output / record['native_output_structure']['relative_path']
        assert native.parent.name == 'native_outputs'
        assert native.read_bytes() == Path(record['native_output_structure']['source_path']).read_bytes()
        assert record['effective_settings']['num_seqs_per_pdb']==expected_count
