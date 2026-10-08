from __future__ import annotations

from pathlib import Path
import hashlib
from io import BytesIO

import httpx
import pytest
import pytest_asyncio
from PIL import Image
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, FrustraMPNNResult, Job, get_session
from routers.frustrampnn import router


@pytest_asyncio.fixture
async def review_api(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'reviews.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(Job(id="job-1", name="CM", status="completed", queue_status="completed", model_id="conformational_mapping", mode="analysis", params={"run_frustrampnn": True}, output_dir=str(tmp_path)))
        session.add(FrustraMPNNResult(
            parent_job_id="job-1", invocation_id="inv-1", parent_workflow_id="conformational_mapping",
            candidate_id="candidate-1", requiredness="required", request_sha256="1" * 64,
            source_artifact_sha256="2" * 64, manifest_sha256="3" * 64, manifest_json={},
            summary_sha256="4" * 64, summary_json={}, runtime_identity_json={}, assigned_gpu_json={},
            terminal_result_json={},
        ))
        await session.commit()

    app = FastAPI()
    app.include_router(router)

    class PrincipalMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.authenticated_principal = {"id": request.headers.get("x-remote-user", "scientist-1"), "roles": ["scientist"]}
            return await call_next(request)

    app.add_middleware(PrincipalMiddleware)

    async def override_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await engine.dispose()


@pytest.mark.asyncio
async def test_saved_review_crud_is_result_scoped_and_preserves_view_state(review_api) -> None:
    payload = {
        "title": "Interface hotspot review",
        "notes": "Inspect chain A positions.",
        "result_references": [{"parent_job_id": "job-1", "invocation_id": "inv-1"}],
        "selected_residues": [{"auth_asym_id": "A", "auth_seq_id": "42", "insertion_code": ""}],
        "filters": {"chain": "A", "slot_status": "ok", "mutation": "W"},
        "viewer_state": {"active_metric_id": "frustrampnn-native-index", "representation": "cartoon"},
        "tags": ["interface", "candidate-1"],
    }
    created = await review_api.post("/api/frustrampnn/jobs/job-1/reviews", json=payload)
    assert created.status_code == 201, created.text
    review = created.json()
    assert review["schema_name"] == "frustrampnn_saved_review"
    assert review["schema_version"] == 1
    assert review["result_references"] == payload["result_references"]
    assert review["viewer_state"] == payload["viewer_state"]

    listed = await review_api.get("/api/frustrampnn/jobs/job-1/reviews")
    assert listed.status_code == 200
    assert [item["review_id"] for item in listed.json()["items"]] == [review["review_id"]]
    assert listed.json()["next_offset"] is None

    updated = await review_api.put(
        f"/api/frustrampnn/jobs/job-1/reviews/{review['review_id']}",
        json={**payload, "notes": "Confirmed exact mapped authority.", "tags": ["confirmed"]},
    )
    assert updated.status_code == 200
    assert updated.json()["notes"] == "Confirmed exact mapped authority."
    assert updated.json()["tags"] == ["confirmed"]

    deleted = await review_api.delete(f"/api/frustrampnn/jobs/job-1/reviews/{review['review_id']}")
    assert deleted.status_code == 204
    assert (await review_api.get("/api/frustrampnn/jobs/job-1/reviews")).json()["items"] == []


@pytest.mark.asyncio
async def test_saved_review_rejects_unbound_result_reference(review_api) -> None:
    response = await review_api.post("/api/frustrampnn/jobs/job-1/reviews", json={
        "title": "Bad scope",
        "notes": "",
        "result_references": [{"parent_job_id": "job-1", "invocation_id": "missing"}],
        "selected_residues": [],
        "filters": {},
        "viewer_state": {},
        "tags": [],
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "saved review result reference is not persisted for this job"


@pytest.mark.asyncio
async def test_saved_review_rejects_nested_unbounded_state(review_api) -> None:
    response = await review_api.post("/api/frustrampnn/jobs/job-1/reviews", json={
        "title": "Bad state", "notes": "",
        "result_references": [{"parent_job_id": "job-1", "invocation_id": "inv-1"}],
        "selected_residues": [], "filters": {"nested": {"unsafe": True}},
        "viewer_state": {}, "tags": [],
    })
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("export_format, media_type", [("json", "application/json"), ("csv", "text/csv")])
async def test_governed_export_persists_exact_download_identity(review_api, export_format: str, media_type: str) -> None:
    created = await review_api.post("/api/frustrampnn/jobs/job-1/exports", json={
        "invocation_id": "inv-1", "format": export_format, "limit": 10,
    })
    assert created.status_code == 201, created.text
    receipt = created.json()
    assert receipt["complete"] is True
    assert receipt["row_count"] == receipt["total_matching_rows"] == 0
    downloaded = await review_api.get(receipt["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith(media_type)
    assert hashlib.sha256(downloaded.content).hexdigest() == receipt["content_sha256"]


def test_csv_export_neutralizes_formula_prefixes() -> None:
    from routers.frustrampnn import _csv_safe

    assert _csv_safe("=HYPERLINK(\"https://invalid\")").startswith('"\'=')
    assert _csv_safe("+SUM(1,1)").startswith('"\'+')
    assert _csv_safe("@cmd").startswith('"\'@')
    assert _csv_safe("  =HYPERLINK(\"https://invalid\")").startswith('"\'  =')
    assert _csv_safe("\t+SUM(1,1)").startswith('"\'\t+')


@pytest.mark.asyncio
async def test_review_capture_persists_exact_png_bytes_under_review_authority(review_api) -> None:
    client = review_api
    created = await client.post("/api/frustrampnn/jobs/job-1/reviews", json={
        "title": "Capture review", "notes": "",
        "result_references": [{"parent_job_id": "job-1", "invocation_id": "inv-1"}],
        "selected_residues": [], "filters": {}, "viewer_state": {}, "tags": [],
    })
    review_id = created.json()["review_id"]
    buffer = BytesIO()
    Image.new("RGB", (2, 2), color=(12, 34, 56)).save(buffer, format="PNG")
    png = buffer.getvalue()
    digest = hashlib.sha256(png).hexdigest()

    response = await client.post(
        f"/api/frustrampnn/jobs/job-1/reviews/{review_id}/captures",
        params={"expected_sha256": digest},
        headers={"content-type": "image/png"},
        content=png,
    )
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt["content_sha256"] == digest
    assert receipt["size_bytes"] == len(png)

    download = await client.get(receipt["download_url"])
    assert download.status_code == 200, download.text
    assert download.content == png
    assert download.headers["x-content-sha256"] == digest

    wrong_actor = await client.get(receipt["download_url"], headers={"x-remote-user": "other"})
    assert wrong_actor.status_code == 404

    mismatch = await client.post(
        f"/api/frustrampnn/jobs/job-1/reviews/{review_id}/captures",
        params={"expected_sha256": "0" * 64},
        headers={"content-type": "image/png"},
        content=png,
    )
    assert mismatch.status_code == 409

    malformed = b"\x89PNG\r\n\x1a\n" + b"not-a-decoded-image"
    malformed_response = await client.post(
        f"/api/frustrampnn/jobs/job-1/reviews/{review_id}/captures",
        params={"expected_sha256": hashlib.sha256(malformed).hexdigest()},
        headers={"content-type": "image/png"},
        content=malformed,
    )
    assert malformed_response.status_code == 422

    deleted = await client.delete(f"/api/frustrampnn/jobs/job-1/reviews/{review_id}")
    assert deleted.status_code == 204
    assert (await client.get(receipt["download_url"])).status_code == 404
