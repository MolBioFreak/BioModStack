"""Preview performs science; only save builds persisted calculation identity."""

import pytest
import rfc8785
from sqlalchemy import event

from routers import molbio_restriction as routes
from services import restriction_analysis as analysis, restriction_digest as digest
from test_restriction_digest_persistence import _client, _preview_request, _store


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["operation_only", "operation_and_fragments"])
async def test_preview_save_identity_boundary(tmp_path, monkeypatch, mode):
    engine, sessions, source_sha = await _store(tmp_path)
    writes = []
    def sql(_conn, _cursor, statement, *_args):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)
    event.listen(engine.sync_engine, "before_cursor_execute", sql)
    request = _preview_request(source_sha)
    try:
        async with await _client(sessions) as client:
            calls = []
            original = rfc8785.dumps
            def canonical(value):
                calls.append(value)
                # Source/catalog/scope identity remains, not full calculated results.
                assert not isinstance(value, dict) or not ({"occurrences", "fragments", "analysis"} & value.keys())
                return original(value)
            def forbidden(*_args, **_kwargs):
                raise AssertionError("preview constructed persisted calculation identity")
            analysis._cache.clear()
            with monkeypatch.context() as patch:
                original_sha = digest.hashlib.sha256
                def sha(value=b"", **kwargs):
                    assert b'"occurrences":' not in value and b'"fragments":' not in value
                    return original_sha(value, **kwargs)
                patch.setattr(digest.hashlib, "sha256", sha)
                patch.setattr(rfc8785, "dumps", canonical)
                patch.setattr(analysis.AnalysisResult, "canonical_result_bytes", forbidden)
                patch.setattr(digest.DigestSimulation, "canonical_unsigned_bytes", forbidden)
                patch.setattr(digest.DigestSimulation, "canonical_bytes", forbidden)
                patch.setattr(routes, "_save_receipt", forbidden)
                preview = await client.post("/api/molbio/restriction/digests/simulate", json=request)
                assert preview.status_code == 200, preview.text
                body = preview.json()
                assert all(body[key] is None for key in (
                    "analysis_result_sha256", "resource_policy_sha256", "request_sha256", "simulation_sha256",
                ))
                analyzed = await client.post("/api/molbio/restriction/analyze", json={
                    "schema": "bms.molbio.restriction-analysis-request.v1",
                    "source": request["source"], "catalog": request["catalog"],
                    "scope": {"mode": "explicit", "enzyme_ids": ["EcoRI"]},
                })
                assert analyzed.status_code == 200, analyzed.text
                assert analyzed.json()["result_sha256"] is None
                assert analyzed.json()["analysis"]["result_sha256"] is None
                assert analyzed.json()["analysis"]["occurrences"] == body["occurrences"]
                view = routes.catalog_authority.require()
                direct = digest.simulate_digest(
                    sequence="TTGAATTCAA", topology="linear", catalog=view,
                    records=[view.by_id["EcoRI"]], selected_enzyme_ids=["EcoRI"],
                    source_receipt=body["source"], catalog_receipt=body["catalog"],
                )
                assert direct.model_dump(mode="json", by_alias=True) == body
                direct_analysis = analysis.analyze_sequence(
                    sequence="TTGAATTCAA", topology="linear", catalog=view, records=[view.by_id["EcoRI"]],
                )
                assert direct_analysis.model_dump(mode="json", by_alias=True) == analyzed.json()["analysis"]
                assert not writes
            save = {**request, "schema": "bms.molbio.restriction-digest-save-request.v1",
                    "idempotency_key": mode, "persistence_mode": mode}
            saved = await client.post("/api/molbio/restriction/digests", json=save)
            assert saved.status_code == 200, saved.text
            stored = saved.json()
            assert len(stored["result_sha256"]) == 64
            for key in body.keys() - {"analysis_result_sha256", "resource_policy_sha256", "request_sha256", "simulation_sha256"}:
                assert body[key] == stored["simulation"][key]
            assert len(stored["outputs"]) == (2 if mode == "operation_and_fragments" else 0)
            assert writes
            writes.clear()
            # Both current hash-free retries and historical hash-carrying retries reopen exactly.
            with monkeypatch.context() as patch:
                patch.setattr(routes, "_complete_digest_pipeline", forbidden)
                for retry in (save, {**save, "simulation_sha256": stored["result_sha256"]}):
                    reopened = await client.post("/api/molbio/restriction/digests", json=retry)
                    assert reopened.status_code == 200, reopened.text
                    assert reopened.content == saved.content
                reopened = await client.get(f"/api/molbio/restriction/digests/{stored['operation_id']}")
                assert reopened.content == saved.content
            assert not writes
            for changed, code in [
                ({"simulation_sha256": "0" * 64}, "simulation_digest_mismatch"),
                ({"source": {**request["source"], "expected_content_sha256": "0" * 64}}, "source_revision_digest_mismatch"),
                ({"catalog": {**request["catalog"], "expected_catalog_sha256": "0" * 64}}, "catalog_digest_mismatch"),
            ]:
                rejected = await client.post("/api/molbio/restriction/digests", json={
                    **save, "idempotency_key": code, **changed,
                })
                assert rejected.status_code == 409, rejected.text
                assert rejected.json()["detail"]["code"] == code
            conflict = await client.post("/api/molbio/restriction/digests", json={**save, "enzyme_ids": ["BamHI"]})
            assert conflict.status_code == 409
    finally:
        await engine.dispose()
