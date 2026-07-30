from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import routers.conformational_mapping as cm_router
from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import (
    Base,
    ConformationalMappingArtifact,
    ConformationalMappingStateLandscapeAnalysisHeader,
    ConformationalMappingStateLandscapeAnalysisPair,
    ConformationalMappingStateLandscapeAnalysisRow,
    Job,
)
from services.conformational_mapping.persistence import (
    ConformationalPersistenceError,
    issue_request_capability,
    paged_state_landscape_analysis_rows,
    register_prepared_request,
)


async def _session(tmp_path: Path) -> tuple[AsyncSession, object]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'state-analysis-api.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)(), engine


def _request(*, token: str | None = None, principal: str | None = None) -> Request:
    headers = [] if token is None else [(b"authorization", f"Bearer {token}".encode("ascii"))]
    request = Request({
        "type": "http", "method": "GET", "scheme": "http", "path": "/api/conformational-mapping/requests/r/state-landscape-analysis",
        "query_string": b"", "headers": headers, "client": ("127.0.0.1", 42000), "server": ("127.0.0.1", 8000),
    })
    if principal is not None:
        request.state.authenticated_principal = SimpleNamespace(id=principal, roles=["scientist"])
    return request


async def _seed_request(session: AsyncSession, request_id: str, *, principal: str = "alice") -> str:
    token, digest = issue_request_capability()
    await register_prepared_request(
        session,
        job=Job(
            id=f"job-{request_id}", name="state-analysis", model_id="conformational_mapping",
            mode="map", status="completed", params={}, created_at=datetime.now(UTC).replace(tzinfo=None),
        ),
        principal_id=principal,
        request={
            "request_id": request_id, "request_sha256": (request_id * 64)[:64],
            "backend": "external_import",
        },
        coordinate_plan={"coordinate_plan_sha256": "b" * 64, "expected_cardinality": 1, "coordinates": [{}]},
        resume_key="0" * 64,
        capability_sha256=digest,
    )
    return token


async def _seed_analysis(
    session: AsyncSession, request_id: str, *, analysis_id: str = "analysis-a", content_sha256: str = "a" * 64,
) -> None:
    session.add(ConformationalMappingStateLandscapeAnalysisHeader(
        request_id=request_id, analysis_id=analysis_id, content_sha256=content_sha256,
        source_ensemble_sha256="e" * 64, source_landscape_sha256="l" * 64,
        source_structure_map_sha256="s" * 64, comparison_sha256="c" * 64,
        formula_version="cm_state_landscape_analysis_v1", formula_sha256="f" * 64,
        policy_sha256="p" * 64, comparison_mode="pairwise", comparison_target_id="target-a",
        comparison_scope="all_within_target", reference_backend_coordinates_json=None,
        reference_candidate_id=None, pair_count=2, row_count=3, exclusion_count=4,
    ))
    pairs = [
        ("pair-a", "candidate-a", "candidate-b"),
        ("pair-b", "candidate-a", "candidate-c"),
    ]
    for pair_id, candidate_a_id, candidate_b_id in pairs:
        session.add(ConformationalMappingStateLandscapeAnalysisPair(
            request_id=request_id, analysis_id=analysis_id, pair_id=pair_id,
            candidate_a_id=candidate_a_id, candidate_b_id=candidate_b_id,
        ))
    rows = [
        ("pair-b", "candidate-a", "candidate-c", 1),
        ("pair-a", "candidate-a", "candidate-b", 4),
        ("pair-a", "candidate-a", "candidate-b", 2),
    ]
    for pair_id, candidate_a_id, candidate_b_id, sequence_index in rows:
        session.add(ConformationalMappingStateLandscapeAnalysisRow(
            id=f"{request_id}-{analysis_id}-{pair_id}-{sequence_index}", request_id=request_id,
            analysis_id=analysis_id, pair_id=pair_id, candidate_a_id=candidate_a_id,
            candidate_b_id=candidate_b_id, target_id="target-a", entity_instance_id="entity-1",
            auth_asym_id="A", auth_seq_id=sequence_index, insertion_code="", sequence_index=sequence_index,
            validated_wt="G", metrics_json={"native_score": {"status": "ok", "reason": None}},
            availability_json={"native_score": {"status": "ok", "reason": None}},
        ))


@pytest.mark.asyncio
async def test_state_analysis_summary_is_compact_personal_workflow_and_projection_backed(tmp_path: Path) -> None:
    session, engine = await _session(tmp_path)
    try:
        token = await _seed_request(session, "request-a")
        await _seed_analysis(session, "request-a")
        session.add(ConformationalMappingArtifact(
            artifact_id="artifact-state-a", request_id="request-a", candidate_id=None,
            role="state_landscape_analysis", relative_path="derived/state.json", storage_path="/not-read/state.json",
            content_sha256="a" * 64, size_bytes=123, media_type="application/json", metadata_json={},
        ))
        await session.commit()

        summary = await cm_router.state_landscape_analysis_summary("request-a", _request(token=token), session)

        assert summary["analysis_id"] == "analysis-a"
        assert summary["authority"]["content_sha256"] == "a" * 64
        assert summary["counts"] == {"pairs": 2, "rows": 3, "exclusions": 4}
        assert [pair["pair_id"] for pair in summary["pairs"]] == ["pair-a", "pair-b"]
        assert summary["artifact"]["artifact_id"] == "artifact-state-a"
        assert summary["artifact"]["download_url"].endswith("/artifacts/artifact-state-a")
        assert "rows" not in summary and "exclusion_ledger" not in summary

        public_summary = await cm_router.state_landscape_analysis_summary(
            "request-a", _request(principal="bob"), session,
        )
        assert public_summary["analysis_id"] == "analysis-a"
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_state_analysis_routes_return_404_without_request_local_projection_or_cross_request_fallback(
    tmp_path: Path,
) -> None:
    session, engine = await _session(tmp_path)
    try:
        token_a = await _seed_request(session, "request-a")
        token_b = await _seed_request(session, "request-b")
        await _seed_analysis(session, "request-b", analysis_id="analysis-b", content_sha256="b" * 64)
        await _seed_analysis(session, "request-b", analysis_id="analysis-c", content_sha256="d" * 64)
        await session.commit()

        with pytest.raises(HTTPException) as absent:
            await cm_router.state_landscape_analysis_summary("request-a", _request(token=token_a), session)
        assert absent.value.status_code == 404

        with pytest.raises(HTTPException) as cross_request:
            await cm_router.state_landscape_analysis_rows(
                "request-a", _request(token=token_a), analysis_id="analysis-b", session=session,
            )
        assert cross_request.value.status_code == 404

        with pytest.raises(HTTPException) as ambiguous:
            await cm_router.state_landscape_analysis_summary("request-b", _request(token=token_b), session)
        assert ambiguous.value.status_code == 409

        with pytest.raises(HTTPException) as missing_export:
            await cm_router.state_landscape_analysis_summary(
                "request-b", _request(token=token_b), session, analysis_id="analysis-b",
            )
        assert missing_export.value.status_code == 409
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_state_analysis_rows_are_bounded_ordered_filtered_and_projection_only(tmp_path: Path) -> None:
    session, engine = await _session(tmp_path)
    try:
        token = await _seed_request(session, "request-a")
        await _seed_analysis(session, "request-a")
        await session.commit()

        page_one = await cm_router.state_landscape_analysis_rows(
            "request-a", _request(token=token), offset=0, limit=2, session=session,
        )
        assert [(row["pair_id"], row["identity"]["sequence_index"]) for row in page_one["rows"]] == [
            ("pair-a", 2), ("pair-a", 4),
        ]
        assert page_one["next_offset"] == 2
        assert page_one["applied_filters"] == {
            "pair_id": None, "candidate_id": None, "entity_instance_id": None,
            "auth_asym_id": None, "sequence_start": None, "sequence_end": None,
        }

        page_two = await cm_router.state_landscape_analysis_rows(
            "request-a", _request(token=token), offset=2, limit=2, session=session,
        )
        assert [(row["pair_id"], row["identity"]["sequence_index"]) for row in page_two["rows"]] == [
            ("pair-b", 1),
        ]
        assert page_two["next_offset"] is None

        filtered = await cm_router.state_landscape_analysis_rows(
            "request-a", _request(token=token), pair_id="pair-a", candidate_id="candidate-b",
            sequence_start=3, sequence_end=4, session=session,
        )
        assert [row["identity"]["sequence_index"] for row in filtered["rows"]] == [4]
        assert filtered["selected_analysis_id"] == "analysis-a"

        for kwargs in ({"offset": -1}, {"limit": 0}, {"limit": 1001}, {"sequence_start": 4, "sequence_end": 3}):
            with pytest.raises(HTTPException) as invalid:
                await cm_router.state_landscape_analysis_rows("request-a", _request(token=token), session=session, **kwargs)
            assert invalid.value.status_code == 422
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_state_analysis_rows_reject_oversized_offset_at_route_and_persistence_boundary(
    tmp_path: Path,
) -> None:
    """The 100k-row analysis cap bounds both HTTP and direct read callers."""

    session, engine = await _session(tmp_path)
    try:
        token = await _seed_request(session, "request-a")
        await _seed_analysis(session, "request-a")
        await session.commit()

        with pytest.raises(HTTPException) as route_error:
            await cm_router.state_landscape_analysis_rows(
                "request-a", _request(token=token), offset=2**100, session=session,
            )
        assert route_error.value.status_code == 422

        with pytest.raises(ConformationalPersistenceError, match="invalid state landscape analysis page"):
            await paged_state_landscape_analysis_rows(
                session, "request-a", offset=2**100,
            )
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_state_analysis_rows_omit_next_offset_at_exact_terminal_boundaries(tmp_path: Path) -> None:
    session, engine = await _session(tmp_path)
    try:
        token = await _seed_request(session, "request-a")
        await _seed_analysis(session, "request-a")
        await session.commit()

        exact_total = await cm_router.state_landscape_analysis_rows(
            "request-a", _request(token=token), offset=0, limit=3, session=session,
        )
        assert len(exact_total["rows"]) == 3
        assert exact_total["next_offset"] is None

        exact_filtered_total = await cm_router.state_landscape_analysis_rows(
            "request-a", _request(token=token), pair_id="pair-a", candidate_id="candidate-b",
            offset=0, limit=2, session=session,
        )
        assert [row["identity"]["sequence_index"] for row in exact_filtered_total["rows"]] == [2, 4]
        assert exact_filtered_total["next_offset"] is None
    finally:
        await session.close()
        await engine.dispose()
