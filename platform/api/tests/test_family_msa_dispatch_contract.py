import os
import subprocess

import pytest
from bms_alignment import AlignmentRequest, parse
from services.family_msa.execution import compile_command
from services.family_msa.requests import MODE, prepare_request, request_schema


@pytest.mark.parametrize(('molecule', 'model'), [
    ('protein', 'family_msa_protein'), ('dna', 'family_msa_nucleic'),
    ('rna', 'family_msa_nucleic'),
])
def test_contract_projection(molecule, model):
    request = {'roster': {'molecule': molecule, 'rows': []}, 'settings': {}}
    selected, effective = prepare_request(request)
    assert (selected, MODE) == (model, 'family_msa')
    assert effective == AlignmentRequest.model_validate(request).model_dump(mode='json')
    assert request_schema() == AlignmentRequest.model_json_schema()
    cmd = compile_command(effective, '/path with space/mafft', '/input with space.fa')
    assert cmd[0] == '/path with space/mafft' and cmd[-1] == '/input with space.fa'
    assert ('--amino' if molecule == 'protein' else '--nuc') in cmd
    assert cmd[cmd.index('--thread') + 1] == '1'


@pytest.mark.parametrize(('molecule', 'sequence'), [
    ('protein', 'ACDEFGHIKLMN'), ('dna', 'ACGTACGTACGT'), ('rna', 'ACGUACGUACGU'),
])
def test_native_compiled_command(tmp_path, molecule, sequence):
    binary = os.environ.get('BMS_TEST_MAFFT')
    if not binary:
        pytest.skip('BMS_TEST_MAFFT not configured')
    source = tmp_path / 'input with space.fa'
    source.write_text(f'>row_1\n{sequence}\n>row_2\n{sequence}\n')
    payload = {'roster': {'molecule': molecule, 'rows': []},
               'settings': {'threads': 2}}
    _, effective = prepare_request(payload)
    proc = subprocess.run(compile_command(effective, binary, str(source)),
                          cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert len(parse(proc.stdout, 'fasta', molecule).rows) == 2
