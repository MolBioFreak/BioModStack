"""Real hierarchy HTTP/SQLite revision reuse; no application lifespan."""
import json
import sqlite3

import httpx
import pytest

from experiment_models import ExperimentAggregateHead, ExperimentRevision
from experiment_services import RevisionConflict, save_hierarchy_revision
from test_project_manager_hierarchy import (
    _app, _project_payload, _experiment_payload, _domain_payload, project_store,
)


@pytest.mark.asyncio
async def test_global_upgrade_keeps_project_and_experiment_identity_order(project_store):
    _path, factory = project_store
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(factory)),
                                base_url="http://test") as client:
        project_url, _project = (await _hierarchy(client))[0]
        legacy = _experiment_payload("Legacy setup group")
        created = await client.post(f"{project_url}/experiments", json=legacy)
        assert created.status_code == 201, created.text
        row = created.json()
        target = f"{project_url}/experiments/{row['id']}"
        request = {**legacy, "schema": "bms.global-experiment.v2",
                   "expected_head_generation": row["head_generation"],
                   "name": "Edited legacy setup group", "change_summary": "UI metadata edit"}
        upgraded = await client.post(f"{target}/upgrade", json=request)
        assert upgraded.status_code == 200, upgraded.text
        result = upgraded.json()
        assert result["id"] == row["id"]
        assert result["project_id"] == _project["id"]
        assert result["payload"]["schema"] == "bms.global-experiment.v2"
        assert result["name"] == request["name"]
        assert (await client.get(target)).json()["current_revision_id"] == result["current_revision_id"]
        repeat = await client.patch(target, json={"expected_head_generation": result["head_generation"],
                                                  "name": result["name"]})
        assert repeat.status_code == 200, repeat.text
        assert repeat.json()["current_revision_id"] == result["current_revision_id"]
        wrong_project = (await _hierarchy(client))[0][0]
        wrong = await client.post(f"{wrong_project}/experiments/{row['id']}/upgrade", json=request)
        assert wrong.status_code == 404, wrong.text


async def _hierarchy(client):
    rows = []
    url = "/api/projects"
    for payload, suffix in [(_project_payload(), "experiments"),
                            (_experiment_payload(), "domains"),
                            (_domain_payload(), None)]:
        if suffix == "experiments":
            payload.update(schema="bms.project.v2", project_scope="global")
        elif suffix == "domains":
            payload.update(schema="bms.global-experiment.v2")
        else:
            payload.pop("dataset_ids")
            payload.pop("created_by")
            payload.update(schema="bms.domain-experiment.v2", domain_contract_version="2",
                           domain_kind="ngs_molbio", dataset_revision_ids=[])
            payload["domain_payload"] = {
                "schema": "bms.ngs-molbio-experiment.v2", "experiment_mode": "analysis",
                "scientific_objective": "Metadata revision fixture", "planned_capability_ids": [],
                "grouping_intent": [], "acceptance_criteria": [], "evidence_plan": [],
            }
        response = await client.post(url, json=payload)
        assert response.status_code == 201, response.text
        row = response.json()
        target = f"{url}/{row['id']}"
        rows.append((target, row))
        url = f"{target}/{suffix}"
    return rows


def _snapshot(path):
    with sqlite3.connect(path) as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in ("revisions", "resources", "revision_edges", "audit_events")}


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [0, 1, 2], ids=["project", "global", "domain"])
async def test_hierarchy_noop_change_revert_and_stale_http(project_store, level):
    path, factory = project_store
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(factory)),
                                base_url="http://test") as client:
        target, original = (await _hierarchy(client))[level]
        before = _snapshot(path)
        for _ in range(2):
            response = await client.patch(target, json={
                "expected_head_generation": original["head_generation"], "name": original["name"],
            })
            assert response.status_code == 200, response.text
            assert response.json()["current_revision_id"] == original["current_revision_id"]
            assert response.json()["head_generation"] == original["head_generation"]
        assert _snapshot(path) == before
        response = await client.patch(target, json={
            "expected_head_generation": original["head_generation"], "name": "Changed name",
        })
        assert response.status_code == 200, response.text
        changed = response.json()
        assert changed["head_generation"] == original["head_generation"] + 1
        assert changed["current_revision_id"] != original["current_revision_id"]
        after_change = _snapshot(path)
        stale = await client.patch(target, json={
            "expected_head_generation": original["head_generation"], "name": "Changed name",
        })
        assert stale.status_code == 409, stale.text
        assert _snapshot(path) == after_change
        reverted = await client.patch(target, json={
            "expected_head_generation": changed["head_generation"], "name": original["name"],
        })
        assert reverted.status_code == 200, reverted.text
        restored = reverted.json()
        assert restored["current_revision_id"] == original["current_revision_id"]
        assert restored["head_generation"] == changed["head_generation"] + 1
        retained = _snapshot(path)
        for table in ("revisions", "resources", "revision_edges"):
            assert retained[table] == after_change[table]
        assert len(retained["audit_events"]) == len(after_change["audit_events"]) + 1
        readback = await client.get(target)
        assert readback.json()["current_revision_id"] == original["current_revision_id"]
        again = await client.patch(target, json={
            "expected_head_generation": restored["head_generation"], "name": original["name"],
        })
        assert again.status_code == 200, again.text
        assert _snapshot(path) == retained
        # Reuse must not recycle generation numbers for subsequent new content.
        fresh = await client.patch(target, json={
            "expected_head_generation": restored["head_generation"], "name": "Third name",
        })
        assert fresh.status_code == 200, fresh.text
        assert fresh.json()["head_generation"] == restored["head_generation"] + 1


@pytest.mark.asyncio
async def test_domain_same_metadata_binds_changed_global_revision(project_store):
    path, factory = project_store
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(factory)),
                                base_url="http://test") as client:
        rows = await _hierarchy(client)
        global_url, global_row = rows[1]
        domain_url, domain = rows[2]
        changed = await client.patch(global_url, json={
            "expected_head_generation": global_row["head_generation"], "name": "New parent",
        })
        assert changed.status_code == 200, changed.text
        saved = await client.patch(domain_url, json={
            "expected_head_generation": domain["head_generation"], "name": domain["name"],
        })
        assert saved.status_code == 200, saved.text
        assert saved.json()["current_revision_id"] != domain["current_revision_id"]
        with sqlite3.connect(path) as db:
            edges = dict(db.execute("SELECT revision_id, target_resource_id FROM revision_edges "
                                    "WHERE role = 'parent_global_revision'"))
        assert edges[domain["current_revision_id"]] == global_row["current_revision_id"]
        assert edges[saved.json()["current_revision_id"]] == changed.json()["current_revision_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("level,kind", [(0, "workspace"), (1, "experiment"), (2, "domain_experiment")])
async def test_noop_reuse_cas_rejects_stale_identity_map(project_store, level, kind):
    _path, factory = project_store
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(factory)),
                                base_url="http://test") as client:
        target, row = (await _hierarchy(client))[level]
        async with factory() as stale_session:
            head = await stale_session.get(ExperimentAggregateHead, row["id"])
            revision = await stale_session.get(ExperimentRevision, head.current_revision_id)
            payload = json.loads(revision.canonical_payload)
            changed = await client.patch(target, json={
                "expected_head_generation": row["head_generation"], "name": "Concurrent name",
            })
            assert changed.status_code == 200, changed.text
            with pytest.raises(RevisionConflict, match="head generation conflict"):
                await save_hierarchy_revision(stale_session, row["id"], kind, payload,
                                              expected_head_generation=row["head_generation"])
            await stale_session.rollback()
        assert (await client.get(target)).json()["current_revision_id"] == changed.json()["current_revision_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [0, 1, 2])
async def test_hierarchy_archive_restore_repeated_cycles(project_store, level):
    path, factory = project_store
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(factory)),
                                base_url="http://test") as client:
        target, row = (await _hierarchy(client))[level]
        for _ in range(2):
            for operation, status in [("archive", "archived"), ("restore", "draft")]:
                response = await client.post(f"{target}/{operation}", json={
                    "expected_head_generation": row["head_generation"],
                })
                assert response.status_code == 200, response.text
                new = response.json()
                assert new["head_generation"] == row["head_generation"] + 1
                assert new["status"] == status
                row = new
        with sqlite3.connect(path) as db:
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.asyncio
async def test_legacy_domain_graph_reuse_preserves_immutable_binding(project_store, monkeypatch):
    import experiment_services as services
    path, factory = project_store
    original_canonical = services.canonical_json

    def legacy_graph(value):
        if isinstance(value, dict) and set(value) == {"nodes", "edges", "references"}:
            value = {**value, "references": [binding for binding in value["references"]
                                             if binding["role"] != "parent_global_revision"]}
        return original_canonical(value)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(factory)),
                                base_url="http://test") as client:
        # Seed exactly the pre-repair graph encoding; no immutable row edits.
        with monkeypatch.context() as patcher:
            patcher.setattr(services, "canonical_json", legacy_graph)
            rows = await _hierarchy(client)
        target, row = rows[2]
        before = _snapshot(path)
        response = await client.patch(target, json={
            "expected_head_generation": row["head_generation"], "name": row["name"],
        })
        assert response.status_code == 200, response.text
        assert response.json()["current_revision_id"] == row["current_revision_id"]
        assert _snapshot(path) == before
        parent_url, parent = rows[1]
        changed_parent = await client.patch(parent_url, json={
            "expected_head_generation": parent["head_generation"], "name": "New Global",
        })
        assert changed_parent.status_code == 200, changed_parent.text
        response = await client.patch(target, json={
            "expected_head_generation": row["head_generation"], "name": row["name"],
        })
        assert response.status_code == 200, response.text
        assert response.json()["current_revision_id"] != row["current_revision_id"]
