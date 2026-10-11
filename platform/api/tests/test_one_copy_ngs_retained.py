import hashlib
import os

import pytest

from services import molbio_ngs_references as refs
from services import ngs_alignment_sessions as reader
import paths


@pytest.fixture
def roots(tmp_path, monkeypatch):
    managed = tmp_path / 'references'
    inputs = tmp_path / 'inputs'
    managed.mkdir()
    inputs.mkdir()
    for module in (refs, paths):
        monkeypatch.setattr(module, 'get_molbio_ngs_reference_root', lambda: managed)
        monkeypatch.setattr(module, 'get_inputs_dir', lambda: inputs)
    return managed, inputs


def test_reference_launch_names_share_one_completed_payload(roots):
    managed, _ = roots
    payload = b'>ref\nACGT\n'
    refs._write_managed_bytes('revision/reference.fasta', payload)
    source = managed / 'revision/reference.fasta'
    digest = hashlib.sha256(payload).hexdigest()
    launches = [refs._snapshot_managed_reference(source, expected_sha256=digest,
                expected_size_bytes=len(payload))[0] for _ in range(3)]
    assert len({p.stat().st_ino for p in [source, *launches]}) == 1
    assert not source.stat().st_mode & 0o222
    launches[0].unlink()
    assert launches[1].read_bytes() == payload
    source.unlink()
    source.write_bytes(b'>replacement\nTTTT\n')
    assert launches[2].read_bytes() == payload


def test_legacy_writable_reference_retains_private_snapshot(roots):
    managed, _ = roots
    source = managed / 'legacy.fasta'
    payload = b'>ref\nACGT\n'
    source.write_bytes(payload)
    snapshot, _, _ = refs._snapshot_managed_reference(source,
        expected_sha256=hashlib.sha256(payload).hexdigest(), expected_size_bytes=len(payload))
    assert source.stat().st_ino != snapshot.stat().st_ino
    source.write_bytes(b'changed')
    assert snapshot.read_bytes() == payload


@pytest.mark.parametrize('oversized', [False, True])
def test_retained_reader_borrows_descriptor_without_body_allocation(roots, monkeypatch, oversized):
    managed, _ = roots
    payload = b'>ref\nACGT\n'
    refs._write_managed_bytes('reference.fasta', payload)
    source = managed / 'reference.fasta'
    if oversized:
        monkeypatch.setattr(reader, 'SNAPSHOT_CACHE_MAX_BYTES', 1)
    def no_snapshot(*args, **kwargs):
        pytest.fail('immutable read allocated a snapshot')
    monkeypatch.setattr(reader, '_reserve_snapshot', no_snapshot)
    monkeypatch.setattr(reader, '_uncached_artifact_snapshot', no_snapshot)
    for _ in range(3):
        with reader.open_verified_artifact_snapshot(source, expected_size=len(payload),
                expected_sha256=hashlib.sha256(payload).hexdigest(), retained=True) as handle:
            assert os.fstat(handle.fileno()).st_ino == source.stat().st_ino
            assert handle.read() == payload
    handle = reader.open_verified_artifact_snapshot(source, expected_size=len(payload),
                expected_sha256=hashlib.sha256(payload).hexdigest(), retained=True)
    descriptor = handle.fileno()
    source.unlink()
    source.write_bytes(b'replacement')
    assert handle.read() == payload
    handle.close()
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_readonly_upload_is_not_immutable_custody(roots, tmp_path):
    source = tmp_path / 'upload.bam'
    source.write_bytes(b'fake-tiny-body')
    source.chmod(0o400)
    assert reader._borrow_retained_artifact(source, source.stat().st_size,
        hashlib.sha256(source.read_bytes()).hexdigest()) is None


def test_retained_reader_preserves_digest_refusal(roots):
    managed, _ = roots
    refs._write_managed_bytes('reference.fasta', b'>ref\nACGT\n')
    with pytest.raises(reader.AlignmentSessionError, match='digest mismatch'):
        reader.open_verified_artifact_snapshot(managed / 'reference.fasta',
            expected_size=10, expected_sha256='0' * 64, retained=True)


def test_governed_completed_oversized_artifact_borrows_pinned_directory(tmp_path, monkeypatch):
    payload = b'completed alignment transport control'
    source = tmp_path / 'aligned.bam'
    source.write_bytes(payload)
    source.chmod(0o444)
    monkeypatch.setattr(reader, 'SNAPSHOT_CACHE_MAX_BYTES', 1)
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    descriptor_path = type(source)(f'/proc/self/fd/{directory}/aligned.bam')
    try:
        for _ in range(3):
            with reader.open_verified_artifact_snapshot(descriptor_path,
                    expected_size=len(payload), expected_sha256=hashlib.sha256(payload).hexdigest(),
                    retained=True) as handle:
                assert os.fstat(handle.fileno()).st_ino == source.stat().st_ino
                assert handle.read() == payload
    finally:
        os.close(directory)


def test_comparison_launch_reuses_receipt_bound_fasta_leaves(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from database import NgsComparisonPanelReceipt
    from services import ngs_comparison_panels as panels
    from services import molbio_ngs_receipts as receipts

    monkeypatch.setattr(panels, 'get_inputs_dir', lambda: tmp_path)
    expected = tmp_path / 'receipt' / 'expected_reference.fasta'
    receipts._atomic_write(expected, b'>expected\nACGT\n')
    panel = tmp_path / 'approved_panel'
    panel.mkdir()
    fasta = panel / 'host.fasta'
    fasta.write_bytes(b'>host\nTGCA\n')
    fasta.chmod(0o400)
    manifest = panel / 'manifest.json'
    manifest.write_text(json.dumps({'schema': panels.PANEL_SCHEMA, 'status': 'APPROVED',
        'panel_id': 'panel', 'version': 1, 'entries': [{'id': 'host', 'label': 'Host', 'role': 'host',
        'fasta_filename': fasta.name, 'fasta_sha256': hashlib.sha256(fasta.read_bytes()).hexdigest()}]}))
    receipt = NgsComparisonPanelReceipt(panel_id='panel', panel_version=1,
        panel_snapshot_path=str(manifest), panel_snapshot_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
    for _ in range(2):
        launch = panels.materialize_comparison_launch(expected_fasta=str(expected),
            expected_sha256=hashlib.sha256(expected.read_bytes()).hexdigest(), panel_receipt=receipt)
        assert Path(launch['reference_fasta']).stat().st_ino == expected.stat().st_ino
        assert (Path(launch['input_root']) / fasta.name).stat().st_ino == fasta.stat().st_ino
        assert json.loads(Path(launch['comparison_panel_snapshot']).read_text())['entries'][0]['fasta_path'] == 'host.fasta'
