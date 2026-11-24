"""One verification run reads each input file once for its hash, and the clone step counts reads in two passes."""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("verify_construct_single_hash_test", ROOT / "scripts/verify_construct.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_a_file_named_in_several_records_is_hashed_once(tmp_path: Path, monkeypatch) -> None:
    module = _load()
    bam = tmp_path / "aligned.bam"
    bam.write_bytes(b"alignment bytes")
    made = {"count": 0}
    real = hashlib.sha256

    def counting(*args):
        made["count"] += 1
        return real(*args)

    monkeypatch.setattr(module.hashlib, "sha256", counting)
    expected = real(b"alignment bytes").hexdigest()
    # The evidence record, the artifact record and the topology check all name the same BAM.
    assert module.file_evidence(bam, role="alignment")["sha256"] == expected
    assert module.artifact_record(bam, kind="alignment", required=True)["sha256"] == expected
    assert module.sha256_file(bam) == expected
    assert made["count"] == 1
    # A file that changed is a different version and is read again.
    bam.write_bytes(b"other alignment!")
    assert module.sha256_file(bam) == real(b"other alignment!").hexdigest() and made["count"] == 2


def test_the_clone_step_derives_mapped_reads_instead_of_a_third_pass() -> None:
    text = (ROOT / "modules/ngs/clone_validation.nf").read_text(encoding="utf-8")
    block = text[text.index("total_reads="):text.index("> alignment_stats.tsv")]
    assert block.count("view -c") == 2
    assert "mapped_reads=\\$((total_reads - unmapped_reads))" in block
