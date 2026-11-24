import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import molbio_ngs_receipts as receipts


class _Session:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_rna_revision_writes_a_dna_letter_reference_and_keeps_the_saved_rna(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, "get_inputs_dir", lambda: tmp_path)
    rna = "ACGUUGCA"
    revision = SimpleNamespace(id="rev-rna", content_sha256=hashlib.sha256(rna.encode()).hexdigest(),
                               snapshot={"sequence": rna, "sequence_type": "rna"})
    receipt = await receipts.issue_molbio_ngs_receipt(_Session(), sequence_id="seq-rna", revision=revision)
    written = Path(receipt.reference_snapshot_path).read_text()
    assert written == ">molbio_revision_rev-rna\nACGTTGCA\n"
    assert receipt.reference_snapshot_sha256 == hashlib.sha256(written.encode()).hexdigest()
    # The library record is untouched and the receipt still names the RNA revision.
    assert revision.snapshot["sequence"] == rna
    assert receipt.revision_sha256 == revision.content_sha256


@pytest.mark.asyncio
async def test_dna_revision_reference_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, "get_inputs_dir", lambda: tmp_path)
    revision = SimpleNamespace(id="rev-dna", content_sha256=hashlib.sha256(b"ACGT").hexdigest(),
                               snapshot={"sequence": "ACGT", "sequence_type": "dna"})
    receipt = await receipts.issue_molbio_ngs_receipt(_Session(), sequence_id="seq-dna", revision=revision)
    assert Path(receipt.reference_snapshot_path).read_text() == ">molbio_revision_rev-dna\nACGT\n"
