"""Bounded real ONT CPU/Nextflow controls. No service, GPU or worker launch.

The selected released image and cached engine are explicit opt-ins. Synthetic
known-truth sequences test plumbing, not scientific accuracy on real samples.
Only stage notifications are inert; no scientific tool/output is substituted.
"""
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def native_cpu(tmp_path):
    selected = {k: os.environ.get(k) for k in (
        'BMS_TEST_ONT_NATIVE_IMAGE', 'BMS_TEST_NEXTFLOW_LAUNCHER', 'BMS_TEST_NEXTFLOW_JAR')}
    if not all(selected.values()):
        pytest.skip('requires explicitly selected released ONT image and offline Nextflow')
    selected = {key: str(value) for key, value in selected.items()}
    image = selected['BMS_TEST_ONT_NATIVE_IMAGE']
    jar = Path(selected['BMS_TEST_NEXTFLOW_JAR'])
    home = tmp_path / 'nxf-home'
    framework = home / 'framework' / jar.parent.name
    framework.mkdir(parents=True)
    (framework / jar.name).symlink_to(jar)
    env = dict(os.environ, NXF_HOME=str(home), NXF_VER=jar.parent.name,
               NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true', NXF_ANSI_LOG='false')
    code = tmp_path / 'code'
    scripts = code / 'scripts'
    scripts.mkdir(parents=True)
    (code / 'config').symlink_to(ROOT / 'config', target_is_directory=True)
    for source in (ROOT / 'scripts').iterdir():
        if source.name != 'stage_reporter.py':
            (scripts / source.name).symlink_to(source, target_is_directory=source.is_dir())
    (scripts / 'stage_reporter.py').write_text('import sys\nprint("offline stage notification", sys.argv[1:])\n')
    config = tmp_path / 'native.config'
    config.write_text(f"""process.executor='local'
process.container='{image}'
process.cpus=2
process.memory='2 GB'
process.errorStrategy='terminate'
process.shell=['/bin/bash','-ue']
apptainer.enabled=true
apptainer.autoMounts=true
apptainer.runOptions='--bind {ROOT}:{ROOT}:ro,{tmp_path}:{tmp_path}'
singularity.enabled=false
docker.enabled=false
""")
    def tool(*args):
        return subprocess.run(['apptainer', 'exec', '--bind', f'{tmp_path}:{tmp_path}', image,
                               *map(str, args)], capture_output=True, text=True, check=True)
    def run(workflow, params):
        params = dict(code_root=str(code), out_dir=str(tmp_path / 'published'),
                      job_id='native-fixture', **params)
        path = tmp_path / 'params.json'; path.write_text(json.dumps(params))
        result = subprocess.run([selected['BMS_TEST_NEXTFLOW_LAUNCHER'], '-C', str(config),
            'run', str(workflow), '-params-file', str(path), '-w', str(tmp_path / 'work'),
            '-with-trace', str(tmp_path / 'trace.tsv')], cwd=tmp_path, env=env,
            capture_output=True, text=True, timeout=240)
        (tmp_path / 'nextflow.log').write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stdout + result.stderr
        return tmp_path / 'published'
    return run, tool


def truth_inputs(tmp_path):
    sequence = ''.join(random.Random(231).choices('ACGT', k=1200))
    reference = tmp_path / 'truth.fasta'; reference.write_text('>truth\n' + sequence + '\n')
    fastq = tmp_path / 'reads.fastq'
    fastq.write_text(''.join(f'@r{i}\n{sequence}\n+\n' + 'I'*len(sequence) + '\n' for i in range(30)))
    return sequence, reference, fastq


@pytest.mark.parametrize('workflow,mode,qc', [
    ('ont_fastq_qc', 'fastq', True), ('ont_fastq_qc', 'fastq', False),
    ('ont_plasmid_qc', 'fastq', True), ('ont_plasmid_qc', 'bam', True),
    ('ont_construct_screening', 'fastq', True), ('ont_construct_screening', 'bam', False),
])
def test_native_qc_and_screening(tmp_path, native_cpu, workflow, mode, qc):
    run, tool = native_cpu
    sequence, reference, fastq = truth_inputs(tmp_path)
    params = dict(reference_fasta=str(reference), reference_sequence_sha256=hashlib.sha256(sequence.encode()).hexdigest(),
                  workflow_id=workflow, input_mode=mode, run_fastq_qc=qc, run_assembly=False)
    if mode == 'bam':
        sam = tmp_path / 'input.sam'
        sam.write_text('@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:truth\tLN:1200\tM5:' + hashlib.md5(sequence.encode()).hexdigest() + '\n' + ''.join(
            f'r{i}\t0\ttruth\t1\t60\t1200M\t*\t0\t0\t{sequence}\t' + 'I'*1200 + '\n' for i in range(30)))
        bam = tmp_path / 'input.bam'; tool('samtools', 'view', '-b', '-o', bam, sam)
        params['bam_path'] = str(bam)
    else:
        params['fastq_path'] = str(fastq)
    output = run(ROOT / f'workflows/ngs/{workflow}.nf', params)
    assert (output / 'align/aligned.bam').is_file()
    trace = (tmp_path / 'trace.tsv').read_text()
    assert ('FastqPlasmidQC' in trace or 'PlasmidQC' in trace) == qc
    if qc:
        consensus = ''.join(l for l in (output / 'fastq_qc/fastq_consensus.fasta').read_text().splitlines() if not l.startswith('>'))
        assert consensus == sequence
        assert (output / 'verification/qc_manifest.json').is_file()
        assert (output / 'fastq_qc/igv_report.html').stat().st_size > 0
        stats = dict(line.split('\t') for line in (output / 'fastq_qc/fastq_alignment_stats.tsv').read_text().splitlines()[1:])
        assert stats['mapped_reads'] == stats['logical_read_records'] == '30'
        assert stats['secondary_alignments'] == stats['supplementary_alignments'] == '0'


@pytest.mark.parametrize('bad_identity', [None, 'source', 'reference'])
def test_native_prepared_bam_authenticates_source_not_transformed_bytes(tmp_path, native_cpu, bad_identity):
    run, tool = native_cpu
    sequence, reference, _ = truth_inputs(tmp_path)
    sam = tmp_path / 'source.sam'
    sam.write_text('@HD\tVN:1.6\tSO:unsorted\n@SQ\tSN:truth\tLN:1200\n' +
        f'r1\t0\ttruth\t1\t60\t1200M\t*\t0\t0\t{sequence}\t' + 'I'*1200 + '\n')
    bam = tmp_path / 'source.bam'; tool('samtools', 'view', '-b', '-o', bam, sam)
    original = hashlib.sha256(bam.read_bytes()).hexdigest()
    harness = tmp_path / 'main.nf'
    harness.write_text(f"include {{ PrepareBamForAnalysis; ValidateMappedBam }} from '{ROOT}/modules/ngs/bam_prepare.nf'\nworkflow {{ PrepareBamForAnalysis(file(params.bam)); ValidateMappedBam(PrepareBamForAnalysis.out.aligned,file(params.reference)) }}\n")
    params = dict(bam=str(bam), reference=str(reference), bam_source_sha256='0'*64 if bad_identity == 'source' else original,
                  bam_reference_sha256='0'*64 if bad_identity == 'reference' else hashlib.sha256(sequence.encode()).hexdigest())
    if bad_identity:
        message = 'snapshot does not match authorized bam_source_sha256' if bad_identity == 'source' else 'bam_reference_sha256 does not match'
        with pytest.raises(AssertionError, match=message):
            run(harness, params)
    else:
        output = run(harness, params)
        assert hashlib.sha256((output / 'align/aligned.bam').read_bytes()).hexdigest() != original
        checks = list((tmp_path / 'work').glob('*/*/bam_mapped_check.log'))
        assert len(checks) == 1
        assert 'authenticated_source_bam_sha256=' + original in checks[0].read_text()


@pytest.mark.parametrize('modification,enabled', [('5mC_5hmC', True), ('6mA', True), ('5mC_5hmC', False)])
def test_native_modkit_on_retained_dorado_calls(tmp_path, native_cpu, modification, enabled):
    run, tool = native_cpu
    retained = os.environ.get('BMS_TEST_ONT_RETAINED_CONTROLS')
    if not retained:
        pytest.skip('requires retained genuine Dorado modified-base CPU outputs')
    source = tmp_path / 'calls.bam'
    source.write_bytes((Path(retained) / f'modified-{modification}.bam').read_bytes())
    record = tool('samtools', 'view', source).stdout.splitlines()[0].split('\t')
    sequence = record[9]
    reference = tmp_path / 'truth.fasta'; reference.write_text('>truth\n' + sequence + '\n')
    bam = tmp_path / 'aligned.bam'
    tool('bash', '-c', 'set -euo pipefail; dorado aligner "$1" "$2" --threads 2 | samtools sort -o "$3"', 'bash', reference, source, bam)
    # Preserve native read groups and MM/ML tags. Establish the real reference
    # identity with samtools' computed SQ M5; this is not inferred read origin.
    header = tool('samtools', 'view', '-H', bam).stdout
    sq = '\n'.join(l for l in tool('samtools', 'dict', reference).stdout.splitlines() if l.startswith('@SQ'))
    header_path = tmp_path / 'header.sam'
    header_path.write_text('\n'.join(sq if l.startswith('@SQ') else l for l in header.splitlines()) + '\n')
    tagged = tmp_path / 'tagged.bam'
    tool('bash', '-c', 'samtools reheader "$1" "$2" > "$3"', 'bash', header_path, bam, tagged)
    output = run(ROOT / 'workflows/ngs/ont_methylation_analysis.nf', dict(
        bam_path=str(tagged), reference_fasta=str(reference), run_modkit=enabled, modkit_filter_threshold=0,
        workflow_id='ont_methylation_analysis', input_mode='bam'))
    assert (output / 'align/aligned.bam').is_file()
    assert (output / 'methylation/methylation.bed').is_file() == enabled
    if enabled:
        lines = (output / 'methylation/methylation.bed').read_text().splitlines()
        assert lines and any(int(line.split('\t')[9]) > 0 for line in lines)
        assert (output / 'methylation/modkit_summary.tsv').stat().st_size > 0
    else:
        assert 'Modkit' not in (tmp_path / 'trace.tsv').read_text()


def test_native_demux_preclassified_units(tmp_path, native_cpu):
    run, tool = native_cpu
    sam = tmp_path / 'classified.sam'
    sam.write_text('@HD\tVN:1.6\tSO:unknown\n@SQ\tSN:truth\tLN:4\n@RG\tID:run_model_SQK-RBK114-96_barcode01\tPU:run\tDS:runid=run basecall_model=model\tSM:barcode01\n@RG\tID:run_model_unclassified\tPU:run\tDS:runid=run basecall_model=model\tSM:unclassified\n' +
        'classified\t4\t*\t0\t0\t*\t*\t0\t0\tACGT\tIIII\tRG:Z:run_model_SQK-RBK114-96_barcode01\tBC:Z:SQK-RBK114-96_barcode01\n' +
        'unknown\t4\t*\t0\t0\t*\t*\t0\t0\tTGCA\tIIII\tRG:Z:run_model_unclassified\tBC:Z:unclassified\n')
    bam = tmp_path / 'classified.bam'; tool('samtools', 'view', '-b', '-o', bam, sam)
    preflight = tmp_path / 'dorado_preflight.json'
    preflight.write_text(json.dumps(dict(schema='biomodstack.dorado_preflight.v1', selection=dict(mode='simplex'), barcoding=dict(kit='SQK-RBK114-96'))))
    harness = tmp_path / 'main.nf'
    harness.write_text(f"include {{ DoradoDemux }} from '{ROOT}/modules/ngs/dorado_basecall.nf'\nworkflow {{ DoradoDemux(file(params.bam),file(params.preflight)) }}\n")
    output = run(harness, dict(bam=str(bam), preflight=str(preflight)))
    manifest = json.loads((output / 'demux/demux_manifest.json').read_text())
    assert manifest['total_reads'] == 2
    assert {u['unit_id']: u['read_count'] for u in manifest['units']} == {'barcode01': 1, 'unclassified': 1}


def test_native_comparison_panel_zero_thresholds(tmp_path, native_cpu):
    run, _ = native_cpu
    sequence, reference, fastq = truth_inputs(tmp_path)
    panel_dir = tmp_path / 'panel'; panel_dir.mkdir()
    decoy = panel_dir / 'decoy.fasta'; decoy.write_text('>decoy\n' + ''.join(random.Random(987).choices('ACGT', k=1200)) + '\n')
    snapshot = panel_dir / 'snapshot.json'
    snapshot.write_text(json.dumps(dict(schema='bms.ngs.comparison-panel.v1', entries=[dict(id='decoy',role='plasmid_decoy',label='Decoy',fasta_path=decoy.name,fasta_sha256=hashlib.sha256(decoy.read_bytes()).hexdigest())])))
    harness = tmp_path / 'main.nf'
    harness.write_text(f"include {{ ComparisonPanelAttribution }} from '{ROOT}/modules/ngs/comparison_panel_attribution.nf'\nworkflow {{ ComparisonPanelAttribution(file(params.fastq),file(params.reference),file(params.snapshot)) }}\n")
    output = run(harness, dict(fastq=str(fastq), reference=str(reference),snapshot=str(snapshot),comparison_panel_min_mapq=0,comparison_panel_min_score_margin=0))
    summary = json.loads((output / 'comparison_panel/comparison_panel_summary.json').read_text())
    assert summary['min_mapq'] == summary['min_score_margin'] == 0
    assert (output / 'comparison_panel/comparison_panel.bam').is_file()
