"""Lane H request-local work and immutable-history regressions (isolated only)."""
import json
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_sample_create_replay_uses_completed_revision_not_head(monkeypatch):
    import molbio_ngs_services as service
    from molbio_ngs_models import MolBioNGSSample, MolBioNGSIdempotencyClaim

    sample = SimpleNamespace(id="sample", current_revision_id="newer")
    original = SimpleNamespace(id="original")
    async def get(model, key):
        if model is MolBioNGSSample:
            return sample
        if model is MolBioNGSIdempotencyClaim:
            return SimpleNamespace(response_json=json.dumps({"sample_revision_id": "original"}))
        raise AssertionError("replay must resolve the persisted revision through the exact reader")
    monkeypatch.setattr(service, "_validate_sample_payload", lambda value: None)
    monkeypatch.setattr(service, "require_acknowledged_local_domain", AsyncMock())
    monkeypatch.setattr(service, "_reserve_idempotency", AsyncMock(return_value="sample"))
    reader = AsyncMock(return_value=original)
    monkeypatch.setattr(service, "get_sample_revision", reader)
    result = await service.create_sample(SimpleNamespace(get=get), global_domain_experiment_id="domain", payload={}, idempotency_key="key")
    assert result == (sample, original)
    assert reader.call_args.args[1:] == ("domain", "sample", "original")


@pytest.mark.asyncio
async def test_reference_create_replay_uses_completed_revision_not_head(monkeypatch):
    from services import molbio_ngs_references as service
    from molbio_ngs_models import MolBioNGSDomainState, MolBioNGSGlobalBinding, MolBioNGSIdempotencyClaim

    async def get(model, key):
        if model is MolBioNGSDomainState:
            return SimpleNamespace(current_binding_revision_id="binding")
        if model is MolBioNGSGlobalBinding:
            return SimpleNamespace(binding_state="acknowledged")
        if model is MolBioNGSIdempotencyClaim:
            return SimpleNamespace(response_json=json.dumps({"reference_revision_id": "original"}))
        raise AssertionError(model)
    resource = SimpleNamespace(current_revision_id="newer")
    monkeypatch.setattr(service, "_reserve_idempotency", AsyncMock(return_value="ref"))
    monkeypatch.setattr(service, "get_reference_resource", AsyncMock(return_value=resource))
    reader = AsyncMock(return_value=SimpleNamespace(id="original"))
    monkeypatch.setattr(service, "get_reference_revision", reader)
    session = SimpleNamespace(get=get)
    _, revision = await service.create_reference(session, global_domain_experiment_id="domain", name="ref", raw_fasta=b">ref\nACGT\n", molecule_type="dna", topology="linear", coordinate_contract="exact", source_provenance={"kind":"test"}, idempotency_key="key")
    assert revision.id == "original"
    reader.assert_awaited_once_with(session, "ref", "original")


@pytest.mark.asyncio
async def test_grouped_counts_and_cursor_summaries_use_scalar_sql(tmp_path):
    from molbio_ngs_models import MolBioNGSBase, MolBioNGSSample
    from molbio_ngs_services import _batch_domain_counts
    from routers.molbio_ngs_experiments import read_molbio_ngs_history_summaries

    # Isolated read-model fixture, not a migration/connector acceptance test.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'read-model.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(MolBioNGSBase.metadata.create_all)
    statements = []
    event.listen(engine.sync_engine, "before_cursor_execute", lambda conn, cursor, statement, params, context, many: statements.append(statement))
    try:
        async with async_sessionmaker(engine)() as session:
            session.add_all([MolBioNGSSample(id=f"sample-{i:03}", global_domain_experiment_id=f"domain-{i % 5}", head_generation=0, created_at="2026-09-11", updated_at="2026-09-11") for i in range(25)])
            await session.commit()
            statements.clear()
            counts = await _batch_domain_counts(session, [f"domain-{i}" for i in range(6)])
            assert len(statements) == 3
            assert counts["domain-0"] == {"samples":5, "references":0, "evidence_assessments":0}
            assert counts["domain-5"]["samples"] == 0
            assert all("GROUP BY" in statement for statement in statements)
            statements.clear()
            seen, cursor = [], None
            while True:
                page = await read_molbio_ngs_history_summaries("domain-0", "samples", session, limit=2, cursor=cursor, resource_id=None)
                assert page.total == 5
                assert len(page.items) <= 2
                seen.extend(item.id for item in page.items)
                cursor = page.next_cursor
                if cursor is None:
                    break
            assert len(seen) == len(set(seen)) == 5
            assert all("canonical_payload" not in statement and "canonical_wrapper" not in statement for statement in statements)
            assert all("LIMIT" in statement for statement in statements if "head_generation" in statement)
            from molbio_ngs_models import MolBioNGSReferenceResource, MolBioNGSReferenceRevision
            resources = [("active", "domain-0", None), ("archived", "domain-0", "2026-09-10"), ("foreign", "domain-1", None)]
            for identity, domain, archived in resources:
                session.add(MolBioNGSReferenceResource(id=identity, global_domain_experiment_id=domain, name=f"Reference {identity}", head_generation=2, archived_at=archived, created_at="2026-09-11", updated_at="2026-09-11"))
                for number in (1, 2):
                    session.add(MolBioNGSReferenceRevision(id=f"{identity}-{number}", reference_id=identity, global_domain_experiment_id=domain, revision_number=number, artifact_id="unused-read-model-artifact", schema_name="fixture", schema_version="1", canonical_payload="not hydrated by summaries", payload_sha256=str(number) * 64, canonical_fasta_sha256="a" * 64, canonical_fasta_size_bytes=10, contig_manifest_sha256="b" * 64, molecule_type="dna", topology="linear", coordinate_contract="exact", source_provenance="{}", created_at="2026-09-11"))
            await session.commit()
            statements.clear()
            page = await read_molbio_ngs_history_summaries("domain-0", "reference", session, limit=3, cursor=None, resource_id=None)
            assert page.total == 4
            assert [item.revision_number for item in page.items] == [2, 2, 1]
            assert {item.reference_id for item in page.items} == {"active", "archived"}
            assert all(item.canonical_fasta_sha256 == "a" * 64 and item.name == f"Reference {item.reference_id}" for item in page.items)
            assert next(item for item in page.items if item.reference_id == "archived").archived_at == "2026-09-10"
            final = await read_molbio_ngs_history_summaries("domain-0", "reference", session, limit=3, cursor=page.next_cursor, resource_id=None)
            assert len(final.items) == 1 and final.next_cursor is None
            assert len({item.id for item in [*page.items, *final.items]}) == 4
            assert len(statements) == 5  # count/page, then count/cursor/page, no per-reference queries
            assert not any("canonical_payload" in statement for statement in statements)
            filtered = await read_molbio_ngs_history_summaries("domain-0", "reference", session, limit=3, cursor=None, resource_id="active")
            assert filtered.total == 2 and all(item.reference_id == "active" for item in filtered.items)
            from fastapi import HTTPException
            with pytest.raises(HTTPException, match="cursor is not in"):
                await read_molbio_ngs_history_summaries("domain-0", "reference", session, limit=3, cursor="foreign-1", resource_id=None)
    finally:
        await engine.dispose()


def test_occurrence_map_hash_is_reused_and_descriptor_tampering_rejected(tmp_path, monkeypatch):
    from tests.test_molbio_ngs_workup import _comparison_fixture
    from services import molbio_ngs_workup as service

    job, manifest, current, path = _comparison_fixture(tmp_path)
    summary = json.loads(path.read_text())
    original = service._sha256_file
    calls = Counter()
    def counted(path):
        calls[path.name] += 1
        return original(path)
    monkeypatch.setattr(service, "_sha256_file", counted)
    result = service.project_ngs_workup(job, manifest, current, summary, comparison_panel_root=path.parent, comparison_summary_path=path)
    assert result["scientific_status"] == "PASS"
    assert calls["comparison_panel_occurrence_map.json"] == 1
    summary["artifacts"][-1]["sha256"] = "0" * 64
    path.write_text(json.dumps(summary))
    result = service.project_ngs_workup(job, manifest, current, summary, comparison_panel_root=path.parent, comparison_summary_path=path)
    assert result["scientific_status"] == "REVIEW"


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_assessment_replay_precedes_native_sources_and_replays_original_verdict(monkeypatch, legacy):
    from services import molbio_ngs_evidence as service

    kwargs = dict(global_domain_experiment_id="domain", state_revision_id="state", ngs_job_receipt_id="job-receipt", ngs_result_manifest_receipt_id="manifest-receipt", ngs_reference_revision_receipt_id="reference-receipt", assessment_rule_id=next(iter(service.ASSESSMENT_RULE_REGISTRY)), idempotency_key="retry")
    intent = {"global_domain_experiment_id":"domain", "state_revision_id":"state", "sample_revision_id":None, "receipt_ids":{"ngs_job":"job-receipt", "ngs_result_manifest":"manifest-receipt", "ngs_reference_revision":"reference-receipt", "ont_instrument_run":None, "molecular_revision":None, "ngs_comparison_panel":None}, "assessment_rule_id":kwargs["assessment_rule_id"], "notes":None, "created_by":service.SERVER_OWNED_ACTOR}
    if legacy:
        intent["requested_assessment"] = "REVIEW"
    digest = service._digest(service._canonical(intent))
    claim = SimpleNamespace(status="completed", request_sha256=digest, result_resource_id="evidence")
    original = SimpleNamespace(requested_assessment="REVIEW", scientific_assessment="REVIEW")
    monkeypatch.setattr(service, "get_state_revision", AsyncMock())
    monkeypatch.setattr(service, "verify_state_revision_integrity", AsyncMock(return_value=({}, [])))
    monkeypatch.setattr(service, "get_evidence_assessment", AsyncMock(return_value=original))
    async def reserve(*args, **kw):
        assert kw["request_sha256"] == digest
        return "evidence"
    monkeypatch.setattr(service, "_reserve_idempotency", reserve)
    native = AsyncMock(side_effect=AssertionError("completed replay must not read native data"))
    monkeypatch.setattr(service, "_required_receipt", native)
    result = await service.create_evidence_assessment(SimpleNamespace(get=AsyncMock(return_value=claim)), None, None, **kwargs)
    assert result is original
    native.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_manifest_snapshot_reads_and_hashes_once(tmp_path, monkeypatch):
    from services import molbio_ngs_member_receipts as service
    root = tmp_path / "job"
    path = root / "fastq_qc" / "qc_manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema":"sequence_qc.manifest.v1", "artifact_schema_version":1, "job_id":"job", "workflow_status":"completed", "verification_status":"review", "workflow_id":"ont_fastq_qc", "input_mode":"fastq", "analysis_status":"completed", "artifacts":[]}))
    job = SimpleNamespace(id="job", model_id="nanopore", params={"ont_workflow_id":"ont_fastq_qc"})
    monkeypatch.setattr(service, "resolve_persisted_job_result_root", lambda job: root)
    original = service.read_manifest_json_nofollow
    calls = []
    def counted(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(service, "read_manifest_json_nofollow", counted)
    raw = path.read_bytes()
    original_hash = service._sha256_bytes
    def hash_distinct_preimage(content):
        assert content != raw, "already verified manifest digest must be reused"
        return original_hash(content)
    monkeypatch.setattr(service, "_sha256_bytes", hash_distinct_preimage)
    receipt, document = await service.resolve_ngs_result_manifest_snapshot(SimpleNamespace(get=AsyncMock(return_value=job)), job_id="job")
    assert document["job_id"] == "job"
    assert receipt.content_digest == __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    assert calls == [path]


@pytest.mark.asyncio
async def test_reference_receipt_validates_metadata_and_reads_bytes_once(tmp_path, monkeypatch):
    from services import molbio_ngs_references as service
    path = tmp_path / "reference.fasta"
    path.write_bytes(b">ref\nACGT\n")
    digest = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    revision = SimpleNamespace(id="rev", reference_id="ref", global_domain_experiment_id="domain", artifact_id="artifact", revision_number=1, canonical_fasta_sha256=digest, canonical_fasta_size_bytes=path.stat().st_size)
    artifact = SimpleNamespace(reference_id="ref", managed_relative_path="fixture", sha256=digest, size_bytes=path.stat().st_size)
    monkeypatch.setattr(service, "get_reference_resource", AsyncMock(return_value=SimpleNamespace(id="ref")))
    metadata = AsyncMock(return_value=revision)
    monkeypatch.setattr(service, "get_reference_revision", metadata)
    monkeypatch.setattr(service, "_managed_path", lambda _: path)
    hash_calls = []
    original = service._sha256
    def hashed(content):
        hash_calls.append(content)
        return original(content)
    monkeypatch.setattr(service, "_sha256", hashed)
    receipt = await service.resolve_ngs_reference_revision_receipt(SimpleNamespace(get=AsyncMock(return_value=artifact)), global_domain_experiment_id="domain", reference_id="ref", revision_id="rev")
    assert receipt.content_digest == digest
    assert metadata.await_count == 1
    assert hash_calls == [b">ref\nACGT\n"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["job", "instrument"])
async def test_completed_attachment_does_not_reread_native_artifacts(monkeypatch, kind):
    from services import molbio_ngs_evidence as service
    row = SimpleNamespace()
    domain_session = SimpleNamespace(get=AsyncMock(return_value=row))
    job = SimpleNamespace(model_id="nanopore", params={"global_domain_experiment_id":"domain", "molbio_ngs_state_revision_id":"state"})
    core_session = SimpleNamespace(get=AsyncMock(return_value=job))
    monkeypatch.setattr(service, "get_state_revision", AsyncMock())
    monkeypatch.setattr(service, "_reserve_idempotency", AsyncMock(return_value="receipt"))
    monkeypatch.setattr(service, "_receipt_row_authority", lambda _: {})
    native = AsyncMock(side_effect=AssertionError("native source unavailable after completion"))
    monkeypatch.setattr(service, "resolve_ngs_job_receipt", native)
    monkeypatch.setattr(service, "resolve_ngs_result_manifest_receipt", native)
    monkeypatch.setattr(service, "resolve_ont_instrument_run_receipt", native)
    if kind == "job":
        result = await service.attach_job_evidence(domain_session, core_session, global_domain_experiment_id="domain", job_id="job", idempotency_key="retry")
        assert result == (row, row)
    else:
        result = await service.attach_instrument_run_evidence(domain_session, core_session, global_domain_experiment_id="domain", state_revision_id="state", run_id="run", observed_generation=1, idempotency_key="retry")
        assert result is row
    native.assert_not_awaited()
