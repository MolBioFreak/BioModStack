import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from typing import Any

import pytest
from services import ont_submission_trust as custody

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('retained,readonly,shared', [(True, True, True), (False, True, False), (True, False, False)])
def test_exact_snapshot_shell_preserves_legacy_fallback(tmp_path, retained, readonly, shared):
    source = tmp_path / 'source with spaces.bam'
    source.write_bytes(b'byte transport control')
    if readonly:
        source.chmod(0o400)
    target = tmp_path / 'source.snapshot.bam'
    subprocess.run(['bash', '-euc', 'source "$1"; ngs_input_snapshot "$2" "$3" "$4"', 'test',
                    str(REPO / 'scripts/ngs_input_snapshot.sh'), str(source), str(target), str(retained).lower()], check=True)
    assert (source.stat().st_ino == target.stat().st_ino) is shared
    assert target.read_bytes() == b'byte transport control'
    if shared:
        assert not target.is_symlink()
        source.unlink()
        source.write_bytes(b'replaced retained name')
        assert target.read_bytes() == b'byte transport control'
    else:
        source.chmod(0o600)
        source.write_bytes(b'changed')
        assert target.read_bytes() == b'byte transport control'


def test_completed_move_bam_launches_share_body_but_own_names(tmp_path, monkeypatch):
    monkeypatch.setattr(custody, 'get_inputs_dir', lambda: tmp_path / 'inputs')
    source = tmp_path / 'filtered_moves.bam'
    payload = b'move transport control'
    source.write_bytes(payload)
    source.chmod(0o400)
    args: dict[str, Any] = dict(source_root=tmp_path, family='ont_external_move_launch_snapshots', authority_id='receipt',
                source_retained=True, expected_sha256=hashlib.sha256(payload).hexdigest(),
                expected_size_bytes=len(payload), suffix='.bam')
    first = custody.publish_immutable_launch_snapshot(source, **args)
    second = custody.publish_immutable_launch_snapshot(source, **args)
    assert first != second
    assert first.stat().st_ino == second.stat().st_ino == source.stat().st_ino
    custody.discard_unclaimed_launch_snapshot(first, snapshot_root=tmp_path / 'inputs',
        expected_sha256=args['expected_sha256'], expected_size_bytes=len(payload))
    source.unlink()
    assert second.read_bytes() == payload


@pytest.mark.parametrize('retained', [False, True])
def test_construct_bundle_actual_receiving_names_and_bytes(tmp_path, retained):
    spec = importlib.util.spec_from_file_location('ngs_construct_bundle', REPO / 'scripts/build_construct_verification_input.py')
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    fasta = tmp_path / 'native_consensus.fasta'
    fasta.write_bytes(b'>ref\nACGT\n')
    reads = tmp_path / 'source.fastq'
    reads.write_bytes(b'@read\nACGT\n+\nIIII\n')
    if retained:
        reads.chmod(0o400)
        fasta.chmod(0o400)
    out = tmp_path / 'construct_verification_input'
    args = ['--reference-fasta', str(fasta), '--source-reads', str(reads), '--consensus-fasta', str(fasta),
            '--consensus-method', 'samtools_consensus', '--out-dir', str(out)]
    if retained:
        args += ['--retained-source-reads', '--retained-consensus']
    assert builder.main(args) == 0
    state = json.loads((out / 'observed_state.json').read_text())
    assert state['source_reads_path'] == 'source_reads.fastq'
    assert state['observed_fasta'] == 'observed_consensus.fasta'
    for original, name in [(reads, 'source_reads.fastq'), (fasta, 'observed_consensus.fasta')]:
        published = out / name
        assert published.read_bytes() == original.read_bytes()
        assert (published.stat().st_ino == original.stat().st_ino) is retained
        original.unlink()
        assert published.stat().st_size > 0


def test_single_demux_chunk_exact_shell_branch_shares_regular_leaf(tmp_path):
    module = (REPO / 'modules/ngs/dorado_basecall.nf').read_text()
    branch = next(line.strip() for line in module.splitlines() if 'if (( \\${#label_bams[@]} == 1 ))' in line)
    branch = branch.replace('\\$', '$')
    source = tmp_path / 'chunk.bam'
    source.write_bytes(b'native chunk byte transport control')
    target = tmp_path / 'barcode01.bam'
    subprocess.run(['bash', '-euc', 'label_bams=("$1"); unit_bam="$2"; ' + branch,
                    'test', str(source), str(target)], check=True)
    assert source.stat().st_ino == target.stat().st_ino
    assert not source.stat().st_mode & 0o222
    source.unlink()
    assert target.read_bytes() == b'native chunk byte transport control'
