from __future__ import annotations

import httpx
import pytest

from test_project_manager_hierarchy import _app, _project_payload, project_store


@pytest.mark.asyncio
async def test_project_search_is_bounded_filterable_and_cursor_paginated(project_store) -> None:
    _db_path, factory = project_store
    app = _app(factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alpha_payload = {**_project_payload("Alpha"), "owner": "operator", "tags": ["protein"]}
        beta_payload = {**_project_payload("Beta"), "owner": "operator", "tags": ["ngs"]}
        alpha = (await client.post("/api/projects", json=alpha_payload)).json()
        beta = (await client.post("/api/projects", json=beta_payload)).json()

        listed_response = await client.get("/api/projects", params={"limit": 1})
        assert listed_response.status_code == 200, listed_response.text
        listed = listed_response.json()
        assert len(listed["items"]) == 1
        assert listed["next_cursor"]

        first_response = await client.get(
            "/api/projects/search",
            params={"q": "operator", "archive": "active", "limit": 1},
        )
        assert first_response.status_code == 200, first_response.text
        first = first_response.json()
        assert len(first["items"]) == 1
        assert first["next_cursor"]
        assert first["items"][0]["active_experiment_count"] == 0
        assert first["items"][0]["unresolved_failure_count"] == 0

        second_response = await client.get(
            "/api/projects/search",
            params={
                "q": "operator",
                "archive": "active",
                "limit": 1,
                "cursor": first["next_cursor"],
            },
        )
        assert second_response.status_code == 200, second_response.text
        second = second_response.json()
        assert len(second["items"]) == 1
        assert second["next_cursor"] is None
        assert {first["items"][0]["id"], second["items"][0]["id"]} == {alpha["id"], beta["id"]}

        tagged = (await client.get("/api/projects/search", params={"q": "protein"})).json()
        assert [item["id"] for item in tagged["items"]] == [alpha["id"]]

        archived = await client.post(
            f"/api/projects/{alpha['id']}/archive",
            json={"expected_head_generation": alpha["head_generation"]},
        )
        assert archived.status_code == 200, archived.text
        archived_search = (await client.get("/api/projects/search", params={"archive": "archived"})).json()
        assert [item["id"] for item in archived_search["items"]] == [alpha["id"]]
