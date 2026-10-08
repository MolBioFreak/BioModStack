"""Pinned Dorado 1.3.1 duplex semantics; fixture executable, real BAM/samtools.

7c84b01de1e46d4c5b2d5208fc430f27579a6c22 duplex.cpp:373-410 forwards
simplex and duplex through the same writer. messages.cpp:92-93,153-154:
dx=0 simplex, -1 duplex parent, 1 duplex. DuplexReadTaggingNode:43-45
retags parents without surviving offspring; no positive duplex yield required.
"""
import json

import pysam
import pytest
from ngs_resource_fixture import ngs_resources

pytestmark = [pytest.mark.native_http, pytest.mark.usefixtures("ngs_resources", "native_http")]

from ngs_producer_fixtures import emit_receipt, producer_receipt
from test_ont_ngs_native_completion import _fixture, _sha, _validate, isolated_result_root
from services import ont_ngs_completion as completion
from services.ont_ngs_contract import DORADO_LOCK_PATH
from pathlib import Path
import copy
import runpy


def write_calls(base, classes):
    with pysam.AlignmentFile(str(base / 'calls.bam'), 'wb', header={'HD': {'VN': '1.6'}}) as bam:
        for i, dx in enumerate(classes):
            read = pysam.AlignedSegment()
            read.query_name = 'template;complement' if dx == 1 else f'read-{i}'
            read.query_sequence = 'ACGTACGT'
            read.query_qualities = pysam.qualitystring_to_array('IIIIIIII')
            read.flag = 4
            if dx is not None:
                read.set_tag('dx', dx)
            bam.write(read)


@pytest.mark.parametrize('classes', [[0, -1, -1, 1], [0], [1], [-1, 1]])
@pytest.mark.parametrize('requested', [False, True])
def test_duplex_producer_preserves_native_classifications(tmp_path, classes, requested):
    _, root = _fixture(tmp_path)
    base = root / 'basecall'
    write_calls(base, classes)
    emitted = emit_receipt(base, mode='duplex', requested=requested, help_failure=True)
    assert emitted.returncode == 0, emitted.stderr
    receipt = json.loads((base / 'dorado_runtime_provenance.json').read_text())
    assert receipt['calls_bam']['duplex_read_counts'] == {
        'simplex': classes.count(0), 'duplex_parent': classes.count(-1), 'duplex': classes.count(1)}
    assert receipt['calls_bam']['duplex_dx1'] == classes.count(1)
    assert receipt['summary'] == {'requested': requested, 'executed': False, 'capability': None, 'output': None}


@pytest.mark.parametrize('classes', [[None], [2], ['1'], []])
def test_duplex_producer_rejects_missing_invalid_tags_or_empty_stdout(tmp_path, classes):
    _, root = _fixture(tmp_path)
    base = root / 'basecall'
    write_calls(base, classes)
    emitted = emit_receipt(base, mode='duplex', requested=False)
    assert emitted.returncode != 0


def duplex_fixture(tmp_path, classes=(0, -1, -1, 1), requested=False, pairs_raw=b'template complement\n'):
    job, root = _fixture(tmp_path)
    base = root / 'basecall'
    job.params['dorado_basecall_mode'] = 'duplex'
    if requested == 'default':
        del job.params['emit_summary']
    else:
        job.params['emit_summary'] = requested
    pairs = Path(job.params['pod5_dir']) / 'pairs.txt'
    pairs.write_bytes(pairs_raw)
    job.params['duplex_pairs'] = str(pairs)
    pre = json.loads((base / 'dorado_preflight.json').read_text())
    stereo = json.loads(DORADO_LOCK_PATH.read_text())['models']['stereo']
    pre['selection'].update(mode='duplex', stereo_model_id=stereo['id'])
    pre['runtime']['assets']['models']['stereo'] = stereo
    pre['inputs']['read_count'] = 20  # Not output equality: splitting/filtering/duplex synthesis.
    parser = runpy.run_path(str(DORADO_LOCK_PATH.parents[2] / 'scripts/dorado_p4_preflight.py'))['_validate_pairs']
    pre['pairs'] = parser(pairs, pairs.parent, set(pairs_raw.decode().split()))
    (base / 'dorado_preflight.json').write_text(json.dumps(pre))
    write_calls(base, classes)
    emitted = emit_receipt(base, mode='duplex', requested=requested is not False, help_failure=True)
    assert emitted.returncode == 0, emitted.stderr
    return job, root


@pytest.mark.parametrize('requested', [False, True, 'default'])
@pytest.mark.parametrize('classes', [[0, -1, -1, 1], [0], [1], [-1, 1]])
def test_native_duplex_accepts_source_outcomes(tmp_path, monkeypatch, classes, requested):
    from services import ngs_alignment_sessions
    def forbidden(*args, **kwargs):
        raise AssertionError('duplex completion must not use derived stores')
    monkeypatch.setattr(ngs_alignment_sessions, 'build_alignment_sessions', forbidden)
    # Snapshot leases are native byte verification, not derived readiness.
    monkeypatch.setattr(ngs_alignment_sessions, 'build_alignment_presentation', forbidden)
    job, root = duplex_fixture(tmp_path, classes, requested)
    result = _validate(job)
    assert result['read_count'] == len(classes)
    assert result['duplex_read_counts'] == {'simplex': classes.count(0), 'duplex_parent': classes.count(-1), 'duplex': classes.count(1)}
    assert result['pairs_sha256'] == _sha(Path(job.params['duplex_pairs']))
    assert result['summary_state'] == ('not_requested' if requested is False else 'not_applicable')
    assert result['summary_read_count'] is None
    assert len(result['artifacts']) == 4


@pytest.mark.parametrize('raw', [b'template complement\nother partner\n', b'abcde abcdefghi\r\n'])
def test_native_duplex_accepts_equivalent_pair_maps_without_normalizing(tmp_path, raw):
    job, root = duplex_fixture(tmp_path, pairs_raw=raw)
    result = _validate(job)
    assert result['pairs_sha256'] == _sha(Path(job.params['duplex_pairs']))
    assert Path(job.params['duplex_pairs']).read_bytes() == raw


def test_pairs_snapshot_parser_uses_one_byte_authority(tmp_path):
    job, root = duplex_fixture(tmp_path)
    path = Path(job.params['duplex_pairs'])
    raw = path.read_bytes()
    parser = runpy.run_path(str(DORADO_LOCK_PATH.parents[2] / 'scripts/dorado_p4_preflight.py'))['_validate_pairs']
    expected = parser(path, path.parent, {'template', 'complement'})
    path.write_text('changed bytes')
    assert parser(path, path.parent, None, raw_bytes=raw) == expected


@pytest.mark.parametrize('damage', ['missing_pairs', 'pairs_bytes', 'pairs_symlink', 'pairs_path', 'pair_count', 'boolean_count', 'malformed_pairs', 'stereo_id', 'stereo_hash', 'dx_count', 'boolean_dx_count', 'summary_capability', 'summary_output', 'summary_executed', 'moves', 'trim', 'extra_input'])
def test_duplex_rejects_authority_damage_without_publication(tmp_path, damage):
    job, root = duplex_fixture(tmp_path, requested=True)
    base = root / 'basecall'
    pre = json.loads((base / 'dorado_preflight.json').read_text())
    runtime = json.loads((base / 'dorado_runtime_provenance.json').read_text())
    pairs = Path(job.params['duplex_pairs'])
    if damage == 'missing_pairs': pairs.unlink()
    if damage == 'pairs_bytes': pairs.write_text('other pair\n')
    if damage == 'pairs_symlink':
        target = tmp_path / 'elsewhere.txt'
        pairs.rename(target)
        pairs.symlink_to(target)
    if damage == 'pairs_path': job.params['duplex_pairs'] = str(pairs.parent / 'other.txt')
    if damage == 'pair_count': pre['pairs'].update(pair_count=2, read_count=4)
    if damage == 'boolean_count': pre['pairs']['pair_count'] = True
    if damage == 'malformed_pairs':
        pairs.write_text('template template\n')
        pre['pairs']['sha256'] = _sha(pairs)
    if damage == 'stereo_id': pre['selection']['stereo_model_id'] = 'wrong'
    if damage == 'stereo_hash': pre['runtime']['assets']['models']['stereo']['aggregate_sha256'] = '0' * 64
    if damage == 'dx_count': runtime['calls_bam']['duplex_read_counts']['duplex_parent'] = 0
    if damage == 'boolean_dx_count': runtime['calls_bam']['duplex_read_counts']['duplex'] = True
    if damage == 'summary_capability': runtime['summary']['capability'] = {'supported': True, 'help_sha256': 'a' * 64}
    if damage == 'summary_output': (base / 'sequencing_summary.tsv').write_text('fabricated')
    if damage == 'summary_executed': runtime['summary']['executed'] = True
    if damage == 'moves': job.params['emit_moves'] = runtime['emit_moves'] = True
    if damage == 'trim': job.params['trim_adapters'] = False
    if damage == 'extra_input': (pairs.parent / 'extra.txt').write_text('unexpected')
    (base / 'dorado_preflight.json').write_text(json.dumps(pre))
    runtime['preflight_sha256'] = _sha(base / 'dorado_preflight.json')
    (base / 'dorado_runtime_provenance.json').write_text(json.dumps(runtime))
    before = copy.deepcopy(job.__dict__)
    with pytest.raises(completion.OntNgsCompletionError):
        _validate(job)
    assert job.__dict__ == before


@pytest.mark.parametrize('raw', [
    b'template,complement\n', b'template\tcomplement\n',
    b'template  complement\n', b'template complement\r\n',
    b'template complement', b'template complement\nother partner',
    b' template complement\n', b'template complement \n',
    b'\ntemplate complement\n', b'template complement\n\n',
    b'template complement\rother partner\r', b'a long-complement\n',
    'é long\n'.encode(),
])
def test_native_duplex_rejects_rehashed_native_pair_mapping_divergence(tmp_path, raw):
    job, root = duplex_fixture(tmp_path, classes=[0])
    base = root / 'basecall'
    pairs = Path(job.params['duplex_pairs'])
    pairs.write_bytes(raw)
    pre = json.loads((base / 'dorado_preflight.json').read_text())
    count = len([line for line in raw.decode().splitlines() if line.strip()])
    pre['pairs'].update(sha256=_sha(pairs), pair_count=count, read_count=2 * count)
    (base / 'dorado_preflight.json').write_text(json.dumps(pre))
    runtime = json.loads((base / 'dorado_runtime_provenance.json').read_text())
    runtime['preflight_sha256'] = _sha(base / 'dorado_preflight.json')
    (base / 'dorado_runtime_provenance.json').write_text(json.dumps(runtime))
    before = copy.deepcopy(job.__dict__)
    with pytest.raises(completion.OntNgsCompletionError) as rejected:
        _validate(job)
    assert 'pinned Dorado 1.3.1 pair mapping' in str(rejected.value.__cause__)
    assert job.__dict__ == before


def attach_reference(job, root, tmp_path, outcome='mapped'):
    """Real indexed artifacts with the unchanged DoradoAlign snapshot contract.

    No alignment inference claimed: explicit mapped/unmapped/filtered fixtures.
    """
    import hashlib
    align = root / 'align'
    align.mkdir()
    selected = tmp_path / 'selected.fasta'
    selected.write_text('>ref\nACGTACGT\n')
    (align / 'reference.fasta').write_bytes(selected.read_bytes())
    pysam.faidx(str(align / 'reference.fasta'))
    job.params.update(reference_fasta=str(selected), reference_sequence_sha256=hashlib.sha256(b'ACGTACGT').hexdigest(), bam_min_mapq=20 if outcome == 'filtered' else 0)
    with pysam.AlignmentFile(str(root / 'basecall/calls.bam'), 'rb', check_sq=False) as bam:
        reads = list(bam.fetch(until_eof=True))
    with pysam.AlignmentFile(str(align / 'aligned.bam'), 'wb', header={'HD': {'VN': '1.6', 'SO': 'coordinate'}, 'SQ': [{'SN': 'ref', 'LN': 8}]}) as bam:
        if outcome != 'filtered':
            for source_read in reads:
                read = pysam.AlignedSegment.fromstring(source_read.to_string(), bam.header)
                if outcome == 'mapped':
                    read.flag = 0
                    read.reference_id = 0
                    read.reference_start = 0
                    read.cigarstring = '8M'
                    read.mapping_quality = 60
                bam.write(read)
    pysam.index(str(align / 'aligned.bam'))
    source_sha = _sha(root / 'basecall/calls.bam')
    receipt = {'source_sha256_before': source_sha, 'source_sha256_after': source_sha, 'source_immutable': 'true',
               'reference_raw_sha256_before': _sha(selected), 'reference_raw_sha256_after': _sha(selected),
               'reference_sequence_sha256': job.params['reference_sequence_sha256'], 'reference_immutable': 'true',
               'bam_min_mapq': str(job.params['bam_min_mapq']), 'input_records': str(len(reads)),
               'output_records': '0' if outcome == 'filtered' else str(len(reads))}
    (align / 'align.log').write_text(''.join(f'{key}={value}\n' for key, value in receipt.items())
        + producer_receipt(align / 'fixture-align', ('modules/ngs/dorado_align.nf',), ('dorado', 'samtools')))
    job.provenance['stage_terminal_states']['dorado_align'] = {'status': 'complete', 'outputs': [str(align / name) for name in ('aligned.bam', 'aligned.bam.bai', 'reference.fasta', 'reference.fasta.fai', 'align.log')]}


@pytest.mark.parametrize('requested', [False, True, 'default'])
@pytest.mark.parametrize('classes', [[0, -1, -1, 1], [0], [1], [-1, 1]])
@pytest.mark.parametrize('outcome', ['mapped', 'unmapped', 'filtered'])
def test_native_duplex_reference_outcome_matrix(tmp_path, classes, requested, outcome):
    job, root = duplex_fixture(tmp_path, classes, requested)
    attach_reference(job, root, tmp_path, outcome)
    result = _validate(job)
    assert result['read_count'] == len(classes)
    assert result['alignment']['record_count'] == (0 if outcome == 'filtered' else len(classes))
    assert result['alignment']['mapped_records'] == (len(classes) if outcome == 'mapped' else 0)
    assert result['duplex_read_counts']['duplex'] == classes.count(1)
    assert result['summary_state'] == ('not_requested' if requested is False else 'not_applicable')
    assert len(result['artifacts']) == 9


@pytest.mark.asyncio
@pytest.mark.parametrize('requested', [False, True, 'default'])
@pytest.mark.parametrize('reference', [False, True])
async def test_native_duplex_cancellation_loser(tmp_path, monkeypatch, requested, reference):
    import test_ont_ngs_native_completion as native_tests
    job, root = duplex_fixture(tmp_path, requested=requested)
    if reference:
        attach_reference(job, root, tmp_path)
    before = {str(p.relative_to(root)): _sha(p) for p in root.rglob('*') if p.is_file()}
    monkeypatch.setattr(native_tests, '_fixture', lambda unused: (job, root))
    await native_tests.test_native_completion_losing_cas_publishes_no_result(tmp_path, None)
    assert {str(p.relative_to(root)): _sha(p) for p in root.rglob('*') if p.is_file()} == before


def test_native_duplex_pairs_changed_during_alignment_fails_before_publication(tmp_path, monkeypatch):
    from services import ont_ngs_native_completion as native
    job, root = duplex_fixture(tmp_path)
    attach_reference(job, root, tmp_path)
    original = native._validate_reference_alignment
    def changed(*args):
        result = original(*args)
        Path(job.params['duplex_pairs']).write_text('changed after basecall validation')
        return result
    monkeypatch.setattr(native, '_validate_reference_alignment', changed)
    before = copy.deepcopy(job.__dict__)
    with pytest.raises(completion.OntNgsCompletionError):
        _validate(job)
    assert job.__dict__ == before


@pytest.mark.parametrize('dx', [None, '1', 2])
def test_native_duplex_rehashed_invalid_dx_fails(tmp_path, dx):
    job, root = duplex_fixture(tmp_path, classes=[1])
    base = root / 'basecall'
    write_calls(base, [dx])
    runtime = json.loads((base / 'dorado_runtime_provenance.json').read_text())
    runtime['calls_bam']['sha256'] = _sha(base / 'calls.bam')
    (base / 'dorado_runtime_provenance.json').write_text(json.dumps(runtime))
    with pytest.raises(completion.OntNgsCompletionError):
        _validate(job)
