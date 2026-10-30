"""The verified-publication cache must never outlive the evidence it verified."""
import os
from types import SimpleNamespace

from services import esmfold2_scientific_consumer as consumer


def _fingerprint(root, job, rows):
    return consumer._publication_fingerprint(job, root, rows)


def test_fingerprint_changes_with_any_publication_evidence(tmp_path):
    (tmp_path / "s0.cif").write_bytes(b"data_a\n")
    (tmp_path / "s0.metrics.json").write_text("{}")
    job = SimpleNamespace(provenance={"a": 1}, params={"n": 1})
    rows = [SimpleNamespace(id="d1", name="s0")]
    base = _fingerprint(tmp_path, job, rows)
    assert _fingerprint(tmp_path, job, rows) == base

    # Same-size rewrite: content changes are caught through mtime/inode.
    st = (tmp_path / "s0.cif").stat()
    (tmp_path / "s0.cif").write_bytes(b"data_b\n")
    os.utime(tmp_path / "s0.cif", ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    changed = _fingerprint(tmp_path, job, rows)
    assert changed != base

    # An added publication file must invalidate (the inventory check rejects it).
    (tmp_path / "extra.cif").write_bytes(b"x")
    added = _fingerprint(tmp_path, job, rows)
    assert added != changed
    (tmp_path / "extra.cif").unlink()
    assert _fingerprint(tmp_path, job, rows) == changed

    assert _fingerprint(tmp_path, SimpleNamespace(provenance={"a": 2}, params={"n": 1}), rows) != changed
    assert _fingerprint(tmp_path, job, rows + [SimpleNamespace(id="d2", name="s1")]) != changed
    edited = [SimpleNamespace(id="d1", name="s0", confidence_metrics={"x": 1})]
    assert _fingerprint(tmp_path, job, edited) != changed
