from __future__ import annotations

from pathlib import Path
import hashlib
from io import BytesIO
import struct
import zlib

import httpx
import pyarrow as pa
import pytest
import pytest_asyncio
from PIL import Image
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, FrustraMPNNArtifact, FrustraMPNNResult, Job, get_session
from routers.frustrampnn import router
from services.scientific_artifacts import publish_table_rows


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
async def review_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BMS_DATA", str(tmp_path / "bms-data"))
    artifact_payloads = {
        "landscape": (tmp_path / "landscape.json", b"{}"),
        "normalized_input": (tmp_path / "normalized.pdb", b"ATOM\n"),
        "structure_map": (tmp_path / "structure_map.json", b"{}"),
        "raw_csv": (tmp_path / "raw_frustrampnn.csv", b"mutation,score\nA,-1.25\nG,0.75\n"),
    }
    artifact_sha256 = {}
    for role, (path, payload) in artifact_payloads.items():
        path.write_bytes(payload)
        artifact_sha256[role] = hashlib.sha256(payload).hexdigest()
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
            settings_sha256="9" * 64, effective_settings_sha256="5" * 64,
            summary_sha256="4" * 64,
            summary_json={
                "schema_name": "frustrampnn_landscape",
                "schema_version": 3,
                "landscape_sha256": artifact_sha256["landscape"],
                "structure_map_sha256": artifact_sha256["structure_map"],
                "normalized_pdb_sha256": artifact_sha256["normalized_input"],
                "threshold_policy": {"id": "frustrampnn_class_v1"},
                "threshold_policy_id": "frustrampnn_class_v1",
                "threshold_policy_sha256": "8" * 64,
                "execution_configuration_sha256": "a" * 64,
                "requested_settings_sha256": "9" * 64,
                "effective_settings_sha256": "5" * 64,
                "runtime_identity_sha256": "b" * 64,
                "source_artifact_sha256": "2" * 64,
            },
            runtime_identity_json={}, assigned_gpu_json={}, terminal_result_json={},
        ))
        artifact_specs = {
            "landscape": ("landscape-1", "landscape.json", "application/json"),
            "normalized_input": ("normalized-input-1", "normalized.pdb", "chemical/x-pdb"),
            "structure_map": ("structure-map-1", "structure_map.json", "application/json"),
            "raw_csv": ("raw-csv-1", "raw_frustrampnn.csv", "text/csv"),
        }
        for role, (artifact_id, relative_path, media_type) in artifact_specs.items():
            storage_path, payload = artifact_payloads[role]
            session.add(FrustraMPNNArtifact(
                artifact_id=artifact_id, parent_job_id="job-1", invocation_id="inv-1",
                role=role, relative_path=relative_path, storage_path=str(storage_path),
                content_sha256=artifact_sha256[role], size_bytes=len(payload), media_type=media_type,
            ))
        schema = pa.schema([
            pa.field("id", pa.string(), nullable=False),
            pa.field("target_id", pa.string(), nullable=False),
            pa.field("entity_instance_id", pa.string(), nullable=False),
            pa.field("auth_asym_id", pa.string(), nullable=False),
            pa.field("auth_seq_id", pa.string(), nullable=False),
            pa.field("insertion_code", pa.string(), nullable=False),
            pa.field("sequence_index", pa.int64(), nullable=False),
            pa.field("wt", pa.string(), nullable=False),
            pa.field("mutation_aa", pa.string(), nullable=False),
            pa.field("score", pa.float64()),
            pa.field("score_class", pa.string(), nullable=False),
            pa.field("scoreable", pa.bool_(), nullable=False),
            pa.field("status", pa.string(), nullable=False),
            pa.field("reason", pa.string()),
            pa.field("row_json", pa.string(), nullable=False),
        ])
        rows = [
            {
                "id": f"row-{mutation}", "target_id": "target-1",
                "entity_instance_id": "entity-1", "auth_asym_id": "A",
                "auth_seq_id": "42", "insertion_code": "", "sequence_index": 42,
                "wt": "A", "mutation_aa": mutation, "score": score,
                "score_class": score_class, "scoreable": True, "status": "ok",
                "reason": None,
                "row_json": '{"residue":{"auth_asym_id":"A","auth_seq_id":42},"slot":{}}',
            }
            for mutation, score, score_class in (
                ("A", -1.25, "high"),
                ("G", 0.75, "minimal"),
            )
        ]
        await publish_table_rows(
            session,
            owner_kind="frustrampnn_result",
            owner_id="job-1:inv-1",
            role="landscape",
            schema_id="bms.frustrampnn-landscape.v1",
            source_sha256="7" * 64,
            rows=rows,
            schema=schema,
        )
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
    assert receipt["row_count"] == receipt["total_matching_rows"] == 2
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
