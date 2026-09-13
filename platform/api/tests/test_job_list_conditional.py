from datetime import datetime

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Job, Design
from routers import jobs as jobs_router


@pytest.mark.asyncio
async def test_summary_etag_tracks_full_visible_projection_filters_and_auth(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'conditional.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add_all([
            Job(id="completed", name="Completed", status="completed", model_id="external_import", mode="structure_import", params={}, created_at=datetime(2026, 1, 1)),
            Job(id="running", name="Running", status="running", model_id="external_import", mode="structure_import", params={}, created_at=datetime(2026, 1, 2)),
        ])
        session.add_all([
            Job(id=f"queued-{index}", name=f"Queued fixture {index}", status="queued", model_id="external_import", mode="structure_import", params={}, created_at=datetime(2025, 12, 1))
            for index in range(98)
        ])
        await session.commit()
    denied = False

    async def session_override():
        if denied:
            raise HTTPException(status_code=403, detail="fixture denied")
        async with factory() as session:
            yield session

    app = FastAPI()
    app.dependency_overrides[jobs_router.get_session] = session_override
    app.include_router(jobs_router.router, prefix="/api/jobs")
    with TestClient(app) as client:
        url = "/api/jobs?summary=true&limit=100"
        first = client.get(url)
        assert first.status_code == 200
        assert len(first.json()["jobs"]) == 100
        etag = first.headers["etag"]
        assert first.headers["cache-control"] == "private, no-cache"
        unchanged = client.get(url, headers={"If-None-Match": etag})
        assert unchanged.status_code == 304 and unchanged.content == b""
        assert client.get(url, headers={"If-None-Match": f'"other", W/{etag}'}).status_code == 304
        completed = client.get(url + "&status=completed", headers={"If-None-Match": etag})
        assert completed.status_code == 200
        assert [row["id"] for row in completed.json()["jobs"]] == ["completed"]
        assert client.get(url + "&status=completed", headers={"If-None-Match": completed.headers["etag"]}).status_code == 304
        async with factory() as session:
            job = await session.get(Job, "running")
            job.status = "completed"
            job.completed_at = datetime(2026, 1, 3)
            await session.commit()
        changed = client.get(url, headers={"If-None-Match": etag})
        assert changed.status_code == 200 and changed.headers["etag"] != etag
        assert len(client.get(url + "&status=completed", headers={"If-None-Match": completed.headers["etag"]}).json()["jobs"]) == 2
        # Design counts are part of the validator, not merely job timestamps.
        async with factory() as session:
            session.add(Design(id="design", job_id="completed", name="new design", pdb_path="fixture.pdb"))
            await session.commit()
        counted = client.get(url, headers={"If-None-Match": changed.headers["etag"]})
        assert counted.status_code == 200
        assert next(row for row in counted.json()["jobs"] if row["id"] == "completed")["design_count"] == 1
        async with factory() as session:
            await session.delete(await session.get(Job, "running"))
            await session.commit()
        removed = client.get(url, headers={"If-None-Match": counted.headers["etag"]})
        assert removed.status_code == 200 and removed.json()["total"] == 99
        denied = True
        assert client.get(url, headers={"If-None-Match": removed.headers["etag"]}).status_code == 403
        denied = False
        assert client.get("/api/jobs?summary=true&limit=501", headers={"If-None-Match": etag}).status_code == 422
        print(f"conditional-list fixture: {len(first.content)} bytes -> {len(unchanged.content)} bytes unchanged; change/count/removal/filter/auth verified")
    await engine.dispose()
