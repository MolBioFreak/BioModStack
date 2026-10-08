"""Bounded POD5 methylation: actual producer shells, real BAM/index parsers.

Scientific executables are controlled fixture emitters, never modkit inference.
Source: modkit v0.6.4 cd85862f71d3bfc289f12adc1052a2e574c95e0f,
writers.rs:809-883 (key/value TSV), :266-318 (18-column bedMethyl).
"""
import asyncio
import copy
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest

from services import ont_ngs_completion as completion
from test_ont_ngs_native_completion import _sha, _nextflow_native_entry, isolated_result_root
from test_ont_ngs_native_modified_completion import modified_fixture, attach_modified_reference

ROOT = Path(__file__).resolve().parents[3]
BED = 'ref\t1\t2\tm\t1\t+\t1\t2\t255,0,0\t1\t100.00\t1\t0\t0\t0\t0\t0\t0\n'
SUMMARY = ('mod_bases\tC\ncount_reads_C\t1\nC_pass_calls_modified_m\t1\n'
           'C_pass_frac_modified_m\t1\nC_fail_calls_modified_m\t0\n'
           'C_total_mod_calls\t1\nC_total_fail_mod_calls\t0\ntotal_reads_used\t1\n')


def emit_modkit(root, threshold=0, *, bed=BED, summary=SUMMARY, fail=False):
    """Execute all three actual module shells with only modkit replaced."""
    out = root / 'methylation'
    out.mkdir(exist_ok=True)
    bins = root / 'modkit-fixture-binaries'
    bins.mkdir(exist_ok=True)
    (bins / 'bed').write_text(bed)
    (bins / 'summary').write_text(summary)
    (bins / 'modkit').write_text('#!/usr/bin/env bash\nset -euo pipefail\n'
        'if [[ "$1" == --version ]]; then printf "modkit 0.6.4\\n"; exit 0; fi\n'
        'printf "%s\\n" "$@" >> "$FIXTURES/argv"\n'
        + ('exit 7\n' if fail else '')
        + 'if [[ "$1" == pileup ]]; then cp "$FIXTURES/bed" "$3"; else cat "$FIXTURES/summary"; fi\n')
    (bins / 'modkit').chmod(0o755)
    (bins / 'samtools').write_text(f'#!{sys.executable}\nimport sys,pysam\n'
        'try:\n result=getattr(pysam,sys.argv[1])(*sys.argv[2:])\n'
        'except pysam.SamtoolsError as e:\n print(e,file=sys.stderr);sys.exit(1)\n'
        'if result: sys.stdout.write(result)\n')
    (bins / 'samtools').chmod(0o755)
    results = []
    for file, process in [('modkit_pileup.nf', 'ValidateModifiedBaseBam'),
                          ('modkit_pileup.nf', 'ModkitPileup'), ('modkit_summary.nf', 'ModkitSummary')]:
        text = (ROOT / 'modules/ngs' / file).read_text().split('process ' + process + ' {', 1)[1]
        script = text.split('"""', 1)[1].split('"""', 1)[0]
        bam = root / 'align/aligned.bam' if process == 'ValidateModifiedBaseBam' else out / 'modified_base_input.bam'
        for key, value in {'bam': str(bam), 'bai': str(bam) + '.bai',
                'reference': str(root / 'align/reference.fasta'), 'task.cpus': '1',
                'filterThreshold': '' if threshold is None else f'--filter-threshold {threshold}'}.items():
            script = script.replace('${' + key + '}', value)
        script = script.replace('\\$', '$').replace('\\\\\n', '\\\n')
        result = subprocess.run(['bash', '-c', script], cwd=out, capture_output=True, text=True,
            env={**os.environ, 'PATH': str(bins) + ':' + os.environ['PATH'], 'FIXTURES': str(bins)})
        results.append(result)
        if result.returncode:
            break
    return results


def methylation_fixture(tmp_path, *, threshold=0, bed=BED, summary=SUMMARY, modification='5mC_5hmC', requested=False, supported=True):
    job, root = modified_fixture(tmp_path, modification, requested=requested, supported=supported)
    attach_modified_reference(job, root, tmp_path)
    for key in ('ont_workflow_id', 'ont_request_workflow_id', 'workflow_id'):
        if key in job.params:
            job.params[key] = 'ont_methylation_analysis'
    job.params.update(ont_workflow_id='ont_methylation_analysis', run_modkit=True,
                      modkit_filter_threshold=threshold)
    results = emit_modkit(root, threshold, bed=bed, summary=summary)
    assert len(results) == 3 and all(r.returncode == 0 for r in results), [(r.returncode, r.stderr) for r in results]
    stages = job.provenance['stage_terminal_states']
    for stage, names in {'modkit_pileup': ['modified_base_input.bam', 'modified_base_input.bam.bai',
                         'modified_base_tag_check.log', 'methylation.bed', 'pileup.log'],
                         'modkit_summary': ['modkit_summary.tsv', 'summary.log']}.items():
        stages[stage] = {'status': 'complete', 'outputs': [str(root / 'methylation' / n) for n in names]}
    return job, root


@pytest.mark.parametrize('threshold', [0, 0.5, 1, None])
def test_modkit_producer_receipts_bind_actual_execution(tmp_path, threshold):
    job, root = methylation_fixture(tmp_path, threshold=threshold)
    for log, output in [('pileup.log', 'methylation.bed'), ('summary.log', 'modkit_summary.tsv')]:
        text = (root / 'methylation' / log).read_text()
        assert 'bms_modkit_version=modkit 0.6.4\n' in text
        assert f'bms_input_sha256={_sha(root / "methylation/modified_base_input.bam")}\n' in text
        assert f'bms_output_sha256={_sha(root / "methylation" / output)}\n' in text
    argv = (root / 'modkit-fixture-binaries/argv').read_text().splitlines()
    assert ('--filter-threshold' in argv) is (threshold is not None)
    if threshold is not None:
        assert argv[argv.index('--filter-threshold') + 1] == str(threshold)


def test_methylation_native_vertical_completion(tmp_path):
    job, root = methylation_fixture(tmp_path)
    before = {str(p): _sha(p) for p in root.rglob('*') if p.is_file()}
    assert asyncio.run(_nextflow_native_entry(job)) is True
    result = job.provenance['result_integrity']
    assert result['result_kind'] == 'ont_native_methylation'
    assert result['methylation']['state'] == 'validated'
    assert result['methylation']['bed_rows'] == 1
    assert result['methylation']['summary_reads_used'] == 1
    assert result['methylation']['filter_threshold'] == 0
    assert before == {str(p): _sha(p) for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('modification', ['none', '5mC_5hmC', '6mA'])
def test_disabled_modkit_native_does_not_claim_alignment_or_methylation(tmp_path, modification):
    job, root = disabled_fixture(tmp_path, modification)
    assert asyncio.run(_nextflow_native_entry(job)) is True
    result = job.provenance['result_integrity']
    assert result['methylation'] == {'state': 'not_requested'}
    assert result['alignment_state'] == 'not_executed'
    assert 'alignment' not in result
    assert {a['path'] for a in result['artifacts']} >= {'align/reference.fasta', 'align/reference.fasta.fai', 'align/reference_prepare.log'}


def disabled_fixture(tmp_path, modification='5mC_5hmC'):
    import hashlib
    import pysam
    from test_ont_ngs_native_completion import _fixture
    job, root = _fixture(tmp_path) if modification == 'none' else modified_fixture(tmp_path, modification)
    for key in ('ont_workflow_id', 'ont_request_workflow_id', 'workflow_id'):
        if key in job.params:
            job.params[key] = 'ont_methylation_analysis'
    align = root / 'align'
    align.mkdir()
    reference = tmp_path / 'selected.fasta'
    reference.write_text('>ref\nACGTACGT\n')
    (align / 'reference.fasta').write_bytes(reference.read_bytes())
    pysam.faidx(str(align / 'reference.fasta'))
    (align / 'reference_prepare.log').write_text('Prepared reference.fasta and reference.fasta.fai for IGV\n')
    job.params.update(run_modkit=False, reference_fasta=str(reference),
                      reference_sequence_sha256=hashlib.sha256(b'ACGTACGT').hexdigest())
    return job, root


@pytest.mark.parametrize('damage', ['bed', 'stage', 'alignment', 'reference', 'index', 'missing_log'])
def test_disabled_modkit_rejects_stale_or_invalid_products(tmp_path, damage):
    job, root = disabled_fixture(tmp_path)
    if damage == 'bed':
        (root / 'methylation').mkdir()
        (root / 'methylation/methylation.bed').write_text(BED)
    if damage == 'stage': job.provenance['stage_terminal_states']['modkit_summary'] = {'status': 'complete', 'outputs': []}
    if damage == 'alignment': job.provenance['stage_terminal_states']['dorado_align'] = {'status': 'complete', 'outputs': []}
    if damage == 'reference': (root / 'align/reference.fasta').write_text('>ref\nTTTTTTTT\n')
    if damage == 'index': (root / 'align/reference.fasta.fai').write_text('ref\t8\t0\t8\t9\n')
    if damage == 'missing_log': (root / 'align/reference_prepare.log').unlink()
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))


@pytest.mark.parametrize('filename', ['modified_base_input.bam', 'modified_base_input.bam.bai', 'modified_base_tag_check.log',
                                     'methylation.bed', 'pileup.log', 'modkit_summary.tsv', 'summary.log'])
@pytest.mark.parametrize('damage', ['missing', 'symlink'])
def test_modkit_requires_every_native_product(tmp_path, filename, damage):
    job, root = methylation_fixture(tmp_path)
    path = root / 'methylation' / filename
    original = tmp_path / 'original'
    original.write_bytes(path.read_bytes())
    path.unlink()
    if damage == 'symlink': path.symlink_to(original)
    before = copy.deepcopy(job.__dict__)
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))
    assert job.__dict__ == before


@pytest.mark.parametrize('stage', ['modkit_pileup', 'modkit_summary'])
@pytest.mark.parametrize('damage', ['missing', 'failed', 'outputs'])
def test_modkit_stage_contract(tmp_path, stage, damage):
    job, root = methylation_fixture(tmp_path)
    if damage == 'missing': del job.provenance['stage_terminal_states'][stage]
    if damage == 'failed': job.provenance['stage_terminal_states'][stage]['status'] = 'failed'
    if damage == 'outputs': job.provenance['stage_terminal_states'][stage]['outputs'].pop()
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))


@pytest.mark.parametrize('damage', ['contig', 'coordinate', 'width', 'fraction', 'coverage', 'negative', 'duplicate', 'code', 'truncated'])
def test_rehashed_modkit_bed_semantics(tmp_path, damage):
    fields = BED.strip().split('\t')
    if damage == 'contig': fields[0] = 'foreign'
    if damage == 'coordinate': fields[2] = '9'
    if damage == 'width': fields.pop()
    if damage == 'fraction': fields[10] = 'nan'
    if damage == 'coverage': fields[9] = '2'
    if damage == 'negative': fields[17] = '-1'
    if damage == 'code': fields[3] = 'z'
    text = '\t'.join(fields) + '\n'
    if damage == 'duplicate': text += text
    if damage == 'truncated': text = text[:-1]
    job, root = methylation_fixture(tmp_path, bed=text)
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))


@pytest.mark.parametrize('damage', ['reads', 'base_reads', 'fraction', 'total', 'negative', 'duplicate', 'code', 'unknown', 'truncated', 'foreign_base'])
def test_rehashed_modkit_summary_semantics(tmp_path, damage):
    text = SUMMARY
    if damage == 'reads': text = text.replace('total_reads_used\t1', 'total_reads_used\t2')
    if damage == 'base_reads': text = text.replace('count_reads_C\t1', 'count_reads_C\t2')
    if damage == 'fraction': text = text.replace('pass_frac_modified_m\t1', 'pass_frac_modified_m\t0.5')
    if damage == 'total': text = text.replace('total_mod_calls\t1', 'total_mod_calls\t2')
    if damage == 'negative': text = text.replace('fail_calls_modified_m\t0', 'fail_calls_modified_m\t-1')
    if damage == 'duplicate': text += 'total_reads_used\t1\n'
    if damage == 'code': text = text.replace('modified_m', 'modified_z')
    if damage == 'unknown': text += 'other\t1\n'
    if damage == 'truncated': text = text[:-1]
    if damage == 'foreign_base': text = text.replace('C', 'A')
    job, root = methylation_fixture(tmp_path, summary=text)
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))


@pytest.mark.parametrize('log,key', [('pileup.log', 'bms_input_sha256'), ('pileup.log', 'bms_index_sha256'),
    ('pileup.log', 'bms_reference_sha256'), ('pileup.log', 'bms_output_sha256'), ('pileup.log', 'bms_modkit_version'),
    ('pileup.log', 'bms_filter_args'), ('summary.log', 'bms_input_sha256'), ('summary.log', 'bms_output_sha256')])
@pytest.mark.parametrize('damage', ['changed', 'duplicate', 'missing'])
def test_modkit_execution_authority(tmp_path, log, key, damage):
    job, root = methylation_fixture(tmp_path)
    path = root / 'methylation' / log
    text = path.read_text()
    row = next(line for line in text.splitlines(keepends=True) if line.startswith(key + '='))
    if damage == 'changed': text = text.replace(row, key + '=wrong\n')
    if damage == 'duplicate': text += row
    if damage == 'missing': text = text.replace(row, '')
    path.write_text(text)
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))


@pytest.mark.parametrize('modification', ['5mC_5hmC', '6mA'])
@pytest.mark.parametrize('threshold', [0, 0.5, 1, None])
@pytest.mark.parametrize('empty_bed', [False, True])
def test_modkit_supported_models_thresholds_and_empty_pileup(tmp_path, modification, threshold, empty_bed):
    bed = '' if empty_bed else BED
    summary = SUMMARY
    if modification == '6mA':
        bed = bed.replace('\tm\t', '\ta\t')
        summary = summary.replace('C', 'A').replace('modified_m', 'modified_a')
    job, root = methylation_fixture(tmp_path, modification=modification, threshold=threshold, bed=bed, summary=summary)
    if threshold is None: del job.params['modkit_filter_threshold']
    assert asyncio.run(_nextflow_native_entry(job)) is True
    assert job.provenance['result_integrity']['methylation']['bed_rows'] == (0 if empty_bed else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [True, False])
async def test_methylation_cas_loser_has_no_publication(tmp_path, monkeypatch, enabled):
    import test_ont_ngs_native_completion as native_tests
    job, root = methylation_fixture(tmp_path) if enabled else disabled_fixture(tmp_path)
    before = {str(p): _sha(p) for p in root.rglob('*') if p.is_file()}
    monkeypatch.setattr(native_tests, '_fixture', lambda unused: (job, root))
    await native_tests.test_native_completion_losing_cas_publishes_no_result(tmp_path, None)
    assert before == {str(p): _sha(p) for p in root.rglob('*') if p.is_file()}


def test_failed_modkit_does_not_emit_success_receipt(tmp_path):
    job, root = modified_fixture(tmp_path)
    attach_modified_reference(job, root, tmp_path)
    results = emit_modkit(root, fail=True)
    assert results[-1].returncode != 0
    assert 'bms_output_sha256=' not in (root / 'methylation/pileup.log').read_text()


def test_modkit_empty_summary_is_valid_no_call_outcome(tmp_path):
    job, root = methylation_fixture(tmp_path, bed='', summary='mod_bases\t\ntotal_reads_used\t0\n')
    assert asyncio.run(_nextflow_native_entry(job)) is True
    assert job.provenance['result_integrity']['methylation']['summary_reads_used'] == 0


@pytest.mark.parametrize('filename', ['basecall/calls.bam', 'align/aligned.bam', 'align/reference.fasta'])
def test_methylation_rechecks_validated_predecessor_products(tmp_path, monkeypatch, filename):
    from services import ont_ngs_native_methylation as native
    job, root = methylation_fixture(tmp_path)
    original = native._validate_modkit
    def changed(*args):
        result = original(*args)
        (root / filename).write_bytes(b'changed after predecessor validation')
        return result
    monkeypatch.setattr(native, '_validate_modkit', changed)
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))


@pytest.mark.parametrize('outcome', ['unmapped', 'filtered'])
def test_modkit_producer_rejects_zero_mapping_per_existing_stage(tmp_path, outcome):
    job, root = modified_fixture(tmp_path)
    attach_modified_reference(job, root, tmp_path, outcome)
    result = emit_modkit(root)
    assert len(result) == 1 and result[0].returncode != 0
    assert 'no mapped reads' in result[0].stderr


@pytest.mark.parametrize('damage', ['tag_count', 'copied_bam', 'copied_index', 'threshold'])
def test_modkit_input_and_selected_settings_fail_closed(tmp_path, damage):
    job, root = methylation_fixture(tmp_path)
    if damage == 'tag_count':
        (root / 'methylation/modified_base_tag_check.log').write_text('total_records=1\nmapped_records=1\nmodified_base_tagged_records=0\n')
    if damage == 'copied_bam': (root / 'methylation/modified_base_input.bam').write_bytes(b'bad')
    if damage == 'copied_index': (root / 'methylation/modified_base_input.bam.bai').write_bytes(b'bad')
    if damage == 'threshold': job.params['modkit_filter_threshold'] = 0.5
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job))


@pytest.mark.parametrize('requested', [False, True, 'default'])
@pytest.mark.parametrize('supported', [False, True])
@pytest.mark.parametrize('modification', ['5mC_5hmC', '6mA'])
def test_methylation_native_basecall_summary_conditionality(tmp_path, requested, supported, modification):
    bed = BED if modification == '5mC_5hmC' else BED.replace('\tm\t', '\ta\t')
    summary = SUMMARY if modification == '5mC_5hmC' else SUMMARY.replace('C', 'A').replace('modified_m', 'modified_a')
    job, root = methylation_fixture(tmp_path, modification=modification, bed=bed, summary=summary, requested=requested, supported=supported)
    del job.params['run_modkit']  # actual workflow omission is enabled, not false
    assert asyncio.run(_nextflow_native_entry(job)) is True
    result = job.provenance['result_integrity']
    assert result['summary_state'] == ('not_requested' if requested is False else 'validated' if supported else 'unsupported')
    assert result['methylation']['state'] == 'validated'


@pytest.mark.parametrize('change', [{'ont_input_mode': 'bam', 'input_mode': 'bam'}, {'dorado_basecall_mode': 'duplex'},
                                  {'modified_bases': 'none'}, {'dorado_quality_mode': 'sup'}, {'reference_fasta': None}])
def test_uncovered_methylation_branches_not_claimed_native(tmp_path, change):
    job, root = methylation_fixture(tmp_path)
    job.params.update(change)
    assert completion.ont_completion_lane(job) is None
