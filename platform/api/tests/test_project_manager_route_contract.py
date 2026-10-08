from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from routers import project_manager
from routers.projects import router


def test_domain_activity_uses_the_frozen_singular_route() -> None:
    paths = {route.path for route in router.routes if isinstance(route, APIRoute)}
    assert "/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/activity" in paths
    assert "/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/activities" not in paths


@pytest.mark.asyncio
async def test_attach_route_forwards_expected_project_generation(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_attach(*_args, **kwargs):
        captured.update(kwargs)
        return {"source_receipt_id": "receipt-1"}

    class FakeSession:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    monkeypatch.setattr(project_manager, "attach_verified_entity", fake_attach)
    payload = project_manager.AttachRequest(
        adapter_id="adapter-1",
        entity_id="entity-1",
        role="references",
        expected_head_generation=7,
    )
    response = await project_manager.attach_domain_entity(
        "project-1",
        "global-1",
        "domain-1",
        payload,
        FakeSession(),
        FakeSession(),
    )

    assert response == {"source_receipt_id": "receipt-1"}
    assert captured["expected_head_generation"] == 7
