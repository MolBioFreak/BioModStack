"""Execute the actual module's summary decision/emission/receipt shell, no GPU.

Pinned Dorado 1.3.1 (7c84b01de1e46d4c5b2d5208fc430f27579a6c22):
basecall_output_args.cpp:42-50 permits --emit-summary without --output-dir.
SummaryFileWriter.cpp:23-43,138-193,214-254 specifies the 20-column TSV,
primary records only, l_qseq and qs (default zero). WriterNode fans the same
records to both writers. StreamHtsFileWriter lazily opens BAM on first record:
zero output is empty stdout and fails this module's samtools quickcheck.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
# Exact native header, no local summary schema invented.
HEADER = ('input_filename batch_id parent_read_id read_id run_id channel mux '
          'minknow_events start_time duration passes_filtering template_start '
          'num_events_template template_duration sequence_length_template '
          'mean_qscore_template pore_type experiment_id sample_id end_reason').split()


def summary_text(*, empty=False):
    # Native default tag values for the real minimal read-1 BAM fixture.
    row = ['unknown', '0', 'read-1', 'read-1', '', '0', '0', '0',
           '0.000000', '0.000000', 'TRUE', '0.000000', '0', '0.000000',
           '4', '0.000000', '', '', '', '']
    return '\t'.join(HEADER) + '\n' + ('' if empty else '\t'.join(row) + '\n')


def emit_receipt(base, *, requested=True, supported=True, help_failure=False, output='normal', mode='simplex', modified=False):
    """Extract unchanged production shell between command options and receipt.

    Only Dorado and samtools executables are replaced: Dorado copies fixtures;
    samtools delegates to pysam's real bundled samtools implementation.
    """
    text = (ROOT / 'modules/ngs/dorado_basecall.nf').read_text()
    start = text.index('    if [[', text.index('      command+=(--no-trim)'))
    if modified:
        start = text.index('    command=(dorado)')
    end = text.index('\n    """', start)
    script = text[start:end]
    script = re.sub(r'\$\{doradoShellQuote\([^\n]*?\)\}',
                    lambda m: shlex.quote(str(ROOT / 'scripts/dorado_supports_option.sh')), script)
    script = script.replace('${summaryRequested}', str(requested).lower()).replace('${movesRequested}', 'false')
    script = script.replace('\\$', '$')
    fixtures = base / 'fixture-binaries'
    fixtures.mkdir(exist_ok=True)
    (fixtures / 'input.bam').write_bytes((base / 'calls.bam').read_bytes())
    (fixtures / 'summary.txt').write_text(summary_text(empty=output in {'empty', 'zero_reads'}))
    fake = fixtures / 'dorado'
    fake.write_text('#!/usr/bin/env bash\nset -euo pipefail\n'
                    'if [[ "${2:-}" == --help ]]; then\n'
                    + ('exit 9\n' if help_failure else
                       "printf '%s\\n' '" + ('  --emit-summary  Emit summary' if supported else '  --emit-moves  Emit moves') + "'\nexit 0\n")
                    + 'fi\n'
                    + 'printf "%s\\n" "$@" > fixture-binaries/argv.txt\n'
                    + ('' if output == 'zero_reads' else 'cat fixture-binaries/input.bam\n')
                    + ('' if output == 'missing' else
                       'for arg in "$@"; do if [[ "$arg" == --emit-summary ]]; then cp fixture-binaries/summary.txt sequencing_summary.txt; fi; done\n'))
    fake.chmod(0o755)
    samtools = fixtures / 'samtools'
    samtools.write_text(f'#!{sys.executable}\nimport sys,pysam\n'
                        'try:\n result=getattr(pysam,sys.argv[1])(*sys.argv[2:])\n'
                        'except pysam.SamtoolsError as e:\n print(e,file=sys.stderr);sys.exit(1)\n'
                        'if result: sys.stdout.write(result)\n')
    samtools.chmod(0o755)
    runtime = json.loads((base / 'dorado_runtime_provenance.json').read_text())
    prefix = (f'set -euo pipefail\nmode={mode}\ncommand=(dorado {"duplex" if mode == "duplex" else "basecaller"} model pod5)\n'
              + 'model_id=' + shlex.quote(runtime['model_id']) + '\n'
              + 'runtime_observed=' + shlex.quote(runtime['runtime_sha256']) + '\n')
    pre = json.loads((base / 'dorado_preflight.json').read_text())
    prefix += 'mod_model_id=' + shlex.quote(pre['selection']['modified_bases_model_id'] or '') + '\n'
    if modified:
        prefix += ('base_model=model\npod5_root=pod5\nbatch_size=64\ndevice=cuda:0\n'
                   'min_qscore=10\npair_relative=\nbarcode_kit=\nsample_relative=\n'
                   'molecule=dna\ntrim_adapters=true\n')
    completed = subprocess.run(['bash', '-c', prefix + script], cwd=base,
                               env={**os.environ, 'PATH': str(fixtures) + ':' + os.environ['PATH']},
                               capture_output=True, text=True)
    return completed


@pytest.mark.parametrize('requested', [True, False])
@pytest.mark.parametrize('supported', [True, False])
def test_actual_emitter_records_summary_decision(tmp_path, requested, supported):
    from test_ont_ngs_native_completion import _fixture
    _, root = _fixture(tmp_path)
    base = root / 'basecall'
    result = emit_receipt(base, requested=requested, supported=supported)
    assert result.returncode == 0, result.stderr
    receipt = json.loads((base / 'dorado_runtime_provenance.json').read_text())
    evidence = receipt['summary']
    assert evidence['requested'] is requested
    assert evidence['executed'] is (requested and supported)
    if requested:
        assert evidence['capability']['supported'] is supported
        assert len(evidence['capability']['help_sha256']) == 64
    else:
        assert evidence['capability'] is None
    assert (evidence['output'] is not None) is (requested and supported)


def test_actual_emitter_does_not_call_failed_probe_unsupported(tmp_path):
    from test_ont_ngs_native_completion import _fixture
    _, root = _fixture(tmp_path)
    result = emit_receipt(root / 'basecall', help_failure=True)
    assert result.returncode != 0


def test_actual_emitter_rejects_missing_requested_output(tmp_path):
    from test_ont_ngs_native_completion import _fixture
    _, root = _fixture(tmp_path)
    result = emit_receipt(root / 'basecall', output='missing')
    assert result.returncode != 0


def test_actual_emitter_zero_reads_fail_native_quickcheck(tmp_path):
    from test_ont_ngs_native_completion import _fixture
    _, root = _fixture(tmp_path)
    base = root / 'basecall'
    result = emit_receipt(base, output='zero_reads')
    assert result.returncode != 0
    assert (base / 'calls.bam').read_bytes() == b''
    assert (base / 'sequencing_summary.txt').read_text() == summary_text(empty=True)


def test_actual_emitter_disabled_summary_never_probes(tmp_path):
    from test_ont_ngs_native_completion import _fixture
    _, root = _fixture(tmp_path)
    result = emit_receipt(root / 'basecall', requested=False, help_failure=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('name', ['sequencing_summary.txt', 'sequencing_summary.tsv'])
def test_actual_emitter_rejects_stale_summary(tmp_path, name):
    from test_ont_ngs_native_completion import _fixture
    _, root = _fixture(tmp_path)
    (root / 'basecall' / name).write_text(summary_text())
    result = emit_receipt(root / 'basecall')
    assert result.returncode != 0
    assert 'stale sequencing summary' in result.stderr
