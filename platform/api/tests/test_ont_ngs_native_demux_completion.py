"""Run the actual DoradoDemux shell with fixture executable, never inference."""
import asyncio
import copy
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pysam
import pytest
from ngs_resource_fixture import ngs_resources

pytestmark = [pytest.mark.native_http, pytest.mark.usefixtures("ngs_resources", "native_http")]

from services import ont_ngs_completion as completion
from services.ont_ngs_native_completion import validate_native_basecall
from test_ont_ngs_native_completion import _fixture, _sha, _validate, isolated_result_root
from ngs_producer_fixtures import emit_receipt, producer_receipt

ROOT = Path(__file__).resolve().parents[3]


def demux_fixture(tmp_path, reference=False, summary=False, empty_bin=False):
    job, root = _fixture(tmp_path)
    job.params['barcode_kit'] = 'SQK-RBK114-96'
    if reference:
        ref = tmp_path / 'selected.fasta'
        ref.write_text('>ref\nACGT\n')
        job.params.update(reference_fasta=str(ref), reference_sequence_sha256=__import__('hashlib').sha256(b'ACGT').hexdigest())
    pre_path = root / 'basecall/dorado_preflight.json'
    pre = json.loads(pre_path.read_text())
    pre['barcoding']['kit'] = job.params['barcode_kit']
    pre_path.write_text(json.dumps(pre))
    # No BC is legitimate unclassified native input (Structure.cpp:170-182).
    job.params['emit_summary'] = summary
    emitted = emit_receipt(root / 'basecall', requested=summary)
    assert emitted.returncode == 0, emitted.stderr
    emitted = emit_demux(root, empty_bin=empty_bin)
    assert emitted.returncode == 0, emitted.stderr
    job.provenance['stage_terminal_states']['dorado_demux'] = {
        'status': 'complete', 'outputs': [str(root / 'demux' / p) for p in
            ['demux_manifest.json', 'per_barcode_units.json', 'demux/units']]}
    return job, root


def emit_demux(root, empty_bin=False, nested=False, label='unclassified', partitions=None):
    task = root / 'demux'
    task.mkdir(exist_ok=True)
    tools = task / 'fixture-binaries'
    tools.mkdir(exist_ok=True)
    # Only the native executable is a fixture. All manifest construction,
    # merging, counts, hashes and validation are the production module shell.
    fake = tools / 'dorado'
    destination = (f'demux/experiment/sample/run/bam_pass/{label}/FC_pass_{label}_protocol_acquire_0.bam'
                   if nested else f'demux/{label}.bam')
    fake.write_text(f'#!{sys.executable}\nimport pathlib,shutil,sys,pysam\n'
                    'assert sys.argv[1:5] == ["demux", "--no-classify", "--output-dir", "demux"]\n'
                    f'destination=pathlib.Path({destination!r}); destination.parent.mkdir(parents=True,exist_ok=True)\n'
                    'shutil.copyfile(sys.argv[5], destination)\n'
                    + ('with pysam.AlignmentFile("demux/barcode01.bam", "wb", header={"HD":{"VN":"1.6"}}): pass\n' if empty_bin else ''))
    if partitions is not None:
        fake.write_text(f'#!{sys.executable}\nimport pathlib,sys,pysam\n'
                        'assert sys.argv[1:5] == ["demux", "--no-classify", "--output-dir", "demux"]\n'
                        'with pysam.AlignmentFile(sys.argv[5], "rb", check_sq=False) as bam:\n'
                        ' header=bam.header; reads=list(bam)\n'
                        f'for name, indices in {partitions!r}:\n'
                        ' path=pathlib.Path("demux") / name; path.parent.mkdir(parents=True,exist_ok=True)\n'
                        ' with pysam.AlignmentFile(str(path), "wb", header=header) as out:\n'
                        '  for index in indices: out.write(reads[index])\n')
    fake.chmod(0o755)
    samtools = tools / 'samtools'
    samtools.write_text(f'#!{sys.executable}\nimport sys,pysam\n'
                       'try: result=getattr(pysam,sys.argv[1])(*sys.argv[2:])\n'
                       'except pysam.SamtoolsError as e: print(e,file=sys.stderr);sys.exit(1)\n'
                       'if result: sys.stdout.write(result)\n')
    samtools.chmod(0o755)
    text = (ROOT / 'modules/ngs/dorado_basecall.nf').read_text().split('process DoradoDemux {', 1)[1]
    script = text.split('    """', 1)[1].rsplit('    """', 1)[0]
    for key, path in [('bam', root / 'basecall/calls.bam'), ('preflight_json', root / 'basecall/dorado_preflight.json')]:
        script = script.replace('${doradoShellQuote(' + key + ')}', shlex.quote(str(path)))
    script = script.replace('\\$', '$')
    script = script.replace('${params.code_root ?: projectDir}', str(ROOT))
    command = task / '.command.sh'
    command.write_text(script)
    return subprocess.run(['bash', str(command.resolve())], cwd=task, capture_output=True, text=True,
                          env={**os.environ, 'PATH': str(tools) + ':' + os.environ['PATH']})


@pytest.mark.parametrize('reference', [False, True])
@pytest.mark.parametrize('summary', [False, True])
@pytest.mark.parametrize('empty_bin', [False, True])
def test_demux_native_completion_accepts_actual_manifest_without_alignment(tmp_path, monkeypatch, reference, summary, empty_bin):
    from services import ngs_alignment_sessions
    def forbidden(*args, **kwargs):
        raise AssertionError('demux completion must not use derived stores or reference alignment')
    monkeypatch.setattr(ngs_alignment_sessions, 'build_alignment_sessions', forbidden)
    job, root = demux_fixture(tmp_path, reference, summary, empty_bin)
    assert completion.ont_completion_lane(job) == 'native_basecall'
    result = _validate(job)
    assert result['demux']['read_count'] == 1
    assert result['demux']['unit_count'] == (2 if empty_bin else 1)
    assert 'alignment' not in result
    assert not (root / 'align').exists()
    assert result['demux']['demux_manifest_sha256'] == _sha(root / 'demux/demux_manifest.json')
    assert all(a['sha256'] == _sha(root / a['path']) for a in result['artifacts'])


@pytest.mark.parametrize('damage', ['schema', 'source', 'preflight', 'total', 'units_mismatch', 'duplicate_unit',
    'missing_unit', 'missing_bam', 'corrupt_bam', 'unit_hash', 'unit_count', 'foreign_read',
    'unsafe_path', 'wrong_label', 'wrong_alias', 'resubmission', 'missing_stage', 'wrong_stage',
    'symlink', 'extra_unit', 'kit', 'unexpected_align'])
def test_demux_native_rejects_invalid_authority(tmp_path, damage):
    job, root = demux_fixture(tmp_path)
    d = root / 'demux'
    path = d / 'demux_manifest.json'
    doc = json.loads(path.read_text())
    unit = doc['units'][0]
    bam = d / unit['bam_path']
    if damage == 'schema': doc['schema'] = 'bad'
    if damage == 'source': doc['source_calls']['sha256'] = '0' * 64
    if damage == 'preflight': doc['preflight_sha256'] = '0' * 64
    if damage == 'total': doc['total_reads'] = True
    if damage == 'duplicate_unit': doc['units'].append(dict(unit))
    if damage == 'missing_unit': (d / unit['unit_manifest_path']).unlink()
    if damage == 'missing_bam': bam.unlink()
    if damage == 'corrupt_bam': bam.write_bytes(b'not bam')
    if damage == 'unit_hash': unit['bam_sha256'] = '0' * 64
    if damage == 'unit_count': unit['read_count'] = 2
    if damage == 'foreign_read':
        with pysam.AlignmentFile(str(bam), 'wb', header={'HD': {'VN': '1.6'}}) as out:
            read = pysam.AlignedSegment(); read.query_name = 'foreign'; read.query_sequence = 'ACGT'; read.flag = 4
            out.write(read)
        unit['bam_sha256'] = _sha(bam)
        receipt = d / unit['unit_manifest_path']
        value = json.loads(receipt.read_text()); value['bam_sha256'] = _sha(bam)
        receipt.write_text(json.dumps(value)); unit['unit_manifest_sha256'] = _sha(receipt)
    if damage == 'unsafe_path': unit['bam_path'] = '../basecall/calls.bam'
    if damage == 'wrong_label': unit['unit_id'] = 'barcode97'
    if damage == 'wrong_alias': unit['sample_alias'] = 'forged'
    if damage == 'resubmission': unit['resubmission_params']['barcode_unit'] = 'barcode02'
    if damage == 'missing_stage': del job.provenance['stage_terminal_states']['dorado_demux']
    if damage == 'wrong_stage': job.provenance['stage_terminal_states']['dorado_demux']['outputs'].pop()
    if damage == 'symlink':
        bam.rename(d / 'outside.bam'); bam.symlink_to(d / 'outside.bam')
    if damage == 'extra_unit': (d / 'demux/units/extra.bam').write_bytes(bam.read_bytes())
    if damage == 'kit': job.params['barcode_kit'] = 'SQK-NBD114-96'
    if damage == 'unexpected_align': job.provenance['stage_terminal_states']['dorado_align'] = {'status': 'complete', 'outputs': []}
    path.write_text(json.dumps(doc))
    units_doc = {'schema': 'biomodstack.dorado_barcode_units.v1', 'units': doc['units']}
    if damage == 'units_mismatch': units_doc['units'] = []
    (d / 'per_barcode_units.json').write_text(json.dumps(units_doc))
    before = copy.deepcopy(job.__dict__)
    with pytest.raises(completion.OntNgsCompletionError):
        validate_native_basecall(job)
    assert job.__dict__ == before


@pytest.mark.parametrize('tag,value,label', [
    (None, None, 'unclassified'), ('BC', 'SQK-RBK114-96_barcode01', 'barcode01'),
    ('BC', 'barcode96', 'barcode96'), ('al', 'barcode02', 'barcode02'), ('SM', 'barcode03', 'barcode03'),
])
def test_actual_nested_producer_and_classified_native_partition(tmp_path, tag, value, label):
    job, root = _fixture(tmp_path)
    job.params['barcode_kit'] = 'SQK-RBK114-96'
    base = root / 'basecall'
    path = base / 'dorado_preflight.json'
    pre = json.loads(path.read_text()); pre['barcoding']['kit'] = job.params['barcode_kit']
    path.write_text(json.dumps(pre))
    with pysam.AlignmentFile(str(base / 'calls.bam'), 'rb', check_sq=False) as bam:
        header = bam.header; reads = list(bam)
    if tag: reads[0].set_tag(tag, value)
    with pysam.AlignmentFile(str(base / 'calls.bam'), 'wb', header=header) as bam:
        for read in reads: bam.write(read)
    emitted = emit_receipt(base, requested=False)
    assert emitted.returncode == 0, emitted.stderr
    emitted = emit_demux(root, nested=True, label=label)
    assert emitted.returncode == 0, emitted.stderr
    job.provenance['stage_terminal_states']['dorado_demux'] = {'status': 'complete', 'outputs': [
        str(root / 'demux' / p) for p in ['demux_manifest.json', 'per_barcode_units.json', 'demux/units']]}
    result = validate_native_basecall(job)
    assert result['demux']['unit_count'] == 1
    assert result['demux']['read_count'] == 1


@pytest.mark.parametrize('damage', ['sequence', 'qualities', 'tag', 'label', 'duplicate_drop'])
def test_demux_rehashed_native_partition_tamper(tmp_path, damage):
    job, root = demux_fixture(tmp_path)
    directory = root / 'demux'
    path = directory / 'demux_manifest.json'
    doc = json.loads(path.read_text())
    unit = doc['units'][0]
    bam_path = directory / unit['bam_path']
    with pysam.AlignmentFile(str(bam_path), 'rb', check_sq=False) as bam:
        header = bam.header; reads = list(bam)
    if damage == 'sequence': reads[0].query_sequence = 'TTTT'
    if damage == 'qualities': reads[0].query_qualities = pysam.qualitystring_to_array('!!!!')
    if damage == 'tag': reads[0].set_tag('BC', 'barcode02')
    if damage == 'label':
        old_bam = bam_path; old_receipt = directory / unit['unit_manifest_path']
        unit.update(unit_id='barcode02', bam_path='demux/units/barcode02.bam', unit_manifest_path='demux/manifests/barcode02.json')
        unit['resubmission_params'].update(bam_path=unit['bam_path'], barcode_unit='barcode02')
        bam_path = directory / unit['bam_path']; old_bam.unlink(); old_receipt.unlink()
    if damage == 'duplicate_drop': reads = reads * 2; unit['read_count'] = 2
    with pysam.AlignmentFile(str(bam_path), 'wb', header=header) as bam:
        for read in reads: bam.write(read)
    unit['bam_sha256'] = _sha(bam_path)
    receipt = {key: unit[key] for key in ('unit_id', 'sample_alias', 'bam_path', 'bam_sha256', 'read_count', 'source_calls_sha256', 'preflight_sha256')}
    receipt['schema'] = 'biomodstack.dorado_barcode_unit.v1'
    receipt_path = directory / unit['unit_manifest_path']
    receipt_path.write_text(json.dumps(receipt)); unit['unit_manifest_sha256'] = _sha(receipt_path)
    path.write_text(json.dumps(doc))
    (directory / 'per_barcode_units.json').write_text(json.dumps({'schema': 'biomodstack.dorado_barcode_units.v1', 'units': doc['units']}))
    with pytest.raises(completion.OntNgsCompletionError, match='partition'):
        validate_native_basecall(job)


def test_sample_sheet_missing_authority_fails_closed(tmp_path):
    job, root = demux_fixture(tmp_path)
    job.params['sample_sheet'] = str(tmp_path / 'samples.csv')
    assert completion.ont_completion_lane(job) == 'native_basecall'
    with pytest.raises(completion.OntNgsCompletionError):
        validate_native_basecall(job)


def alias_fixture(tmp_path, tag='BC', value='Alpha', label='Alpha'):
    import runpy
    job, root = _fixture(tmp_path)
    job.params['barcode_kit'] = 'SQK-RBK114-96'
    source = Path(job.params['pod5_dir'])
    sheet = source / 'samples.csv'
    sheet.write_bytes(b'experiment_id,kit,flow_cell_id,barcode,alias\r\nexp,SQK-RBK114-96,FC,barcode01,Alpha\r\n')
    job.params['sample_sheet'] = str(sheet)
    base = root / 'basecall'
    path = base / 'dorado_preflight.json'
    pre = json.loads(path.read_text())
    pre['inputs'].update(experiment_ids=['exp'], sample_sheet_indexes=[
        {'experiment_id': 'exp', 'flow_cell_id': 'FC', 'position_id': 'X1'}])
    producer = runpy.run_path(str(ROOT / 'scripts/dorado_p4_preflight.py'))
    pre['barcoding'] = {'kit': job.params['barcode_kit'], 'sample_sheet': producer['_validate_sample_sheet'](
        sheet, source, job.params['barcode_kit'], pre['inputs'])}
    path.write_text(json.dumps(pre))
    with pysam.AlignmentFile(str(base / 'calls.bam'), 'rb', check_sq=False) as bam:
        header = bam.header; reads = list(bam)
    if tag:
        reads[0].set_tag(tag, value)
    with pysam.AlignmentFile(str(base / 'calls.bam'), 'wb', header=header) as bam:
        for read in reads: bam.write(read)
    emitted = emit_receipt(base, requested=False)
    assert emitted.returncode == 0, emitted.stderr
    emitted = emit_demux(root, nested=True, label=label)
    assert emitted.returncode == 0, emitted.stderr
    job.provenance['stage_terminal_states']['dorado_demux'] = {'status': 'complete', 'outputs': [
        str(root / 'demux' / p) for p in ['demux_manifest.json', 'per_barcode_units.json', 'demux/units']]}
    return job, root


def test_sample_sheet_parser_uses_exact_snapshot(tmp_path):
    import runpy
    job, root = alias_fixture(tmp_path)
    path = Path(job.params['sample_sheet'])
    raw = path.read_bytes()
    pre = json.loads((root / 'basecall/dorado_preflight.json').read_text())
    path.write_bytes(raw.replace(b'Alpha', b'Other'))
    parser = runpy.run_path(str(ROOT / 'scripts/dorado_p4_preflight.py'))['_validate_sample_sheet']
    assert parser(path, path.parent, job.params['barcode_kit'], pre['inputs'], raw_bytes=raw) == pre['barcoding']['sample_sheet']


@pytest.mark.parametrize('tag,value,label', [('BC', 'Alpha', 'Alpha'), ('al', 'Alpha', 'Alpha'),
    ('SM', 'Alpha', 'Alpha'), ('BC', 'SQK-RBK114-96_barcode01', 'barcode01'),
    (None, None, 'unclassified')])
def test_sample_sheet_alias_authority_from_actual_producer(tmp_path, tag, value, label):
    job, root = alias_fixture(tmp_path, tag, value, label)
    assert completion.ont_completion_lane(job) == 'native_basecall'
    result = _validate(job)
    assert result['demux']['read_count'] == 1
    assert result['sample_sheet_sha256'] == _sha(Path(job.params['sample_sheet']))
    units = json.loads((root / 'demux/per_barcode_units.json').read_text())['units']
    assert units[0]['sample_alias'] == (None if label == 'unclassified' else 'Alpha')


@pytest.mark.parametrize('sheet', [False, True])
@pytest.mark.parametrize('reference', [False, True])
@pytest.mark.parametrize('requested', [False, True, None])
@pytest.mark.parametrize('supported', [False, True])
def test_demux_summary_cross_product(tmp_path, sheet, reference, requested, supported):
    job, root = alias_fixture(tmp_path) if sheet else demux_fixture(tmp_path)
    if reference:
        ref = tmp_path / 'ref.fa'; ref.write_text('>ref\nACGT\n')
        job.params.update(reference_fasta=str(ref), reference_sequence_sha256=__import__('hashlib').sha256(b'ACGT').hexdigest())
    if requested is None:
        job.params.pop('emit_summary')
    else:
        job.params['emit_summary'] = requested
    emitted = emit_receipt(root / 'basecall', requested=requested is not False, supported=supported)
    assert emitted.returncode == 0, emitted.stderr
    result = _validate(job)
    state = 'not_requested' if requested is False else ('validated' if supported else 'unsupported')
    assert result['summary_state'] == state
    assert any(a['path'].endswith('sequencing_summary.tsv') for a in result['artifacts']) == (state == 'validated')
    assert (root / 'basecall/sequencing_summary.tsv').exists() == (state == 'validated')
    assert 'alignment' not in result and not (root / 'align').exists()


@pytest.mark.parametrize('sheet', [False, True])
@pytest.mark.parametrize('empty_bin', [False, True])
def test_multiple_partitions_and_real_per_label_merge(tmp_path, sheet, empty_bin):
    import shutil
    job, root = alias_fixture(tmp_path) if sheet else demux_fixture(tmp_path)
    base = root / 'basecall'
    label = 'Alpha' if sheet else 'barcode01'
    second_label = 'Beta' if sheet else 'barcode02'
    if sheet:
        import runpy
        path = Path(job.params['sample_sheet'])
        path.write_bytes(path.read_bytes() + b'exp,SQK-RBK114-96,FC,barcode02,Beta\r\n')
        pre_path = base / 'dorado_preflight.json'; pre = json.loads(pre_path.read_text())
        parser = runpy.run_path(str(ROOT / 'scripts/dorado_p4_preflight.py'))['_validate_sample_sheet']
        pre['barcoding']['sample_sheet'] = parser(path, path.parent, job.params['barcode_kit'], pre['inputs'])
        pre_path.write_text(json.dumps(pre))
    header = {'HD': {'VN': '1.6'}, 'RG': [{'ID': 'rg', 'SM': 'sample'}], 'PG': [{'ID': 'pg', 'PN': 'dorado'}]}
    with pysam.AlignmentFile(str(base / 'calls.bam'), 'wb', header=header) as bam:
        for i, tag in enumerate([label, label, second_label, None]):
            read = pysam.AlignedSegment(); read.query_name = f'read-{i}'
            read.query_sequence = 'ACGT'; read.query_qualities = pysam.qualitystring_to_array('IIII'); read.flag = 4
            read.set_tag('RG', 'rg'); read.set_tag('PG', 'pg'); read.set_tag('qs', 40.0)
            if tag: read.set_tag('BC', tag)
            bam.write(read)
    emitted = emit_receipt(base, requested=False)
    assert emitted.returncode == 0, emitted.stderr
    shutil.rmtree(root / 'demux')
    partitions = [(f'run/bam_pass/{label}/first.bam', [0]),
                  (f'run/bam_fail/{label}/second.bam', [1]),
                  (f'run/bam_pass/{second_label}/third.bam', [2]),
                  ('run/bam_pass/unclassified/fourth.bam', [3])]
    if empty_bin: partitions.append(('run/bam_pass/barcode03/empty.bam', []))
    emitted = emit_demux(root, partitions=partitions)
    assert emitted.returncode == 0, emitted.stderr
    result = _validate(job)
    assert result['demux']['read_count'] == 4
    assert result['demux']['unit_count'] == (4 if empty_bin else 3)
    units = json.loads((root / 'demux/per_barcode_units.json').read_text())['units']
    assert {u['unit_id']: u['sample_alias'] for u in units} == {
        'barcode01': 'Alpha' if sheet else None, 'barcode02': 'Beta' if sheet else None,
        'unclassified': None, **({'barcode03': None} if empty_bin else {})}
    assert {u['unit_id']: u['read_count'] for u in units} == {
        'barcode01': 2, 'barcode02': 1, 'unclassified': 1, **({'barcode03': 0} if empty_bin else {})}
    with pysam.AlignmentFile(str(root / 'demux/demux/units/barcode01.bam'), 'rb', check_sq=False) as bam:
        reads = list(bam)
        assert len(bam.header.to_dict()['RG']) == 2  # real samtools collision rewriting
        assert {r.query_name for r in reads} == {'read-0', 'read-1'}
        assert all(r.query_sequence == 'ACGT' and r.get_tag('qs') == 40.0 for r in reads)


@pytest.mark.parametrize('damage', ['bytes', 'path', 'symlink', 'extra_ancillary', 'assignment', 'duplicate', 'selector', 'alias_receipt', 'row_count_bool'])
def test_sample_sheet_mismatches_fail_closed(tmp_path, damage):
    job, root = alias_fixture(tmp_path)
    path = Path(job.params['sample_sheet'])
    pre_path = root / 'basecall/dorado_preflight.json'
    pre = json.loads(pre_path.read_text())
    if damage == 'bytes': path.write_bytes(path.read_bytes().replace(b'Alpha', b'Other'))
    if damage == 'path':
        other = path.with_name('other.csv'); other.write_bytes(path.read_bytes()); job.params['sample_sheet'] = str(other)
    if damage == 'symlink':
        other = path.with_name('other.csv'); path.rename(other); path.symlink_to(other)
    if damage == 'extra_ancillary': path.with_name('extra.csv').write_bytes(path.read_bytes())
    if damage in {'assignment', 'duplicate', 'selector', 'row_count_bool'}:
        if damage == 'row_count_bool': pre['barcoding']['sample_sheet']['rows'] = True
        if damage == 'assignment': pre['barcoding']['sample_sheet']['assignments'][0]['alias'] = 'Other'
        if damage == 'duplicate': pre['barcoding']['sample_sheet']['assignments'] *= 2
        if damage == 'selector': pre['inputs']['sample_sheet_indexes'][0]['flow_cell_id'] = 'OTHER'
        pre_path.write_text(json.dumps(pre))
        emitted = emit_receipt(root / 'basecall', requested=False)
        assert emitted.returncode == 0, emitted.stderr
        import shutil
        shutil.rmtree(root / 'demux')
        emitted = emit_demux(root, nested=True, label='barcode01')
        assert emitted.returncode == 0, emitted.stderr
    if damage == 'alias_receipt':
        p = root / 'demux/demux_manifest.json'; doc = json.loads(p.read_text())
        doc['units'][0]['sample_alias'] = 'Other'; p.write_text(json.dumps(doc))
        (root / 'demux/per_barcode_units.json').write_text(json.dumps({'schema': 'biomodstack.dorado_barcode_units.v1', 'units': doc['units']}))
    before = copy.deepcopy(job.__dict__)
    with pytest.raises(completion.OntNgsCompletionError): validate_native_basecall(job)
    assert job.__dict__ == before


@pytest.mark.parametrize('tag,value,accepted', [('BC', 'barcode01', True), ('al', 'Other', False), ('SM', 'Other', False)])
def test_alias_partition_uses_pinned_tag_precedence_not_path(tmp_path, tag, value, accepted):
    job, root = alias_fixture(tmp_path)
    base = root / 'basecall'
    with pysam.AlignmentFile(str(base / 'calls.bam'), 'rb', check_sq=False) as bam:
        header = bam.header; reads = list(bam)
    reads[0].set_tag('BC', None)
    reads[0].set_tag('SM', 'Alpha')
    reads[0].set_tag(tag, value)
    with pysam.AlignmentFile(str(base / 'calls.bam'), 'wb', header=header) as bam:
        for read in reads: bam.write(read)
    emitted = emit_receipt(base, requested=False)
    assert emitted.returncode == 0, emitted.stderr
    import shutil
    shutil.rmtree(root / 'demux')
    emitted = emit_demux(root, nested=True, label='Alpha')
    assert emitted.returncode == 0, emitted.stderr
    if accepted:
        assert _validate(job)['demux']['read_count'] == 1
    else:
        with pytest.raises(completion.OntNgsCompletionError): validate_native_basecall(job)


def test_sample_sheet_change_during_demux_never_publishes(tmp_path, monkeypatch):
    from services import ont_ngs_native_completion as native
    job, root = alias_fixture(tmp_path)
    original = native._validate_demux
    def replace_sheet(*args):
        result = original(*args)
        path = Path(job.params['sample_sheet'])
        path.write_bytes(path.read_bytes().replace(b'Alpha', b'Other'))
        return result
    monkeypatch.setattr(native, '_validate_demux', replace_sheet)
    before = copy.deepcopy(job.__dict__)
    with pytest.raises(completion.OntNgsCompletionError, match='before publication'): _validate(job)
    assert job.__dict__ == before


@pytest.mark.asyncio
@pytest.mark.parametrize('summary', [False, True, None])
async def test_alias_demux_cas_loser_publishes_nothing(tmp_path, monkeypatch, summary):
    import test_ont_ngs_native_completion as native_tests
    job, root = alias_fixture(tmp_path)
    if summary is None: job.params.pop('emit_summary')
    else: job.params['emit_summary'] = summary
    emitted = emit_receipt(root / 'basecall', requested=summary is not False)
    assert emitted.returncode == 0, emitted.stderr
    monkeypatch.setattr(native_tests, '_fixture', lambda unused: (job, root))
    await native_tests.test_native_completion_losing_cas_publishes_no_result(tmp_path, None)


@pytest.mark.asyncio
@pytest.mark.parametrize('reference,summary', [(False, False), (False, True), (True, False), (True, True)])
async def test_demux_cas_loser_publishes_nothing(tmp_path, monkeypatch, reference, summary):
    import test_ont_ngs_native_completion as native_tests
    job, root = demux_fixture(tmp_path, reference=reference, summary=summary)
    monkeypatch.setattr(native_tests, '_fixture', lambda unused: (job, root))
    await native_tests.test_native_completion_losing_cas_publishes_no_result(tmp_path, None)
