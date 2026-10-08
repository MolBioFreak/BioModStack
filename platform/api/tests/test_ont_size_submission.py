"""Reference-length intent survives typed HTTP submission and real Job persistence."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Job, MolBioNgsReceipt
from routers import ont_runs
from test_ont_policy_regression import policy_context, pooled_context  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", ["ont_fastq_qc", "ont_plasmid_qc", "ont_construct_screening", "wf_clone_validation"])
@pytest.mark.parametrize("reference_length,requested", [(3000, "omitted"), (3000, None), (12000, None), (12000, 7000), (12000, 100000001)])
async def test_typed_submission_persists_requested_and_effective_size(policy_context, monkeypatch, workflow, reference_length, requested):
    context = policy_context
    receipt = await context.session.get(MolBioNgsReceipt, context.receipt_ids[0])
    sequence = "ACGT" * (reference_length // 4)
    fasta = f">size_fixture\n{sequence}\n".encode("ascii")
    Path(receipt.reference_snapshot_path).write_bytes(fasta)
    receipt.reference_snapshot_sha256 = hashlib.sha256(fasta).hexdigest()
    receipt.revision_sha256 = hashlib.sha256(sequence.encode("ascii")).hexdigest()
    await context.session.commit()
    calls = []
    real_identity = ont_runs.normalized_fasta_sequence_identity
    def identity(path):
        value = real_identity(path)
        calls.append(value)
        return value
    monkeypatch.setattr(ont_runs, "normalized_fasta_sequence_identity", identity)
    params = {"fastq_path": str(context.fastq), "molbio_ngs_receipt_id": receipt.id}
    if requested != "omitted":
        params["expected_plasmid_size"] = requested
    response = await context.client.post(f"/api/ont/ngs/{workflow}/submit", json={"name": "size regression", "params": params})
    assert response.status_code == 201, response.text
    requested_value = None if requested == "omitted" else requested
    expected = reference_length if requested_value is None else requested_value
    assert calls == [(hashlib.sha256(sequence.encode("ascii")).hexdigest(), reference_length)]
    assert response.json()["params"]["expected_plasmid_size"] == expected
    assert response.json()["params"]["requested_expected_plasmid_size"] == requested_value
    async with async_sessionmaker(context.engine)() as reader:
        job = await reader.get(Job, response.json()["id"])
        assert job.params["expected_plasmid_size"] == expected
        assert job.params["requested_expected_plasmid_size"] == requested_value
        assert job.params["reference_sequence_sha256"] == calls[0][0]
        assert job.status == "queued"
