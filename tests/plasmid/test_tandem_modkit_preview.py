"""Compile the real plasmid graphs; no native processing or inference is launched."""
from pathlib import Path
import os
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('workflow', ['wf_clone_validation', 'ont_construct_screening', 'ont_plasmid_qc'])
@pytest.mark.parametrize('mode', ['pod5', 'bam', 'fastq'])
def test_plasmid_modkit_graph_and_nonblocking_fastq_default(tmp_path, workflow, mode):
    launcher = Path(os.environ.get('BMS_NEXTFLOW_BIN', '/usr/local/bin/nextflow'))
    if not launcher.is_file():
        pytest.skip('installed Nextflow unavailable; no graph qualification')
    reads = tmp_path / ('pod5' if mode == 'pod5' else f'reads.{mode}')
    if mode == 'pod5':
        reads.mkdir()
    else:
        reads.write_bytes(b'')  # Graph-only fixture, not scientific input.
    reference = tmp_path / 'reference.fa'
    reference.write_text('>reference\nACGT\n')
    log = tmp_path / 'nextflow.log'
    # Deliberately omit run_modkit for FASTQ: nextflow.config defaults it to true.
    # The workflow must skip modkit rather than fail graph compilation.
    options = [] if mode == 'fastq' else ['--run_modkit', 'true']
    if mode == 'fastq' and workflow == 'ont_plasmid_qc':
        # Existing plasmid-QC input policy requires explicit omission for FASTQ;
        # the newly ported clone/screening stages instead skip nonblockingly.
        options = ['--run_modkit', 'false']
    result = subprocess.run([
        str(launcher), '-c', str(ROOT / 'nextflow.config'), '-log', str(log), 'run', str(ROOT / f'workflows/ngs/{workflow}.nf'),
        '-preview', '-offline', '-w', str(tmp_path / 'work'), '--code_root', str(ROOT),
        '--out_dir', str(tmp_path / 'out'),
        '--' + {'pod5': 'pod5_dir', 'bam': 'bam_path', 'fastq': 'fastq_path'}[mode], str(reads),
        '--reference_fasta', str(reference), '-ansi-log', 'false', *options,
    ], cwd=tmp_path, env=dict(os.environ, NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true'),
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    text = log.read_text()
    # Nextflow preview leaves workflow.success unset in these two existing
    # onComplete handlers. Keep that diagnostic visible without confusing an
    # unexecuted completion callback with graph-construction failure.
    errors = [line.split(' ERROR ', 1)[1] for line in text.splitlines() if ' ERROR ' in line]
    expected_errors = [] if workflow == 'ont_plasmid_qc' else [
        'nextflow.script.WorkflowMetadata - Failed to invoke `workflow.onComplete` event handler'
    ]
    assert errors == expected_errors, text
    for process in ['ModkitAnalysis:ModkitPileup', 'ModkitAnalysis:ModkitSummary']:
        assert (process in text) is (mode != 'fastq'), text
