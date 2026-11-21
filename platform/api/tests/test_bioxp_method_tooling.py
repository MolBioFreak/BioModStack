"""Outline, small edits and recipes over the real library store (compiler stubbed at its seam)."""
from copy import deepcopy

import pytest

from bioxp_method_outline import apply_edits, EditError, outline, step_line
from test_bioxp_methods_api import BASE, create, store, compiler_stub  # noqa: F401  (fixtures)

METHOD = {"schema": "bms.bioxp-method.v1", "name": "Tooling", "unknown": {"keep": [1, None, "0.10"]},
          "editor_state": {"workflow_pages": [{"id": "setup", "label": "Set up", "step_ids": ["a", "b"]}]},
          "deck_plan": {"labware": [{"id": "lw1", "station": "LOC_OC", "name": "Plate"}],
                        "materials": [{"id": "m1", "name": "Water", "kind": "reagent"}],
                        "assignments": [{"id": "x", "labware_id": "lw1", "well": "A1", "material_id": "m1", "volume_ul": "50"}],
                        "tip_racks": [{"tray": 1, "tip_type": "T10", "tips": {"available": ["A1", "A2"]}}]},
          "steps": [
              {"step_id": "a", "type": "action", "action": "chiller_setpoint", "label": "OC to 10", "inputs": {"bank": "oc", "target_temp_c": 10}},
              {"step_id": "b", "type": "action", "action": "transfer", "label": "Move water",
               "inputs": {"volume_ul": "9", "channels": [0], "source": {"station": "LOC_OC", "wells": ["A1"]},
                          "destination": {"station": "LOC_TC", "wells": ["B1"]}}},
              {"step_id": "g", "type": "group", "label": "Mix", "steps": [
                  {"step_id": "g1", "type": "action", "action": "mix", "inputs": {"volume_ul": 5, "cycles": 3}}]},
              {"step_id": "c", "type": "action", "action": "thermal_profile", "label": "Cycle",
               "inputs": {"repeat": 30, "segments": [{"target_temp_c": 37, "duration_s": 60}, {"target_temp_c": 16, "duration_s": 60}]}}]}


def test_outline_stages_lines_deck_and_tips():
    out = outline(deepcopy(METHOD))
    assert [s["label"] for s in out["stages"]] == ["Set up", "Mix", "Cycle"]
    assert out["stages"][0]["steps"][1]["line"] == "transfer: Move water — 9 µL LOC_OC A1 → LOC_TC B1 ch [0]"
    assert out["stages"][1]["steps"][1] == {"path": "/steps/2/steps/0", "depth": 1, "step_id": "g1", "line": "mix — 5 µL ×3"}
    assert out["stages"][2]["steps"][0]["line"] == "thermal_profile: Cycle — (37°C 60s, 16°C 60s) ×30"
    assert out["deck"][0]["contents"] == [{"well": "A1", "material_id": "m1", "material": "Water", "volume_ul": "50"}]
    assert out["tips"] == [{"tray": 1, "tip_type": "T10", "available": 2}]
    assert "check" not in out


def test_param_inputs_are_named_not_guessed():
    assert step_line({"action": "wait", "inputs": {"expr": {"op": "param", "id": "w"}}}) == "wait — inputs from <param w>"


def test_edits_change_only_addressed_values_and_keep_unknown_fields():
    method, changes = apply_edits(METHOD, [
        {"op": "set", "step": "b", "path": "/inputs/volume_ul", "value": "12"},
        {"op": "insert", "after": "a", "node": {"step_id": "w", "type": "action", "action": "wait", "inputs": {"seconds": 5}}},
        {"op": "move", "step": "c", "before": "a"},
        {"op": "remove", "step": "g1"}])
    assert [s["step_id"] for s in method["steps"]] == ["c", "a", "w", "b", "g"]
    assert method["steps"][3]["inputs"]["volume_ul"] == "12" and method["steps"][4]["steps"] == []
    assert method["unknown"] == METHOD["unknown"] and METHOD["steps"][1]["inputs"]["volume_ul"] == "9"
    assert [c["op"] for c in changes] == ["set", "insert", "move", "remove"]
    assert changes[0]["before"] == "9" and changes[0]["path"] == "/steps/1/inputs/volume_ul"


@pytest.mark.parametrize("edit, message", [
    ({"op": "set", "step": "zz", "path": "/inputs", "value": 1}, "matches 0 steps"),
    ({"op": "insert", "node": {"step_id": "a", "type": "action"}}, "already exists"),
    ({"op": "set", "path": "/schema", "value": "x"}, "schema"),
    ({"op": "move", "step": "a", "after": "a"}, "itself"),
    ({"op": "frob"}, "unknown op")])
def test_bad_edit_rejects_whole_batch(edit, message):
    with pytest.raises(EditError) as error:
        apply_edits(METHOD, [{"op": "set", "step": "a", "path": "/label", "value": "changed"}, edit])
    assert error.value.index == 1 and message in error.value.detail
    assert METHOD["steps"][0]["label"] == "OC to 10"


@pytest.mark.asyncio
async def test_http_outline_edit_saved_revision_and_stale_base(store):
    client, transport, _, _ = store
    saved = await create(client, deepcopy(METHOD))
    read = (await client.get(f"{BASE}/library/{saved['id']}/outline")).json()
    assert read["revision"] == 1 and read["stages"][0]["label"] == "Set up"
    checked = (await client.get(f"{BASE}/library/{saved['id']}/outline", params={"check": "true"})).json()
    assert checked["check"]["document_emitted"] is True and checked["check"]["issue_counts"] == {"advisory": 1}
    body = {"expected_base_revision": 1, "edits": [{"op": "set", "step": "a", "path": "/inputs/target_temp_c", "value": 4}]}
    edited = await client.post(f"{BASE}/library/{saved['id']}/edits", json=body)
    assert edited.status_code == 200, edited.text
    assert edited.json()["revision"] == 2 and edited.json()["method"]["steps"][0]["inputs"]["target_temp_c"] == 4
    assert edited.json()["method"]["unknown"] == METHOD["unknown"]
    stale = await client.post(f"{BASE}/library/{saved['id']}/edits", json=body)
    assert stale.status_code == 409
    bad = await client.post(f"{BASE}/library/{saved['id']}/edits", json={"expected_base_revision": 2, "edits": [{"op": "remove", "step": "nope"}]})
    assert bad.status_code == 422 and bad.json()["detail"]["edit_index"] == 0
    assert (await client.get(f"{BASE}/library/{saved['id']}")).json()["revision"] == 2
    draft = (await client.post(f"{BASE}/edit", json={"method": METHOD, "edits": [{"op": "remove", "step": "c"}]})).json()
    assert [s["step_id"] for s in draft["method"]["steps"]] == ["a", "b", "g"]
    assert len((await client.get(f"{BASE}/library")).json()) == 1
    assert transport.calls == []


@pytest.mark.asyncio
async def test_http_recipes_list_and_build_without_invented_values(store):
    client, transport, _, _ = store
    listed = (await client.get(f"{BASE}/recipes")).json()
    pcr = next(r for r in listed if r["id"] == "pcr")
    assert pcr["questions"] and all("default" not in q for q in pcr["questions"])
    ask = pcr["questions"][0]["id"]
    built = (await client.post(f"{BASE}/recipes/pcr/build", json={"answers": {ask: {"seconds": 5}}, "name": "My PCR"})).json()
    assert built["method"]["name"] == "My PCR" and built["bindings"] == {ask: {"seconds": 5}}
    assert ask not in built["unanswered"] and len(built["unanswered"]) == len(pcr["questions"]) - 1
    assert "saved" not in built and (await client.get(f"{BASE}/library")).json() == []
    refused = await client.post(f"{BASE}/recipes/pcr/build", json={"answers": {"invented": 1}})
    assert refused.status_code == 422
    saved = (await client.post(f"{BASE}/recipes/pcr/build", json={"name": "Saved PCR", "save": True})).json()
    assert saved["saved"]["revision"] == 1 and saved["saved"]["name"] == "Saved PCR"
    assert (await client.post(f"{BASE}/recipes/nope/build", json={})).status_code == 404
    assert transport.calls == []
