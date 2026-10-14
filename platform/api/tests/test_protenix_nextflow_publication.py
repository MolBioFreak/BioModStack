"""Execute production Nextflow publication declarations, without scientific inference."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_protenix_publication_writer import native_tree


@pytest.mark.parametrize('process_name,geometry,validator', [
    ('ProtenixPredict', False, False),
    ('ProtenixFromComplex', False, False),
    ('ProtenixFromComplex', False, True),
    ('ProtenixFromComplex', True, False),
])
def test_nextflow_copies_exact_staged_seed_sample_paths(tmp_path, process_name, geometry, validator):
    nextflow = os.environ.get('BMS_TEST_NEXTFLOW') or shutil.which('nextflow')
    if nextflow is None:
        pytest.skip('Nextflow executable is required for the publication execution regression')
    repository = Path(__file__).resolve().parents[3]
    module = (repository/'modules/protenix.nf').read_text()
    helper = 'def protenixComplexFinalizesGeometry' + module.split('def protenixComplexFinalizesGeometry', 1)[1].split('\nprocess ', 1)[0]
    # Retain the exact production inputs, outputs, publishDir and saveAs rules.
    # Only science is replaced with a closed native-output fixture plus real writer.
    header = 'process ' + process_name + ' {' + module.split('process ' + process_name + ' {', 1)[1].split('    script:', 1)[0]
    fixture = tmp_path/'native'
    native_tree(fixture, samples=2, suffix='pdb' if geometry else 'cif')
    if geometry:
        for path in list(fixture.rglob('*.pdb')):
            path.with_suffix('.cif').write_bytes(b'raw original CIF fixture\n')
    msa = tmp_path/'prepared_msa'
    msa.mkdir()
    (msa/'msa_report.json').write_text('{}')
    payload = tmp_path/'complex.json'
    payload.write_text('[]')
    result_root = 'validation/protenix_v2/test-candidate' if validator else 'pdb_files/predictions'
    writer = repository/'scripts/write_structure_producer_manifest.py'
    script = f'''    script:
    """
    mkdir -p predictions msa_prepared
    cp -a '{fixture}/.' predictions/
    cp '{msa}/msa_report.json' msa_prepared/msa_report.json
    python3 '{writer}' --predictions-root predictions --producer-method protenix --producer-sample task-A --format {'pdb' if geometry else 'mmcif'} --publication-dir producer_publication --published-structure-root '{result_root}' --output producer_candidates.json
    """
}}
'''
    if process_name == 'ProtenixPredict':
        invocation = f"{process_name}(Channel.of(tuple([producer_artifact_id: 'task-A'], 'G', 'task-A', file('{msa}'))))"
    else:
        invocation = f"{process_name}(Channel.of(tuple([candidate_id: 'test-candidate'], file('{payload}'), file('{msa}'))))"
    workflow = tmp_path/'publication.nf'
    workflow.write_text(helper + '\n' + header + script + '\nworkflow {\n' + invocation + '\n}\n')
    config = tmp_path/'nextflow.config'
    config.write_text(f'''params.out_dir = '{tmp_path / 'returned'}'
params.plr_validator_suite_active = {'true' if validator else 'false'}
params.protenix_target_geometry_mode = '{'conditioned' if geometry else 'flexible'}'
params.fixed_target_source_path = '{payload}'
params.fixed_target_source_chains = 'A'
params.target_chains = 'A'
process.executor = 'local'
process.cpus = 1
process.memory = '512 MB'
''')
    environment = dict(os.environ, NXF_OFFLINE='true', NXF_ANSI_LOG='false',
                       NXF_HOME=str(tmp_path/'nxf-home'), HOME=str(tmp_path/'home'))
    completed = subprocess.run([nextflow, 'run', str(workflow), '-c', str(config),
        '-work-dir', str(tmp_path/'work')], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    returned = tmp_path/'returned'
    receipts = list((returned/'run/protenix/producer').rglob('publication.json'))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert len(receipt['bindings']) == 6
    suffix = 'pdb' if geometry else 'cif'
    files = list((returned/result_root).rglob('*.' + suffix))
    assert len(files) == 6
    assert len(list((returned/result_root).rglob('*summary_confidence*.json'))) == 6
    assert len(list((returned/result_root).rglob('*full_data*.json'))) == 6
    if validator:
        assert json.loads((returned/result_root/'msa_report.json').read_text()) == {}
    for binding in receipt['bindings']:
        coordinate = returned/binding['published_relative_path']
        original = fixture/binding['producer_output_key']
        assert coordinate.read_bytes() == original.read_bytes()
        for record in binding.get('confidence', {}).values():
            companion = returned/record['published_relative_path']
            assert companion.parent == coordinate.parent
            assert companion.read_bytes() == original.with_name(companion.name).read_bytes()
    if geometry:
        assert len(list((returned/'run/protenix_complex/raw').rglob('*.cif'))) == 6
