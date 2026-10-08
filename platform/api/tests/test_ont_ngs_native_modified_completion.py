"""Locked DNA HAC simplex modifications: real BAM and producer shell, no inference.

Dorado 1.3.1 7c84b01 messages.cpp:182-336 emits MN:i, MM:Z groups
and ML:B:C; no-position groups are valid. Locked config.toml files (archives
verified against aggregate_sha256): C/h,m and A/a, single-base motifs.
ModBaseChunkCallerNode:310-325,720-729 initializes even no-motif reads.
"""
from array import array
import copy
import json

import pysam
import pytest

from test_dorado_summary_emitter import emit_receipt
from test_ont_ngs_native_completion import _fixture, _sha, _validate, isolated_result_root
from services import ont_ngs_completion as completion
from services.ont_ngs_contract import DORADO_LOCK_PATH


def modified_fixture(tmp_path, modification='5mC_5hmC', *, empty=False, requested=False, supported=True):
    job, root = _fixture(tmp_path)
    base = root / 'basecall'
    lock = json.loads(DORADO_LOCK_PATH.read_text())
    model = lock['models']['dna']['hac']
    mod = lock['models']['modified_bases'][modification]
    job.params.update(dorado_quality_mode='hac', dorado_resolved_model_id=model['id'], modified_bases=modification)
    if requested == 'default':
        del job.params['emit_summary']
    else:
        job.params['emit_summary'] = requested
    pre = json.loads((base / 'dorado_preflight.json').read_text())
    pre['selection'].update(quality='hac', model_id=model['id'], model_aggregate_sha256=model['aggregate_sha256'],
                            modified_bases=modification, modified_bases_model_id=mod['id'])
    pre['runtime']['assets']['models'].update(base=model, modified_bases=mod)
    (base / 'dorado_preflight.json').write_text(json.dumps(pre))
    runtime = json.loads((base / 'dorado_runtime_provenance.json').read_text())
    runtime['model_id'] = model['id']
    (base / 'dorado_runtime_provenance.json').write_text(json.dumps(runtime))
    with pysam.AlignmentFile(str(base / 'calls.bam'), 'wb', header={'HD': {'VN': '1.6'}}) as bam:
        read = pysam.AlignedSegment()
        read.query_name = 'read-1'
        read.query_sequence = 'ACGT'
        read.query_qualities = pysam.qualitystring_to_array('IIII')
        read.flag = 4
        groups = ['C+h.', 'C+m.'] if modification == '5mC_5hmC' else ['A+a.']
        read.set_tag('MN', 4)
        read.set_tag('MM', ''.join(g + ('' if empty else ',0') + ';' for g in groups))
        read.set_tag('ML', array('B', [] if empty else ([0, 255] if len(groups) == 2 else [255])))
        bam.write(read)
    emitted = emit_receipt(base, requested=requested is not False, supported=supported, modified=True)
    assert emitted.returncode == 0, emitted.stderr
    return job, root


@pytest.mark.parametrize('modification', ['5mC_5hmC', '6mA'])
def test_actual_modified_producer_records_invoked_model(tmp_path, modification):
    job, root = modified_fixture(tmp_path, modification)
    base = root / 'basecall'
    pre = json.loads((base / 'dorado_preflight.json').read_text())
    runtime = json.loads((base / 'dorado_runtime_provenance.json').read_text())
    argv = (base / 'fixture-binaries/argv.txt').read_text().splitlines()
    assert argv[0] == 'basecaller'
    assert argv[argv.index('--modified-bases-models') + 1] == str(base / 'sealed_models' / pre['selection']['modified_bases_model_id'])
    assert runtime['modified_bases_model_id'] == pre['selection']['modified_bases_model_id']


@pytest.mark.parametrize('modification', ['5mC_5hmC', '6mA'])
@pytest.mark.parametrize('empty', [False, True])
def test_native_modified_simplex_accepts_positions_or_empty_groups(tmp_path, modification, empty):
    job, root = modified_fixture(tmp_path, modification, empty=empty)
    result = _validate(job)
    assert result['modified_bases']['selection'] == modification
    assert result['modified_bases']['probability_count'] == (0 if empty else (2 if modification == '5mC_5hmC' else 1))
    assert result['modified_bases']['model_id'] == json.loads((root / 'basecall/dorado_preflight.json').read_text())['selection']['modified_bases_model_id']
    assert len(result['artifacts']) == 4


def rewrite_calls(root, mutate):
    base = root / 'basecall'
    with pysam.AlignmentFile(str(base / 'calls.bam'), 'rb', check_sq=False) as bam:
        header, reads = bam.header, list(bam.fetch(until_eof=True))
    for read in reads:
        mutate(read)
    with pysam.AlignmentFile(str(base / 'calls.bam'), 'wb', header=header) as bam:
        for read in reads:
            bam.write(read)
    runtime = json.loads((base / 'dorado_runtime_provenance.json').read_text())
    runtime['calls_bam']['sha256'] = _sha(base / 'calls.bam')
    (base / 'dorado_runtime_provenance.json').write_text(json.dumps(runtime))


@pytest.mark.parametrize('modification', ['5mC_5hmC', '6mA'])
@pytest.mark.parametrize('damage', ['missing_MM', 'missing_ML', 'missing_MN', 'duplicate_MM', 'duplicate_ML', 'duplicate_MN',
                                  'mn_length', 'mn_type', 'ml_type', 'ml_short', 'ml_long',
                                  'coordinate', 'negative', 'delimiter', 'wrong_code', 'wrong_base', 'wrong_strand',
                                  'wrong_context', 'empty_MM'])
def test_modified_rehashed_bam_rejects_invalid_tags_without_publication(tmp_path, modification, damage):
    job, root = modified_fixture(tmp_path, modification)
    def mutate(read):
        mm = read.get_tag('MM')
        if damage.startswith('missing_'): read.set_tag(damage.removeprefix('missing_'), None)
        if damage.startswith('duplicate_'):
            tag = damage.removeprefix('duplicate_')
            read.set_tag(tag, read.get_tag(tag), replace=False)
        if damage == 'mn_length': read.set_tag('MN', 5)
        if damage == 'mn_type': read.set_tag('MN', '4')
        if damage == 'ml_type': read.set_tag('ML', array('H', read.get_tag('ML')))
        if damage == 'ml_short': read.set_tag('ML', array('B', []))
        if damage == 'ml_long': read.set_tag('ML', array('B', [1, 2, 3]))
        if damage == 'coordinate': read.set_tag('MM', mm.replace(',0', ',1'))
        if damage == 'negative': read.set_tag('MM', mm.replace(',0', ',-1'))
        if damage == 'delimiter': read.set_tag('MM', mm[:-1])
        if damage == 'wrong_code': read.set_tag('MM', mm.replace('+', '+z'))
        if damage == 'wrong_base': read.set_tag('MM', mm.replace(mm[0], 'T'))
        if damage == 'wrong_strand': read.set_tag('MM', mm.replace('+', '-'))
        if damage == 'wrong_context': read.set_tag('MM', mm.replace('.', '?'))
        if damage == 'empty_MM': read.set_tag('MM', '')
    rewrite_calls(root, mutate)
    before = copy.deepcopy(job.__dict__)
    with pytest.raises(completion.OntNgsCompletionError):
        _validate(job)
    assert job.__dict__ == before


@pytest.mark.parametrize('modification', ['5mC_5hmC', '6mA'])
@pytest.mark.parametrize('damage', ['selection', 'selection_id', 'runtime_id', 'missing_runtime_id', 'asset_hash', 'asset_files', 'asset_bytes', 'missing_asset'])
def test_modified_model_authority_rejects_relinked_receipts(tmp_path, modification, damage):
    job, root = modified_fixture(tmp_path, modification)
    base = root / 'basecall'
    pre = json.loads((base / 'dorado_preflight.json').read_text())
    runtime = json.loads((base / 'dorado_runtime_provenance.json').read_text())
    if damage == 'selection': pre['selection']['modified_bases'] = 'none'
    if damage == 'selection_id': pre['selection']['modified_bases_model_id'] = 'wrong'
    if damage == 'runtime_id': runtime['modified_bases_model_id'] = 'wrong'
    if damage == 'missing_runtime_id': del runtime['modified_bases_model_id']
    if damage == 'asset_hash': pre['runtime']['assets']['models']['modified_bases']['aggregate_sha256'] = '0' * 64
    if damage == 'asset_files': pre['runtime']['assets']['models']['modified_bases']['files'] = True
    if damage == 'asset_bytes': pre['runtime']['assets']['models']['modified_bases']['bytes'] = 1
    if damage == 'missing_asset': del pre['runtime']['assets']['models']['modified_bases']
    (base / 'dorado_preflight.json').write_text(json.dumps(pre))
    runtime['preflight_sha256'] = _sha(base / 'dorado_preflight.json')
    (base / 'dorado_runtime_provenance.json').write_text(json.dumps(runtime))
    before = copy.deepcopy(job.__dict__)
    with pytest.raises(completion.OntNgsCompletionError):
        _validate(job)
    assert job.__dict__ == before


@pytest.mark.parametrize('modification', ['5mC_5hmC', '6mA'])
@pytest.mark.parametrize('variant', ['no_motif', 'skip_first', 'multiple_sites'])
def test_modified_native_coordinate_and_no_motif_outcomes(tmp_path, modification, variant):
    job, root = modified_fixture(tmp_path, modification, empty=variant == 'no_motif')
    def mutate(read):
        if variant == 'no_motif':
            read.query_sequence = 'GGTT'
        else:
            read.query_sequence = 'ACGTACGT'
            read.set_tag('MN', 8)
            delta = ',1' if variant == 'skip_first' else ',0,0'
            groups = ['C+h.', 'C+m.'] if modification == '5mC_5hmC' else ['A+a.']
            read.set_tag('MM', ''.join(g + delta + ';' for g in groups))
            read.set_tag('ML', array('B', [128] * len(groups) * (1 if variant == 'skip_first' else 2)))
        read.query_qualities = pysam.qualitystring_to_array('I' * read.query_length)
    rewrite_calls(root, mutate)
    result = _validate(job)
    assert result['modified_bases']['probability_count'] == (0 if variant == 'no_motif' else
            (2 if modification == '5mC_5hmC' else 1) * (2 if variant == 'multiple_sites' else 1))


def attach_modified_reference(job, root, tmp_path, outcome='mapped'):
    # Reuse the accepted real BAM/index/reference receipt helper. It uses 8M;
    # our summary-compatible four-base read needs 4M, not a fake alignment run.
    from test_ont_ngs_native_duplex_completion import attach_reference
    attach_reference(job, root, tmp_path, 'unmapped' if outcome == 'mapped' else outcome)
    if outcome == 'mapped':
        path = root / 'align/aligned.bam'
        with pysam.AlignmentFile(str(path), 'rb') as bam:
            header, reads = bam.header, list(bam.fetch(until_eof=True))
        with pysam.AlignmentFile(str(path), 'wb', header=header) as bam:
            for read in reads:
                read.flag = 0
                read.reference_id = 0
                read.reference_start = 0
                read.mapping_quality = 60
                read.cigarstring = '4M'
                bam.write(read)
        pysam.index(str(path))


@pytest.mark.parametrize('modification', ['5mC_5hmC', '6mA'])
@pytest.mark.parametrize('requested', [False, True, 'default'])
@pytest.mark.parametrize('supported', [False, True])
@pytest.mark.parametrize('reference', [False, True])
def test_modified_summary_reference_matrix_and_pure_preparation(tmp_path, monkeypatch, modification, requested, supported, reference):
    from services import ngs_alignment_sessions, ont_ngs_native_completion as native
    def forbidden(*args, **kwargs):
        raise AssertionError('native modified completion must not use derived readiness')
    monkeypatch.setattr(ngs_alignment_sessions, 'build_alignment_sessions', forbidden)
    monkeypatch.setattr(ngs_alignment_sessions, 'open_verified_artifact_snapshot', forbidden)
    job, root = modified_fixture(tmp_path, modification, requested=requested, supported=supported)
    if reference:
        attach_modified_reference(job, root, tmp_path)
    before = copy.deepcopy(job.__dict__)
    files = {str(p.relative_to(root)): _sha(p) for p in root.rglob('*') if p.is_file()}
    result = native.validate_native_basecall(job)
    assert job.__dict__ == before
    assert {str(p.relative_to(root)): _sha(p) for p in root.rglob('*') if p.is_file()} == files
    assert result['summary_state'] == ('not_requested' if requested is False else 'validated' if supported else 'unsupported')
    assert len(result['artifacts']) == 4 + (5 if reference else 0) + (1 if supported and requested is not False else 0)


@pytest.mark.parametrize('modification', ['5mC_5hmC', '6mA'])
@pytest.mark.parametrize('outcome', ['unmapped', 'filtered'])
def test_modified_reference_zero_mapping_outcomes(tmp_path, modification, outcome):
    job, root = modified_fixture(tmp_path, modification)
    attach_modified_reference(job, root, tmp_path, outcome)
    result = _validate(job)
    assert result['alignment']['record_count'] == (0 if outcome == 'filtered' else 1)
    assert result['alignment']['mapped_records'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('modification', ['5mC_5hmC', '6mA'])
@pytest.mark.parametrize('reference', [False, True])
async def test_modified_native_cancellation_loser(tmp_path, monkeypatch, modification, reference):
    import test_ont_ngs_native_completion as native_tests
    job, root = modified_fixture(tmp_path, modification, requested=True)
    if reference:
        attach_modified_reference(job, root, tmp_path)
    before = {str(p.relative_to(root)): _sha(p) for p in root.rglob('*') if p.is_file()}
    monkeypatch.setattr(native_tests, '_fixture', lambda unused: (job, root))
    await native_tests.test_native_completion_losing_cas_publishes_no_result(tmp_path, None)
    assert {str(p.relative_to(root)): _sha(p) for p in root.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('change', [dict(dorado_quality_mode='sup'), dict(dorado_quality_mode='fast'),
                                  dict(dorado_basecall_mode='duplex'), dict(barcode_kit='SQK-RBK114-96'),
                                  dict(sample_sheet='/selected.csv'), dict(modified_bases='unknown')])
def test_modified_dispatch_keeps_unsupported_combinations_closed(tmp_path, change):
    job, root = modified_fixture(tmp_path)
    job.params.update(change)
    assert completion.ont_completion_lane(job) is None


@pytest.mark.parametrize('damage', ['missing_MM', 'missing_ML', 'missing_MN', 'probability', 'coordinate'])
def test_modified_alignment_preserves_native_primary_tags(tmp_path, damage):
    job, root = modified_fixture(tmp_path)
    attach_modified_reference(job, root, tmp_path)
    path = root / 'align/aligned.bam'
    with pysam.AlignmentFile(str(path), 'rb') as bam:
        header, reads = bam.header, list(bam.fetch(until_eof=True))
    read = reads[0]
    if damage.startswith('missing_'): read.set_tag(damage.removeprefix('missing_'), None)
    if damage == 'probability': read.set_tag('ML', array('B', [1, 254]))
    if damage == 'coordinate': read.set_tag('MM', 'C+h.;C+m.;'); read.set_tag('ML', array('B'))
    with pysam.AlignmentFile(str(path), 'wb', header=header) as bam:
        bam.write(read)
    pysam.index(str(path))
    before = copy.deepcopy(job.__dict__)
    with pytest.raises(completion.OntNgsCompletionError):
        _validate(job)
    assert job.__dict__ == before


@pytest.mark.parametrize('modification', ['5mC_5hmC', '6mA'])
def test_modified_alignment_reverse_orientation_preserves_as_sequenced_tags(tmp_path, modification):
    job, root = modified_fixture(tmp_path, modification)
    def change_sequence(read):
        read.query_sequence = 'ACGA'
        read.query_qualities = pysam.qualitystring_to_array('IIII')
    rewrite_calls(root, change_sequence)
    attach_modified_reference(job, root, tmp_path)
    path = root / 'align/aligned.bam'
    with pysam.AlignmentFile(str(path), 'rb') as bam:
        header, reads = bam.header, list(bam.fetch(until_eof=True))
    with pysam.AlignmentFile(str(path), 'wb', header=header) as bam:
        for read in reads:
            read.flag = 16
            read.query_sequence = 'TCGT'
            read.query_qualities = pysam.qualitystring_to_array('IIII')
            bam.write(read)
    pysam.index(str(path))
    assert _validate(job)['alignment']['mapped_records'] == 1


@pytest.mark.parametrize('mm', ['C+m.,0;C+h.,0;', 'C+h.,0;C+m.;'])
def test_locked_cytosine_channels_keep_order_and_shared_mask(tmp_path, mm):
    job, root = modified_fixture(tmp_path)
    def mutate(read):
        read.set_tag('MM', mm)
        read.set_tag('ML', array('B', [128] * mm.count(',')))
    rewrite_calls(root, mutate)
    with pytest.raises(completion.OntNgsCompletionError):
        _validate(job)


@pytest.mark.parametrize('modification', ['5mC_5hmC', '6mA'])
def test_modified_reference_hardclipped_supplementary_may_omit_tags(tmp_path, modification):
    job, root = modified_fixture(tmp_path, modification)
    attach_modified_reference(job, root, tmp_path)
    path = root / 'align/aligned.bam'
    with pysam.AlignmentFile(str(path), 'rb') as bam:
        header, reads = bam.header, list(bam.fetch(until_eof=True))
    supplementary = pysam.AlignedSegment.fromstring(reads[0].to_string(), header)
    supplementary.flag = 2048
    supplementary.query_sequence = 'CGT'
    supplementary.query_qualities = pysam.qualitystring_to_array('III')
    supplementary.cigarstring = '1H3M'
    for tag in ('MM', 'ML', 'MN'):
        supplementary.set_tag(tag, None)
    with pysam.AlignmentFile(str(path), 'wb', header=header) as bam:
        bam.write(reads[0])
        bam.write(supplementary)
    pysam.index(str(path))
    log = root / 'align/align.log'
    log.write_text(log.read_text().replace('output_records=1', 'output_records=2'))
    assert _validate(job)['alignment']['record_count'] == 2


@pytest.mark.parametrize('damage', [None, 'partial', 'probability', 'coordinate'])
def test_modified_softclipped_supplementary_retained_tags_are_source_bound(tmp_path, damage):
    job, root = modified_fixture(tmp_path)
    attach_modified_reference(job, root, tmp_path)
    path = root / 'align/aligned.bam'
    with pysam.AlignmentFile(str(path), 'rb') as bam:
        header, reads = bam.header, list(bam.fetch(until_eof=True))
    supplementary = pysam.AlignedSegment.fromstring(reads[0].to_string(), header)
    supplementary.flag = 2048
    supplementary.cigarstring = '1S3M'
    if damage == 'partial': supplementary.set_tag('MM', None)
    if damage == 'probability': supplementary.set_tag('ML', array('B', [1, 254]))
    if damage == 'coordinate': supplementary.set_tag('MM', 'C+h.,5;C+m.,5;')
    with pysam.AlignmentFile(str(path), 'wb', header=header) as bam:
        bam.write(reads[0])
        bam.write(supplementary)
    pysam.index(str(path))
    log = root / 'align/align.log'
    log.write_text(log.read_text().replace('output_records=1', 'output_records=2'))
    if damage is None:
        assert _validate(job)['alignment']['record_count'] == 2
    else:
        with pytest.raises(completion.OntNgsCompletionError):
            _validate(job)
