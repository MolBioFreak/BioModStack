from __future__ import annotations

from pathlib import Path
import hashlib
from io import BytesIO
import struct
import zlib

import httpx
import pytest
import pytest_asyncio
from PIL import Image
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, FrustraMPNNArtifact, FrustraMPNNResult, Job, get_session
from routers.frustrampnn import router


VIEW_STATE = {"active_metric_id": "frustrampnn-native-index", "landscape_offset": 0, "metric_workbench_open": True, "chart_x_axis": "sequence_index", "chart_y_axis": "score", "structure_camera": None, "structure_representations": [], "structure_layers": []}


async def create_review(client: httpx.AsyncClient) -> dict:
    response = await client.post("/api/frustrampnn/jobs/job-1/reviews", json={
        "title": "Bound review", "notes": "",
        "result_references": [{"parent_job_id": "job-1", "invocation_id": "inv-1"}],
        "selected_residues": [], "filters": {}, "viewer_state": VIEW_STATE, "tags": [],
        "supersedes_review_id": None,
    })
    assert response.status_code == 201, response.text
    return response.json()


@pytest_asyncio.fixture
async def review_api(tmp_path: Path):
    landscape_path = tmp_path / "landscape.json"
    landscape_path.write_bytes(b"{}")
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
            effective_settings_sha256="5" * 64,
            summary_sha256="4" * 64, summary_json={}, runtime_identity_json={}, assigned_gpu_json={},
            terminal_result_json={},
        ))
        session.add(FrustraMPNNArtifact(
            artifact_id="landscape-1", parent_job_id="job-1", invocation_id="inv-1",
            role="landscape", relative_path="landscape.json", storage_path=str(landscape_path), content_sha256="6" * 64,
            size_bytes=2, media_type="application/json",
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
async def test_saved_review_revisions_are_immutable_and_preserve_view_state(review_api) -> None:
    payload = {
        "title": "Interface hotspot review",
        "notes": "Inspect chain A positions.",
        "result_references": [{"parent_job_id": "job-1", "invocation_id": "inv-1"}],
        "selected_residues": [{"auth_asym_id": "A", "auth_seq_id": "42", "insertion_code": ""}],
        "filters": {"chain": "A", "slot_status": "ok", "mutation": "W"},
        "viewer_state": {"active_metric_id": "frustrampnn-native-index", "landscape_offset": 20, "metric_workbench_open": True, "chart_x_axis": "sequence_index", "chart_y_axis": "score", "structure_camera": {"mode": "perspective"}, "structure_representations": [], "structure_layers": []},
        "tags": ["interface", "candidate-1"],
        "supersedes_review_id": None,
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

    revised = await review_api.post("/api/frustrampnn/jobs/job-1/reviews", json={**payload, "notes": "Confirmed exact mapped authority.", "tags": ["confirmed"], "supersedes_review_id": review["review_id"]})
    assert revised.status_code == 201
    assert revised.json()["review_id"] != review["review_id"]
    assert revised.json()["supersedes_review_id"] == review["review_id"]
    items = (await review_api.get("/api/frustrampnn/jobs/job-1/reviews")).json()["items"]
    assert {item["review_id"] for item in items} == {review["review_id"], revised.json()["review_id"]}
    assert (await review_api.put(f"/api/frustrampnn/jobs/job-1/reviews/{review['review_id']}", json=payload)).status_code == 404
    assert (await review_api.delete(f"/api/frustrampnn/jobs/job-1/reviews/{review['review_id']}")).status_code == 404


@pytest.mark.asyncio
async def test_saved_review_rejects_unbound_result_reference(review_api) -> None:
    response = await review_api.post("/api/frustrampnn/jobs/job-1/reviews", json={
        "title": "Bad scope",
        "notes": "",
        "result_references": [{"parent_job_id": "job-1", "invocation_id": "missing"}],
        "selected_residues": [],
        "filters": {},
        "viewer_state": VIEW_STATE,
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
@pytest.mark.parametrize(
    "viewer_state",
    [
        {**VIEW_STATE, "structure_camera": {"mode": "perspective", "target": [0, 1]}},
        {**VIEW_STATE, "structure_representations": [{"representationId": "r", "documentId": "primary", "kind": "invalid", "visible": True, "opacity": 1}]},
        {**VIEW_STATE, "structure_layers": [{"layerId": "l", "visible": True, "opacity": 2, "order": 0}]},
    ],
)
async def test_saved_review_rejects_malformed_typed_structure_state(review_api, viewer_state) -> None:
    response = await review_api.post("/api/frustrampnn/jobs/job-1/reviews", json={
        "title": "Bad typed state", "notes": "",
        "result_references": [{"parent_job_id": "job-1", "invocation_id": "inv-1"}],
        "selected_residues": [], "filters": {}, "viewer_state": viewer_state, "tags": [],
    })
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("export_format, media_type", [("json", "application/json"), ("csv", "text/csv")])
async def test_governed_export_persists_exact_download_identity(review_api, export_format: str, media_type: str) -> None:
    review = await create_review(review_api)
    created = await review_api.post("/api/frustrampnn/jobs/job-1/exports", json={
        "review_id": review["review_id"], "invocation_id": "inv-1", "format": export_format, "limit": 10,
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
    review_id = (await create_review(client))["review_id"]
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

    chunks: list[tuple[bytes, bytes]] = []
    cursor = len(b"\x89PNG\r\n\x1a\n")
    while cursor < len(png):
        length = struct.unpack(">I", png[cursor:cursor + 4])[0]
        kind = png[cursor + 4:cursor + 8]
        data = png[cursor + 8:cursor + 8 + length]
        chunks.append((kind, data))
        cursor += 12 + length
    truncated = bytearray(b"\x89PNG\r\n\x1a\n")
    for kind, data in chunks:
        if kind == b"IDAT":
            data = data[:max(1, len(data) // 2)]
        truncated.extend(struct.pack(">I", len(data)))
        truncated.extend(kind)
        truncated.extend(data)
        truncated.extend(struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    truncated_png = bytes(truncated)
    with Image.open(BytesIO(truncated_png)) as image:
        image.verify()
    truncated_response = await client.post(
        f"/api/frustrampnn/jobs/job-1/reviews/{review_id}/captures",
        params={"expected_sha256": hashlib.sha256(truncated_png).hexdigest()},
        headers={"content-type": "image/png"},
        content=truncated_png,
    )
    assert truncated_response.status_code == 422

    assert (await client.delete(f"/api/frustrampnn/jobs/job-1/reviews/{review_id}")).status_code == 404
    assert (await client.get(receipt["download_url"])).status_code == 200
