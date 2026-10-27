import json
import shutil

import pytest
from pydantic import ValidationError

from bms_alignment.models import AlignmentRequest, Row, SequenceSet
from bms_alignment.native import run_mafft
from bms_alignment.bundle import make_bundle, verify_bundle


@pytest.mark.parametrize('identifier', ['', 'row x', 'row\n>injected', 'row\tbad', '>bad', 'bad\x00'])
def test_unsafe_identifiers(identifier):
    with pytest.raises(ValidationError):
        Row(row_id=identifier, header='display', sequence='AC')


def test_duplicate_ids_and_fasta_injection(tmp_path):
    row = Row(row_id='id', header='same', sequence='AC')
    with pytest.raises(ValidationError):
        SequenceSet(molecule='dna', rows=[row, row])
    for field in ('header', 'sequence'):
        with pytest.raises(ValidationError):
            Row(row_id='id', **{field: 'AC\n>bad', ('sequence' if field == 'header' else 'header'): 'ok'})
    request = AlignmentRequest(roster=SequenceSet(molecule='dna', rows=[row]), settings={})
    request.roster.rows.append(row)
    with pytest.raises(ValidationError):
        run_mafft(request, tmp_path, 'unused')
    assert not (tmp_path / 'input.fasta').exists()


def test_manifest_malformed_and_relocated(tmp_path):
    source = tmp_path / 'src'; source.mkdir()
    (source / 'data').write_text('hello')
    dest = tmp_path / 'bundle'
    manifest = make_bundle(source, dest, ['data'])
    relocated = tmp_path / 'relocated'; shutil.copytree(dest, relocated)
    assert verify_bundle(relocated)['valid']
    path = relocated / 'manifest.json'
    for bad in ({'files': []}, {}, {'schema': 'bad', 'files': [], 'dependencies': {'included': [], 'excluded': []}},
                {**manifest, 'files': manifest['files'] * 2},
                {**manifest, 'files': [{**manifest['files'][0], 'size': True}]},
                {**manifest, 'dependencies': {'included': [], 'excluded': []}}):
        path.write_text(json.dumps(bad))
        assert not verify_bundle(relocated)['valid']
    path.write_text('{')
    assert not verify_bundle(relocated)['valid']
    with pytest.raises(ValueError):
        make_bundle(source, dest, ['data', 'data'])


def test_receipt_has_portable_arguments(tmp_path):
    binary = tmp_path / 'engine'
    binary.write_text('#!/bin/sh\nfor arg do last="$arg"; done\ncat "$last"\n')
    binary.chmod(0o755)
    request = AlignmentRequest(roster=SequenceSet(molecule='dna', rows=[Row(row_id='a', header='h', sequence='AC')]), settings={})
    result = run_mafft(request, tmp_path / 'work', str(binary))
    assert result['returncode'] == 0
    assert result['argv'][0] == 'engine' and result['argv'][-1] == 'input.fasta'
    from pathlib import Path
    assert str(tmp_path) not in (Path(result['attempt_dir']) / 'receipt.json').read_text()
