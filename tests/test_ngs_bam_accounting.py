"""One-pass BAM accounting versus native independent samtools flag filters."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def accounting_block():
    source = (ROOT / 'modules/ngs/fastq_plasmid_qc.nf').read_text()
    block = source.split('    # Decode the BAM once', 1)[1].split('    primary_mapped_reads=', 1)[0]
    block = block[block.index('    "\\${SAMTOOLS_CMD[@]}"'):]
    # Native GString escapes; full Nextflow execution is independently covered.
    return block.replace('\\$', '$').replace('\\\\n', '\\n').replace('${bam}', 'input.bam')


@pytest.mark.parametrize('flags', [[], [0, 4, 256, 2048, 2304, 260, 2052, 2308, 512, 1024, 16], list(range(4096))])
def test_native_flag_accounting_matches_original_filters(tmp_path, flags):
    image = os.environ.get('BMS_TEST_ONT_NATIVE_IMAGE')
    if not image:
        pytest.skip('requires explicitly selected released ONT image')
    sam = tmp_path / 'input.sam'
    sam.write_text('@HD\tVN:1.6\n@SQ\tSN:ref\tLN:4\n' + ''.join(
        f'repeated\t{flag}\tref\t1\t60\t4M\t*\t0\t0\tACGT\tIIII\n' for flag in flags))
    cmd = ['apptainer', 'exec', '--bind', f'{tmp_path}:{tmp_path}', image]
    subprocess.run([*cmd, 'samtools', 'view', '-b', '-o', str(tmp_path / 'input.bam'), str(sam)], check=True)
    expected = []
    for filters in [('-F','4'),('-f','4'),('-F','2308'),('-f','4','-F','2304'),('-f','256'),('-f','2048')]:
        result = subprocess.run([*cmd, 'samtools', 'view', '-c', *filters, str(tmp_path / 'input.bam')], capture_output=True, text=True, check=True)
        expected.append(int(result.stdout))
    suffix = '\nprintf "%s\\n" "$mapped_alignment_records" "$unmapped_alignment_records" "$mapped_reads" "$unmapped_reads" "$secondary_alignments" "$supplementary_alignments"\n'
    script = 'set -euo pipefail\nSAMTOOLS_CMD=(samtools)\n' + accounting_block() + suffix
    result = subprocess.run([*cmd, 'bash', '-c', script], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert list(map(int, result.stdout.splitlines())) == expected
    assert not (tmp_path / 'bam.accounting.tmp').exists()


def test_counting_does_not_hide_decoder_failure(tmp_path):
    tool = tmp_path / 'samtools'
    tool.write_text('#!/bin/bash\nprintf "read\\t0\\tref\\t1\\t60\\t4M\\t*\\t0\\t0\\tACGT\\tIIII\\n"\nexit 42\n')
    tool.chmod(0o700)
    script = 'set -euo pipefail\nSAMTOOLS_CMD=("' + str(tool) + '")\n' + accounting_block() + '\nprintf should-not-complete\n'
    result = subprocess.run(['bash', '-c', script], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 42
    assert 'should-not-complete' not in result.stdout
