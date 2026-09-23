"""Mounted BC2 readback route: model-owned results, bounded query and no Designs."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import jobs as jobs_router  # noqa: E402
from services import bindcraft2_result_readback as readback  # noqa: E402
from services.bindcraft2_publication import PublicationError  # noqa: E402


def test_readback_route_pages_and_rejects_unpublished_or_wrong_model(monkeypatch):
    jobs = {
        "bc2": SimpleNamespace(id="bc2", model_id="bindcraft2"),
        "generic": SimpleNamespace(id="generic", model_id="proteinmpnn"),
    }

    class Session:
        async def get(self, _cls, job_id):
            return jobs.get(job_id)

    session = Session()

    async def override_session():
        yield session

    calls = []

    async def page(job, actual_session, **kwargs):
        calls.append((job.id, actual_session, kwargs))
        if kwargs["arm"] == "unpublished":
            raise PublicationError("no receipt")
        return {"schema": "bindcraft2.native-readback.v1", "arm": kwargs["arm"],
                "stage": kwargs["stage"], "offset": kwargs["offset"], "limit": kwargs["limit"],
                "total": 0, "rows": [], "selection": {"eligible": False, "reason": "unmapped"}}

    monkeypatch.setattr(readback, "read_bindcraft2_result_page", page)
    app = FastAPI()
    app.dependency_overrides[jobs_router.get_session] = override_session
    app.include_router(jobs_router.router, prefix="/api/jobs")
    with TestClient(app) as client:
        response = client.get("/api/jobs/bc2/bindcraft2-results?arm=arm_b&stage=attempt&offset=3&limit=12")
        assert response.status_code == 200, response.text
        assert response.json()["stage"] == "attempt"
        assert calls == [("bc2", session, {"arm": "arm_b", "stage": "attempt", "offset": 3, "limit": 12})]
        assert client.get("/api/jobs/absent/bindcraft2-results").status_code == 404
        assert client.get("/api/jobs/generic/bindcraft2-results").status_code == 400
        assert client.get("/api/jobs/bc2/bindcraft2-results?limit=101").status_code == 422
        assert client.get("/api/jobs/bc2/bindcraft2-results?offset=-1").status_code == 422
        assert client.get("/api/jobs/bc2/bindcraft2-results?stage=unknown").status_code == 422
        unavailable = client.get("/api/jobs/bc2/bindcraft2-results?arm=unpublished")
        assert unavailable.status_code == 409
        assert unavailable.json()["detail"] == "Verified native results unavailable"
