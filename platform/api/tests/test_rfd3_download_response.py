"""HTTP transport fixtures, not RFD3 execution/publication acceptance."""
import gzip
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from routers import jobs


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["generation", "local_redesign"])
@pytest.mark.parametrize("compressed", [False, True])
async def test_rfd3_download_filename_matches_http_decoded_content(tmp_path, owner, compressed):
    # The database read is an inert transport double. Integrity/path checks and
    # actual FileResponse streaming remain the production implementation.
    content = b"data_transport_fixture\n#\n" if compressed else b'{"transport_fixture":true}\n'
    filename = "candidate.cif.gz" if compressed else "metadata.json"
    native_bytes = gzip.compress(content, mtime=0) if compressed else content
    path = tmp_path / filename
    path.write_bytes(native_bytes)
    digest = hashlib.sha256(native_bytes).hexdigest()
    media_type = "chemical/x-mmcif+gzip" if compressed else "application/json"
    role = "candidate_structure" if compressed else "candidate_metadata"
    job = SimpleNamespace(id="transport-job", model_id="protein_local_redesign", output_dir=str(tmp_path))
    if owner == "generation":
        item = SimpleNamespace(provenance={"artifacts": [{
            "role": role, "relative_path": filename, "bytes": len(native_bytes),
            "sha256": digest, "media_type": media_type,
        }]})
    else:
        item = SimpleNamespace(role="structure" if compressed else "native_prediction_metadata",
                               relative_path=filename, storage_path=str(path),
                               size_bytes=len(native_bytes), content_sha256=digest,
                               media_type=media_type)
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalar_one_or_none=lambda: job),
        SimpleNamespace(scalar_one_or_none=lambda: item),
    ]))
    app = FastAPI()

    @app.get("/artifact")
    async def artifact():
        if owner == "generation":
            return await jobs.get_rfd3_generation_artifact(job.id, "candidate", role, session)
        return await jobs.get_rfd3_local_redesign_artifact(job.id, "artifact", session)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/artifact")
    assert response.status_code == 200, response.text
    assert response.content == content  # HTTPX and browsers decode the gzip transport.
    expected_name = "candidate.cif" if compressed else "metadata.json"
    assert response.headers["content-disposition"] == f'attachment; filename="{expected_name}"'
    assert response.headers.get("content-encoding") == ("gzip" if compressed else None)
    assert path.read_bytes() == native_bytes
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
