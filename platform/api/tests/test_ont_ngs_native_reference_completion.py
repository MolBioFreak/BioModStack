"""Native optional-reference products; real pysam/FASTA, no scientific jobs."""
import asyncio
import copy
import hashlib

import pysam
import pytest
from ngs_resource_fixture import ngs_resources

pytestmark = [pytest.mark.native_http, pytest.mark.usefixtures("ngs_resources", "native_http")]

from services import ont_ngs_completion as completion
from services.ont_ngs_native_completion import validate_native_basecall
from test_ont_ngs_native_completion import _sha, _nextflow_native_entry, isolated_result_root
from test_ont_ngs_native_summary_completion import summary_fixture
from ngs_producer_fixtures import producer_receipt


def reference_fixture(tmp_path, molecule='dna', requested=False, supported=True, mapq=0, outcome='mapped'):
    job, root = summary_fixture(tmp_path, molecule, requested, supported)
    align = root / 'align'
    align.mkdir()
    reference = tmp_path / 'selected.fasta'
    reference.write_text('>ref\nACGTACGT\n')
    (align / 'reference.fasta').write_bytes(reference.read_bytes())
    pysam.faidx(str(align / 'reference.fasta'))
    job.params.update(reference_fasta=str(reference), reference_sequence_sha256=hashlib.sha256(b'ACGTACGT').hexdigest(), bam_min_mapq=mapq)
    with pysam.AlignmentFile(str(align / 'aligned.bam'), 'wb', header={'HD': {'VN': '1.6', 'SO': 'coordinate'}, 'SQ': [{'SN': 'ref', 'LN': 8}]}) as bam:
        if outcome != 'filtered':
            read = pysam.AlignedSegment()
            read.query_name = 'read-1'
            read.query_sequence = 'ACGT'
            read.query_qualities = pysam.qualitystring_to_array('IIII')
            read.flag = 4 if outcome == 'unmapped' else 0
            if outcome == 'mapped':
                read.reference_id = 0
                read.reference_start = 0
                read.cigarstring = '4M'
                read.mapping_quality = 60
            bam.write(read)
    pysam.index(str(align / 'aligned.bam'))
    evidence = {
        'source_sha256_before': _sha(root / 'basecall/calls.bam'),
        'source_sha256_after': _sha(root / 'basecall/calls.bam'), 'source_immutable': 'true',
        'reference_raw_sha256_before': _sha(reference), 'reference_raw_sha256_after': _sha(reference),
        'reference_sequence_sha256': job.params['reference_sequence_sha256'], 'reference_immutable': 'true',
        'bam_min_mapq': str(mapq), 'input_records': '1', 'output_records': '0' if outcome == 'filtered' else '1',
    }
    (align / 'align.log').write_text(''.join(f'{k}={v}\n' for k, v in evidence.items())
        + producer_receipt(align / 'fixture-align', ('modules/ngs/dorado_align.nf',), ('dorado', 'samtools')))
    outputs = [str(align / name) for name in ('aligned.bam', 'aligned.bam.bai', 'reference.fasta', 'reference.fasta.fai', 'align.log')]
    job.provenance['stage_terminal_states']['dorado_align'] = {'status': 'complete', 'outputs': outputs}
    return job, root


@pytest.mark.parametrize('molecule', ['dna', 'rna'])
@pytest.mark.parametrize('requested', [True, 'default', False])
@pytest.mark.parametrize('supported', [True, False])
def test_reference_native_dispatch_and_summary_independence(tmp_path, monkeypatch, molecule, requested, supported):
    from services import ngs_alignment_sessions
    def forbidden(*args, **kwargs):
        raise AssertionError('scientific completion must not require derived readiness')
    monkeypatch.setattr(ngs_alignment_sessions, 'build_alignment_sessions', forbidden)
    # Verified snapshots are now the native byte transport, not a derived
    # catalog/preview. Completion must still never build those products.
    monkeypatch.setattr(ngs_alignment_sessions, 'build_alignment_presentation', forbidden)
    job, root = reference_fixture(tmp_path, molecule, requested, supported)
    assert asyncio.run(_nextflow_native_entry(job)) is True
    result = job.provenance['result_integrity']
    assert result['alignment']['mapped_records'] == 1
    assert result['alignment']['reference_sequence_sha256'] == job.params['reference_sequence_sha256']
    assert result['alignment']['source_bam_sha256'] == _sha(root / 'basecall/calls.bam')
    assert result['summary_state'] == ('not_requested' if requested is False else 'validated' if supported else 'unsupported')
    assert {a['path'] for a in result['artifacts']} >= {'align/aligned.bam', 'align/aligned.bam.bai', 'align/reference.fasta', 'align/reference.fasta.fai', 'align/align.log'}


@pytest.mark.parametrize('outcome,mapq', [('unmapped', 0), ('filtered', 20)])
def test_reference_zero_mapping_is_valid_scientific_outcome(tmp_path, outcome, mapq):
    job, _ = reference_fixture(tmp_path, outcome=outcome, mapq=mapq)
    result = validate_native_basecall(job)
    assert result['alignment']['mapped_records'] == 0
    assert result['alignment']['record_count'] == (0 if outcome == 'filtered' else 1)


@pytest.mark.parametrize('damage', ['missing_stage', 'stage_failed', 'stage_outputs', 'missing_bam', 'corrupt_bam', 'index', 'fai', 'reference', 'reference_input', 'reference_digest', 'source_digest', 'mapq', 'input_count', 'output_count', 'duplicate_receipt', 'missing_receipt', 'symlink', 'contig', 'foreign_read', 'low_mapq'])
def test_reference_native_rejects_invalid_authority_without_publication(tmp_path, damage):
    job, root = reference_fixture(tmp_path, mapq=20)
    align = root / 'align'
    log = align / 'align.log'
    if damage == 'missing_stage': del job.provenance['stage_terminal_states']['dorado_align']
    if damage == 'stage_failed': job.provenance['stage_terminal_states']['dorado_align']['status'] = 'failed'
    if damage == 'stage_outputs': job.provenance['stage_terminal_states']['dorado_align']['outputs'].pop()
    if damage == 'missing_bam': (align / 'aligned.bam').unlink()
    if damage == 'corrupt_bam': (align / 'aligned.bam').write_bytes(b'bad')
    if damage == 'index': (align / 'aligned.bam.bai').write_bytes(b'bad')
    if damage == 'fai': (align / 'reference.fasta.fai').write_text('ref\t8\t0\t8\t9\n')
    if damage == 'reference': (align / 'reference.fasta').write_text('>ref\nTTTTTTTT\n')
    if damage == 'reference_input': __import__('pathlib').Path(job.params['reference_fasta']).write_text('>ref\nTTTTTTTT\n')
    if damage == 'reference_digest': job.params['reference_sequence_sha256'] = '0' * 64
    if damage == 'source_digest': log.write_text(log.read_text().replace(_sha(root / 'basecall/calls.bam'), '0' * 64))
    if damage == 'mapq': job.params['bam_min_mapq'] = 21
    if damage == 'input_count': log.write_text(log.read_text().replace('input_records=1', 'input_records=2'))
    if damage == 'output_count': log.write_text(log.read_text().replace('output_records=1', 'output_records=2'))
    if damage == 'duplicate_receipt': log.write_text(log.read_text() + 'bam_min_mapq=20\n')
    if damage == 'missing_receipt': log.write_text('native diagnostic only\n')
    if damage == 'symlink':
        (align / 'reference.fasta').unlink()
        (align / 'reference.fasta').symlink_to(job.params['reference_fasta'])
    if damage in {'contig', 'foreign_read', 'low_mapq'}:
        with pysam.AlignmentFile(str(align / 'aligned.bam'), 'rb') as bam:
            header = bam.header.to_dict()
            reads = list(bam)
        if damage == 'contig': header['SQ'][0]['LN'] = 9
        if damage == 'foreign_read': reads[0].query_name = 'foreign'
        if damage == 'low_mapq': reads[0].mapping_quality = 10
        with pysam.AlignmentFile(str(align / 'aligned.bam'), 'wb', header=header) as bam:
            for read in reads: bam.write(read)
        pysam.index(str(align / 'aligned.bam'))
    before = copy.deepcopy(job.__dict__)
    with pytest.raises(completion.OntNgsCompletionError):
        validate_native_basecall(job)
    assert job.__dict__ == before


@pytest.mark.asyncio
@pytest.mark.parametrize('molecule', ['dna', 'rna'])
@pytest.mark.parametrize('requested,supported', [(False, True), (True, True), ('default', False)])
async def test_reference_native_cas_loser_publishes_nothing(tmp_path, monkeypatch, molecule, requested, supported):
    import test_ont_ngs_native_completion as native_tests
    job, root = reference_fixture(tmp_path, molecule, requested, supported)
    monkeypatch.setattr(native_tests, '_fixture', lambda unused: (job, root))
    await native_tests.test_native_completion_losing_cas_publishes_no_result(tmp_path, None)


def test_reference_rejects_stale_index_for_all_unmapped_bam(tmp_path):
    job, root = reference_fixture(tmp_path, outcome='unmapped')
    stale = tmp_path / 'stale.bam'
    with pysam.AlignmentFile(str(stale), 'wb', header={'HD': {'VN': '1.6', 'SO': 'coordinate'}, 'SQ': [{'SN': 'ref', 'LN': 8}]}):
        pass
    pysam.index(str(stale))
    (root / 'align/aligned.bam.bai').write_bytes((tmp_path / 'stale.bam.bai').read_bytes())
    with pytest.raises(completion.OntNgsCompletionError):
        validate_native_basecall(job)
