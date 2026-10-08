"""External BAM native barrier, using actual producer shells on temporary data."""
import asyncio
import array
import copy
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pysam
import pytest

from services import ont_ngs_completion as completion
from test_ont_ngs_native_completion import _sha, _nextflow_native_entry, isolated_result_root
from test_ont_ngs_native_methylation_completion import emit_modkit, BED, SUMMARY, ROOT


def shell(process, cwd, values, bins):
    text = (ROOT / 'modules/ngs/bam_prepare.nf').read_text().split('process ' + process + ' {', 1)[1]
    script = text.split('"""', 1)[1].split('"""', 1)[0]
    for key, value in values.items():
        script = script.replace('${' + key + '}', str(value))
    script = script.replace('\\$', '$').replace('\\\\\n', '\\\n')
    return subprocess.run(['bash', '-c', script], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, 'PATH': str(bins) + ':' + os.environ['PATH']})


def external_fixture(tmp_path, *, enabled=True, reference=True, threshold=0, code='m', m5=True,
                     mapq=0, outcome='mapped', bed=None, summary=None, mixed_legacy=False, base='C'):
    root = tmp_path / 'result'
    align = root / 'align'
    align.mkdir(parents=True)
    bins = tmp_path / 'bins'
    bins.mkdir()
    (bins / 'samtools').write_text(f'#!{sys.executable}\nimport sys,pysam\n'
        'try:\n r=getattr(pysam,sys.argv[1])(*sys.argv[2:])\n'
        'except pysam.SamtoolsError as e:\n print(e,file=sys.stderr);sys.exit(1)\n'
        'if r: sys.stdout.write(r)\n')
    (bins / 'samtools').chmod(0o755)
    source = tmp_path / 'external.bam'
    sq = {'SN': 'ref', 'LN': 8}
    if m5: sq['M5'] = hashlib.md5(b'ACGTACGT').hexdigest()
    with pysam.AlignmentFile(source, 'wb', header={'HD': {'VN': '1.6', 'SO': 'unsorted'}, 'SQ': [sq]}) as bam:
        for i, quality in enumerate([60, 5]):
            read = pysam.AlignedSegment(bam.header)
            read.query_name = f'external-{i}'
            read.query_sequence = 'ACGT'
            read.query_qualities = pysam.qualitystring_to_array('IIII')
            read.flag = 4 if outcome == 'unmapped' else 0
            read.reference_id = -1 if outcome == 'unmapped' else 0
            read.reference_start = -1 if outcome == 'unmapped' else i
            read.cigarstring = None if outcome == 'unmapped' else '4M'
            read.mapping_quality = 0 if outcome == 'filtered' else quality
            if code:
                read.set_tag('MM', f'{base}+{code}?,0;')
                read.set_tag('ML', array.array('B', [230] * (len(code) if code.isalpha() else 1)))
                if mixed_legacy and i == 1:
                    read.set_tag('MM', None)
                    read.set_tag('ML', None)
                    read.set_tag('Mm', 'C+h?,0;')
                    read.set_tag('Ml', array.array('B', [230]))
            bam.write(read)
    selected = tmp_path / 'selected.fasta'
    selected.write_text('>ref\nACGTACGT\n')
    params = {'ont_workflow_id': 'ont_methylation_analysis', 'ont_input_mode': 'bam',
              'bam_path': str(source), 'bam_min_mapq': mapq, 'run_modkit': enabled,
              'modkit_filter_threshold': threshold}
    if reference:
        params.update(reference_fasta=str(selected), reference_sequence_sha256=hashlib.sha256(b'ACGTACGT').hexdigest())
    values = {'bam': source, 'bamMinMapq': mapq, 'declaredSourceSha256': '', 'task.cpus': 1}
    prepared = shell('PrepareBamForAnalysis', align, values, bins)
    if outcome != 'mapped': return prepared, root
    assert prepared.returncode == 0, prepared.stderr
    if reference:
        checked = tmp_path / 'mapped-check'
        checked.mkdir()
        if not m5:
            params.update(bam_source_sha256=_sha(source),
                          bam_reference_sha256=params['reference_sequence_sha256'])
        result = shell('ValidateMappedBam', checked, {**values, 'bam': align / 'aligned.bam',
            'bai': align / 'aligned.bam.bai', 'reference': selected,
            'declaredSourceSha256': params.get('bam_source_sha256', ''),
            'declaredReferenceSha256': params.get('bam_reference_sha256', '')}, bins)
        if not m5:
            return result, root
        assert result.returncode == 0, result.stderr
        result = shell('PrepareReferenceForIGV', align, {'reference': selected}, bins)
        assert result.returncode == 0, result.stderr
    names = ['aligned.bam', 'aligned.bam.bai', 'bam_prepare.log']
    if reference: names += ['reference.fasta', 'reference.fasta.fai']
    stages = {'bam_prepare': {'status': 'complete', 'outputs': [str(align / n) for n in names]}}
    if enabled:
        output_code = code if code.isdigit() else code[:1]
        results = emit_modkit(root, threshold,
            bed=BED.replace('\tm\t', '\t' + output_code + '\t') if bed is None else bed,
            summary=SUMMARY.replace('modified_m', 'modified_' + output_code) if summary is None else summary)
        assert len(results) == 3 and all(r.returncode == 0 for r in results), [r.stderr for r in results]
        for stage, names in {'modkit_pileup': ['modified_base_input.bam', 'modified_base_input.bam.bai',
                'modified_base_tag_check.log', 'methylation.bed', 'pileup.log'],
                'modkit_summary': ['modkit_summary.tsv', 'summary.log']}.items():
            stages[stage] = {'status': 'complete', 'outputs': [str(root / 'methylation' / n) for n in names]}
    job = SimpleNamespace(id='external-job', model_id='nanopore', mode='methylation_analysis', params=params,
        output_dir=str(root), child_output_dir=None, status='running', queue_status='running',
        provenance={'stage_terminal_states': stages})
    return job, root


@pytest.mark.parametrize('enabled,reference', [(True, True), (False, True), (False, False)])
def test_external_native_vertical(tmp_path, enabled, reference):
    job, root = external_fixture(tmp_path, enabled=enabled, reference=reference)
    before = {str(p): _sha(p) for p in root.rglob('*') if p.is_file()}
    assert asyncio.run(_nextflow_native_entry(job)) is True
    result = job.provenance['result_integrity']
    assert result['input_mode'] == 'bam'
    assert result['alignment_state'] == 'validated'
    assert result['methylation']['state'] == ('validated' if enabled else 'not_requested')
    assert result['alignment']['reference_identity'] == ('bam_sq_m5' if reference else 'not_requested')
    assert not any(key in result for key in ('preflight_sha256', 'runtime_provenance_sha256', 'modified_bases'))
    assert before == {str(p): _sha(p) for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('base,summary_base,code', [('U', 'T', 'm'), ('N', 'A', 'm'), ('C', 'C', '076792')])
def test_external_modkit_base_and_numeric_projection(tmp_path, base, summary_base, code):
    output_code = str(int(code)) if code.isdigit() else code
    job, root = external_fixture(tmp_path, base=base, code=code,
        bed=BED.replace('\tm\t', '\t' + output_code + '\t'),
        summary=SUMMARY.replace('C', summary_base).replace('modified_m', 'modified_' + output_code))
    assert asyncio.run(_nextflow_native_entry(job)) is True


def test_external_mixed_legacy_tags_follow_modkit_not_dorado(tmp_path):
    job, root = external_fixture(tmp_path, mixed_legacy=True,
        bed=BED.replace('\tm\t', '\th\t'), summary=SUMMARY.replace('modified_m', 'modified_h'))
    assert asyncio.run(_nextflow_native_entry(job)) is True


@pytest.mark.parametrize('code', ['m', 'h', 'mh', '76792'])
@pytest.mark.parametrize('threshold', [0, 0.5, 1, None])
def test_external_codes_and_threshold_not_dorado_models(tmp_path, code, threshold):
    job, root = external_fixture(tmp_path, code=code, threshold=threshold, mapq=10)
    del job.params['run_modkit']
    assert asyncio.run(_nextflow_native_entry(job)) is True
    result = job.provenance['result_integrity']
    assert result['alignment']['record_count'] == 1
    assert result['methylation']['filter_threshold'] == threshold


@pytest.mark.parametrize('enabled', [True, False])
@pytest.mark.parametrize('filename', ['align/aligned.bam', 'align/aligned.bam.bai', 'align/bam_prepare.log',
    'align/reference.fasta', 'align/reference.fasta.fai', 'align/reference_prepare.log'])
@pytest.mark.parametrize('damage', ['missing', 'symlink'])
def test_external_native_required_products(tmp_path, enabled, filename, damage):
    job, root = external_fixture(tmp_path, enabled=enabled)
    path = root / filename
    original = tmp_path / 'original'
    original.write_bytes(path.read_bytes())
    path.unlink()
    if damage == 'symlink': path.symlink_to(original)
    before = copy.deepcopy(job.__dict__)
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))
    assert before == job.__dict__


@pytest.mark.parametrize('field', ['source_sha256_before', 'source_sha256_after', 'source_immutable',
    'bam_min_mapq', 'input_records', 'output_records', 'mapped_records', 'input_sort_order'])
@pytest.mark.parametrize('damage', ['bad', 'duplicate', 'missing'])
def test_external_preparation_receipt(tmp_path, field, damage):
    job, root = external_fixture(tmp_path, enabled=False)
    path = root / 'align/bam_prepare.log'
    lines = path.read_text().splitlines()
    line = next(line for line in lines if line.startswith(field + '='))
    if damage == 'bad': lines[lines.index(line)] = field + '=invalid'
    if damage == 'missing': lines.remove(line)
    if damage == 'duplicate': lines.append(line)
    path.write_text('\n'.join(lines) + '\n')
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))


@pytest.mark.parametrize('field', ['bam_source_sha256', 'bam_reference_sha256'])
@pytest.mark.parametrize('value', [False, 0, [], {}, 'wrong'])
def test_external_malformed_declared_authority_not_ignored(tmp_path, field, value):
    job, root = external_fixture(tmp_path, enabled=False)
    job.params[field] = value
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))


@pytest.mark.parametrize('damage', ['source', 'source_sha', 'reference', 'reference_sha', 'index', 'mapq',
    'source_symlink', 'missing_stage', 'failed_stage', 'outputs', 'dorado_stage', 'no_reference', 'threshold'])
def test_external_authority_fails_closed(tmp_path, damage):
    job, root = external_fixture(tmp_path)
    if damage == 'source': Path(job.params['bam_path']).write_bytes(b'changed')
    if damage == 'source_sha': job.params['bam_source_sha256'] = 'f' * 64
    if damage == 'reference': Path(job.params['reference_fasta']).write_text('>ref\nTTTTTTTT\n')
    if damage == 'reference_sha': job.params['reference_sequence_sha256'] = 'f' * 64
    if damage == 'index': (root / 'align/aligned.bam.bai').write_bytes(b'bad')
    if damage == 'mapq': job.params['bam_min_mapq'] = 61
    if damage == 'threshold': job.params['modkit_filter_threshold'] = 0.5
    if damage == 'no_reference': del job.params['reference_fasta']
    if damage == 'source_symlink':
        source = Path(job.params['bam_path'])
        renamed = source.with_suffix('.original')
        source.rename(renamed)
        source.symlink_to(renamed)
    stages = job.provenance['stage_terminal_states']
    if damage == 'missing_stage': del stages['bam_prepare']
    if damage == 'failed_stage': stages['bam_prepare']['status'] = 'failed'
    if damage == 'outputs': stages['bam_prepare']['outputs'].reverse()
    if damage == 'dorado_stage': stages['dorado_basecall'] = {'status': 'complete', 'outputs': []}
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))


@pytest.mark.parametrize('filename', ['modified_base_input.bam', 'modified_base_input.bam.bai', 'modified_base_tag_check.log',
    'methylation.bed', 'pileup.log', 'modkit_summary.tsv', 'summary.log'])
def test_external_enabled_requires_modkit_products(tmp_path, filename):
    job, root = external_fixture(tmp_path)
    (root / 'methylation' / filename).unlink()
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))


@pytest.mark.parametrize('stage', ['modkit_pileup', 'modkit_summary'])
def test_external_disabled_rejects_stages(tmp_path, stage):
    job, root = external_fixture(tmp_path, enabled=False)
    job.provenance['stage_terminal_states'][stage] = {'status': 'complete', 'outputs': []}
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))


@pytest.mark.parametrize('filename', ['modified_base_input.bam', 'modified_base_input.bam.bai', 'modified_base_tag_check.log',
    'methylation.bed', 'pileup.log', 'modkit_summary.tsv', 'summary.log'])
def test_external_disabled_rejects_stale_products(tmp_path, filename):
    job, root = external_fixture(tmp_path, enabled=False)
    (root / 'methylation').mkdir()
    (root / 'methylation' / filename).write_bytes(b'')
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))


@pytest.mark.parametrize('outcome', ['unmapped', 'filtered'])
def test_external_producer_zero_mapped_is_failure(tmp_path, outcome):
    process, root = external_fixture(tmp_path, outcome=outcome, mapq=10)
    assert process.returncode != 0
    assert 'no mapped reads' in process.stderr


def test_external_no_m5_source_sha_conflict_is_not_fabricated_success(tmp_path):
    process, root = external_fixture(tmp_path, m5=False)
    assert process.returncode != 0
    assert 'exact BAM object being validated' in process.stderr


@pytest.mark.parametrize('summary', ['mod_bases\t\ntotal_reads_used\t0\n', SUMMARY])
def test_external_no_calls_are_success(tmp_path, summary):
    job, root = external_fixture(tmp_path, bed='', summary=summary)
    assert asyncio.run(_nextflow_native_entry(job)) is True
    assert job.provenance['result_integrity']['methylation']['bed_rows'] == 0


@pytest.mark.parametrize('enabled', [True, False])
@pytest.mark.asyncio
async def test_external_cas_loser(tmp_path, monkeypatch, enabled):
    import test_ont_ngs_native_completion as native_tests
    job, root = external_fixture(tmp_path, enabled=enabled)
    before = {str(p): _sha(p) for p in root.rglob('*') if p.is_file()}
    monkeypatch.setattr(native_tests, '_fixture', lambda unused: (job, root))
    await native_tests.test_native_completion_losing_cas_publishes_no_result(tmp_path, None)
    assert before == {str(p): _sha(p) for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage', ['contig', 'coordinate', 'width', 'fraction', 'coverage', 'negative', 'duplicate', 'code', 'truncated'])
def test_external_rehashed_bed(tmp_path, monkeypatch, damage):
    import test_ont_ngs_native_methylation_completion as shared
    monkeypatch.setattr(shared, 'methylation_fixture', external_fixture)
    shared.test_rehashed_modkit_bed_semantics(tmp_path, damage)


@pytest.mark.parametrize('damage', ['reads', 'base_reads', 'fraction', 'total', 'negative', 'duplicate', 'code', 'unknown', 'truncated', 'foreign_base'])
def test_external_rehashed_summary(tmp_path, monkeypatch, damage):
    import test_ont_ngs_native_methylation_completion as shared
    # The shared oracle changes total_reads_used to 2; filter the external
    # two-read source to one record so that value is genuinely impossible.
    monkeypatch.setattr(shared, 'methylation_fixture', lambda path, **kwargs: external_fixture(path, mapq=10, **kwargs))
    shared.test_rehashed_modkit_summary_semantics(tmp_path, damage)


@pytest.mark.parametrize('log,key', [('pileup.log', 'bms_input_sha256'), ('pileup.log', 'bms_index_sha256'),
    ('pileup.log', 'bms_reference_sha256'), ('pileup.log', 'bms_output_sha256'), ('pileup.log', 'bms_modkit_version'),
    ('pileup.log', 'bms_filter_args'), ('summary.log', 'bms_input_sha256'), ('summary.log', 'bms_index_sha256'),
    ('summary.log', 'bms_modkit_version'), ('summary.log', 'bms_output_sha256')])
@pytest.mark.parametrize('damage', ['changed', 'duplicate', 'missing'])
def test_external_modkit_execution_receipt(tmp_path, monkeypatch, log, key, damage):
    import test_ont_ngs_native_methylation_completion as shared
    monkeypatch.setattr(shared, 'methylation_fixture', external_fixture)
    shared.test_modkit_execution_authority(tmp_path, log, key, damage)


@pytest.mark.parametrize('stage', ['modkit_pileup', 'modkit_summary'])
@pytest.mark.parametrize('damage', ['missing', 'failed', 'outputs'])
def test_external_modkit_stage_contract(tmp_path, monkeypatch, stage, damage):
    import test_ont_ngs_native_methylation_completion as shared
    monkeypatch.setattr(shared, 'methylation_fixture', external_fixture)
    shared.test_modkit_stage_contract(tmp_path, stage, damage)


@pytest.mark.parametrize('reference', [True, False])
def test_external_disabled_untagged_needs_no_modification_model(tmp_path, reference):
    job, root = external_fixture(tmp_path, enabled=False, reference=reference, code='')
    assert asyncio.run(_nextflow_native_entry(job)) is True
    assert job.provenance['result_integrity']['methylation'] == {'state': 'not_requested'}


@pytest.mark.parametrize('damage', ['sequence', 'flag', 'quality', 'cigar', 'MM', 'ML', 'missing_record'])
def test_external_rehashed_prepared_inventory(tmp_path, damage):
    job, root = external_fixture(tmp_path, enabled=False)
    path = root / 'align/aligned.bam'
    with pysam.AlignmentFile(path, 'rb') as bam:
        header = bam.header
        reads = list(bam)
    if damage == 'sequence': reads[0].query_sequence = 'TTTT'
    if damage == 'flag': reads[0].flag = 16
    if damage == 'quality': reads[0].mapping_quality = 40
    if damage == 'cigar': reads[0].cigarstring = '3M1S'
    if damage == 'MM': reads[0].set_tag('MM', 'C+h?,0;')
    if damage == 'ML': reads[0].set_tag('ML', array.array('B', [1]))
    if damage == 'missing_record': reads.pop()
    with pysam.AlignmentFile(path, 'wb', header=header) as bam:
        for read in reads: bam.write(read)
    pysam.index(str(path))
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))


@pytest.mark.parametrize('filename', ['source', 'reference', 'align/aligned.bam', 'align/reference.fasta'])
def test_external_final_identity_recheck(tmp_path, monkeypatch, filename):
    from services import ont_ngs_native_methylation as native
    job, root = external_fixture(tmp_path)
    original = native._validate_modkit
    def changed(*args):
        result = original(*args)
        path = Path(job.params['bam_path']) if filename == 'source' else Path(job.params['reference_fasta']) if filename == 'reference' else root / filename
        path.write_bytes(b'changed after validation')
        return result
    monkeypatch.setattr(native, '_validate_modkit', changed)
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))
