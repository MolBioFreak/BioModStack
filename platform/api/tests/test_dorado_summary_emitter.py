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

import pytest

from tests.ngs_producer_fixtures import HEADER, emit_receipt, summary_text


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
