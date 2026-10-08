"""Summary branch: production shell receipts and real BAM semantic controls."""
import asyncio
import copy
import hashlib
import json

import pysam
import pytest
from ngs_resource_fixture import ngs_resources

pytestmark = [pytest.mark.native_http, pytest.mark.usefixtures("ngs_resources", "native_http")]

from services import ont_ngs_completion as completion
from test_ont_ngs_native_completion import _fixture, _sha, _validate, _nextflow_native_entry, isolated_result_root
from ngs_producer_fixtures import emit_receipt, summary_text


def summary_fixture(tmp_path, molecule='dna', requested=True, supported=True):
    job, root = _fixture(tmp_path, molecule)
    if requested == 'default':
        del job.params['emit_summary']
    else:
        job.params['emit_summary'] = requested
    emitted = emit_receipt(root / 'basecall', requested=requested is not False, supported=supported)
    assert emitted.returncode == 0, emitted.stderr
    return job, root


@pytest.mark.parametrize('molecule', ['dna', 'rna'])
@pytest.mark.parametrize('requested', [True, 'default', False])
@pytest.mark.parametrize('supported', [True, False])
def test_summary_completion_uses_actual_emitter_authority(tmp_path, monkeypatch, molecule, requested, supported):
    from services import ngs_alignment_sessions
    def forbidden(*args, **kwargs):
        raise AssertionError('native completion must not open derived stores')
    monkeypatch.setattr(ngs_alignment_sessions, 'build_alignment_sessions', forbidden)
    job, root = summary_fixture(tmp_path, molecule, requested, supported)
    assert asyncio.run(_nextflow_native_entry(job)) is True
    result = job.provenance['result_integrity']
    expected = 'not_requested' if requested is False else ('validated' if supported else 'unsupported')
    assert result['summary_state'] == expected
    artifacts = {a['path']: a for a in result['artifacts']}
    summary = root / 'basecall/sequencing_summary.tsv'
    if expected == 'validated':
        assert artifacts['basecall/sequencing_summary.tsv']['sha256'] == _sha(summary)
        assert result['summary_read_count'] == 1
    else:
        assert 'basecall/sequencing_summary.tsv' not in artifacts
    assert job.status == 'completed'
    assert result['effective_params_sha256'] == hashlib.sha256(__import__('rfc8785').dumps(job.params)).hexdigest()


@pytest.mark.parametrize('damage', [
    'missing', 'symlink', 'empty', 'header_only', 'header', 'duplicate_column',
    'row_width', 'read_id', 'parent_id', 'length', 'qscore', 'nan', 'numeric',
    'filter', 'duplicate_read', 'hash', 'size', 'path', 'request', 'execution',
    'capability', 'capability_type', 'capability_hash', 'missing_evidence',
    'zero_bam', 'bad_request_type',
])
def test_summary_completion_rejects_inconsistent_products(tmp_path, damage):
    job, root = summary_fixture(tmp_path)
    base = root / 'basecall'
    summary = base / 'sequencing_summary.tsv'
    runtime_path = base / 'dorado_runtime_provenance.json'
    runtime = json.loads(runtime_path.read_text())
    text = summary.read_text()
    header, row = text.splitlines()
    fields = row.split('\t')
    if damage == 'missing': summary.unlink()
    if damage == 'symlink':
        summary.rename(base / 'elsewhere.tsv')
        summary.symlink_to(base / 'elsewhere.tsv')
    if damage == 'empty': summary.write_text('')
    if damage == 'header_only': summary.write_text(header + '\n')
    if damage == 'header': summary.write_text('unrelated\n' + row + '\n')
    if damage == 'duplicate_column': summary.write_text(header.replace('parent_read_id', 'read_id') + '\n' + row + '\n')
    if damage == 'row_width': summary.write_text(header + '\n' + row + '\textra\n')
    changes = {'read_id': (3, 'other'), 'parent_id': (2, 'other'), 'length': (14, '5'),
               'qscore': (15, '1.000000'), 'nan': (15, 'nan'), 'numeric': (9, 'oops'), 'filter': (10, 'FALSE')}
    if damage in changes:
        index, value = changes[damage]
        fields[index] = value
        summary.write_text(header + '\n' + '\t'.join(fields) + '\n')
    if damage == 'duplicate_read': summary.write_text(text + row + '\n')
    if summary.exists():
        runtime['summary']['output']['sha256'] = _sha(summary)
        runtime['summary']['output']['size_bytes'] = summary.stat().st_size
    if damage == 'hash': runtime['summary']['output']['sha256'] = '0' * 64
    if damage == 'size': runtime['summary']['output']['size_bytes'] += 1
    if damage == 'path': runtime['summary']['output']['path'] = 'elsewhere.tsv'
    if damage == 'request': runtime['summary']['requested'] = False
    if damage == 'execution': runtime['summary']['executed'] = False
    if damage == 'capability': runtime['summary']['capability']['supported'] = False
    if damage == 'capability_type': runtime['summary']['capability']['supported'] = 'true'
    if damage == 'capability_hash': runtime['summary']['capability']['help_sha256'] = 'bad'
    if damage == 'missing_evidence': del runtime['summary']
    if damage == 'bad_request_type': job.params['emit_summary'] = 'true'
    if damage == 'zero_bam':
        with pysam.AlignmentFile(str(base / 'calls.bam'), 'wb', header={'HD': {'VN': '1.6'}}):
            pass
        runtime['calls_bam'].update(sha256=_sha(base / 'calls.bam'), read_count=0,
                                   read_inventory_sha256=hashlib.sha256(b'').hexdigest())
        summary.write_text(summary_text(empty=True))
        runtime['summary']['output'].update(sha256=_sha(summary), size_bytes=summary.stat().st_size)
    runtime_path.write_text(json.dumps(runtime))
    before = copy.deepcopy(job.__dict__)
    from services.ont_ngs_native_completion import validate_native_basecall
    with pytest.raises(completion.OntNgsCompletionError):
        validate_native_basecall(job)
    assert job.__dict__ == before


@pytest.mark.parametrize('requested,supported', [(False, True), (True, False)])
def test_summary_not_emitted_rejects_stray_or_forged_output(tmp_path, requested, supported):
    job, root = summary_fixture(tmp_path, requested=requested, supported=supported)
    (root / 'basecall/sequencing_summary.tsv').write_text(summary_text())
    before = copy.deepcopy(job.__dict__)
    from services.ont_ngs_native_completion import validate_native_basecall
    with pytest.raises(completion.OntNgsCompletionError):
        validate_native_basecall(job)
    assert job.__dict__ == before


@pytest.mark.parametrize('molecule', ['dna', 'rna'])
def test_summary_accepts_pinned_upstream_row_with_bam_qs_and_parent(tmp_path, molecule):
    # Verbatim header + first row from nanoporetech/dorado at
    # 7c84b01de1e46d4c5b2d5208fc430f27579a6c22,
    # regression_test/ref/linux/basecalling/Kit14_fast/sequencing_summary.txt.
    # BAM is a semantic identity fixture, not a claimed sequence reconstruction.
    native_text = 'input_filename\tbatch_id\tparent_read_id\tread_id\trun_id\tchannel\tmux\tminknow_events\tstart_time\tduration\tpasses_filtering\ttemplate_start\tnum_events_template\ttemplate_duration\tsequence_length_template\tmean_qscore_template\tpore_type\texperiment_id\tsample_id\tend_reason\nSQK-LSK114.pod5\t0\t00377c2d-e727-4961-b06e-5768579dad81\t00377c2d-e727-4961-b06e-5768579dad81\td432feef-df8b-49c6-86fd-346bd736e506\t225\t4\t7199\t118742.875000\t12.608000\tTRUE\t118743.039062\t10371\t12.445200\t3556\t9.587790\tnot_set\tmk1d_validation_batch5_agu_04022025\tMD-102807\tsignal_positive\n'
    job, root = summary_fixture(tmp_path, molecule=molecule)
    base = root / 'basecall'
    fields = native_text.splitlines()[1].split('\t')
    with pysam.AlignmentFile(str(base / 'calls.bam'), 'wb', header={'HD': {'VN': '1.6'}}) as bam:
        read = pysam.AlignedSegment()
        read.query_name = fields[3]
        read.query_sequence = 'A' * int(fields[14])
        read.flag = 4
        read.set_tag('pi', fields[2])
        read.set_tag('qs', float(fields[15]), value_type='f')
        bam.write(read)
    (base / 'sequencing_summary.tsv').write_text(native_text)
    path = base / 'dorado_runtime_provenance.json'
    runtime = json.loads(path.read_text())
    runtime['calls_bam'].update(sha256=_sha(base / 'calls.bam'),
        read_inventory_sha256=hashlib.sha256((fields[3] + '\n').encode()).hexdigest())
    runtime['summary']['output'].update(sha256=_sha(base / 'sequencing_summary.tsv'),
        size_bytes=(base / 'sequencing_summary.tsv').stat().st_size)
    path.write_text(json.dumps(runtime))
    assert _validate(job)['summary_state'] == 'validated'
