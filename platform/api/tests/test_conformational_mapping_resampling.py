from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, ConformationalMappingRecord, ConformationalMappingRequest, Job
from routers import conformational_mapping as cm_router
from routers.conformational_mapping import ResamplingLaunchRequest
from services.conformational_mapping.contracts import canonical_sha256, validate_schema
from services.conformational_mapping.persistence import register_prepared_request
from services.conformational_mapping.resampling import ResamplingError, materialize_resampling_pair, pair_terminal_manifests
from services.conformational_mapping.state_landscape_analysis import derive_state_landscape_analysis_for_request


FIXTURE = Path(__file__).parent / "fixtures" / "conformational_mapping" / "schemas" / "positive" / "all_schemas.json"
TOOL = {"tool_id": "msa", "tool_version": "1", "tool_sha256": "a" * 64, "database_sha256": "b" * 64, "settings_sha256": "c" * 64}


def _snapshot() -> dict:
    """Synthetic complex with two explicitly sequence/feature-bound instances.

    This is not implicit splitting of an admitted shared entity: it is a distinct
    input authority. The original shared-copy authority remains a negative below.
    """
    snapshot = json.loads(FIXTURE.read_text())["cm_complex_snapshot_v1"]
    shared = snapshot["entities"][0]
    snapshot["entities"] = [
        {**shared, "source_entity_id": entity_id, "count": 1,
         "ordered_instance_ids": [instance_id]}
        for entity_id, instance_id in zip(
            ["repeat_prot", "unaffected_prot"], shared["ordered_instance_ids"], strict=True
        )
    ]
    for index, mapping in enumerate(snapshot["instance_mappings"]):
        mapping["source_entity_id"] = snapshot["entities"][index]["source_entity_id"]
        mapping["runtime_entity_id"] = f"runtime-protein-{index}"
        mapping["output_entity_id"] = str(index + 1)
    snapshot["normalized_source_sha256"] = canonical_sha256({
        key: value for key, value in snapshot.items() if key != "normalized_source_sha256"
    })
    validate_schema("cm_complex_snapshot_v1", snapshot)
    return snapshot


def _features(changed: str, *, disabled: bool = False) -> dict:
    # Test-only feature bytes; never scientific execution/acceptance evidence.
    return {"repeat_prot": {} if disabled else {"msa": changed},
            "unaffected_prot": {} if disabled else {"msa": "unchanged"}}


def _handoff(mode: str = "regenerate_mutated_protein_v1") -> dict:
    snapshot = _snapshot()
    return {
        "source_complex_sha256": canonical_sha256(snapshot), "entity_instance_id": "repeat_prot_copy1",
        "sequence_index": 1, "validated_wt": "A", "substitution": "V", "idempotency_key": "d" * 64,
        "feature_policy": {"mode": mode},
        "resampling_settings": {"ordered_seeds": [101, 202], "samples_per_seed": 2},
    }


def _materialize(mode: str = "regenerate_mutated_protein_v1") -> dict:
    disabled = mode == "features_disabled_control_v1"
    return materialize_resampling_pair(
        _snapshot(), _handoff(mode), wt_features=_features("wt", disabled=disabled),
        mutant_features=_features("mut", disabled=disabled), tool_identity=TOOL,
    )


def test_cm10_001_explicit_wt_control() -> None:
    pair = _materialize()
    assert pair["wt_snapshot"]["entities"][0]["sequence"] == "A"
    assert pair["mutant_snapshot"]["entities"][0]["sequence"] == "V"
    assert pair["mutant_snapshot"]["entities"][1] == pair["wt_snapshot"]["entities"][1]
    assert pair["feature_records"][1]["wt_sha256"] == pair["feature_records"][1]["mutant_sha256"]


def test_cm10_002_materialize_from_complex_snapshot() -> None:
    pair = _materialize()
    assert pair["wt_snapshot_sha256"] == canonical_sha256(pair["wt_snapshot"])
    assert pair["mutant_snapshot_sha256"] == canonical_sha256(pair["mutant_snapshot"])


def test_cm10_003_exact_substitution_only() -> None:
    pair = _materialize()
    assert pair["substitution"] == {"entity_instance_id": "repeat_prot_copy1", "sequence_index": 1, "wt": "A", "mutant": "V"}


def test_cm10_004_preserve_entities_copies_bonds_order() -> None:
    pair = _materialize()
    left, right = copy.deepcopy(pair["wt_snapshot"]), copy.deepcopy(pair["mutant_snapshot"])
    left["entities"][0].pop("sequence")
    right["entities"][0].pop("sequence")
    right["normalized_source_sha256"] = left["normalized_source_sha256"]
    assert left == right


def test_cm10_005_match_seed_sample_runtime() -> None:
    pair = _materialize()
    assert pair["expected_coordinates"] == [
        {"ordered_seed": 101, "sample_index": 0}, {"ordered_seed": 101, "sample_index": 1},
        {"ordered_seed": 202, "sample_index": 0}, {"ordered_seed": 202, "sample_index": 1},
    ]


@pytest.mark.parametrize("mode", ["regenerate_mutated_protein_v1", "paired_regenerate_changed_protein_v1"])
def test_cm10_006_mutant_only_regenerate_policy(mode: str) -> None:
    assert _materialize(mode)["feature_records"][0]["declared_difference"] == "regenerated_changed_sequence"


def test_cm10_007_paired_regenerate_policy() -> None:
    assert _materialize("paired_regenerate_changed_protein_v1")["feature_policy"]["mode"] == "paired_regenerate_changed_protein_v1"


def test_cm10_008_features_disabled_control() -> None:
    assert _materialize("features_disabled_control_v1")["feature_records"][0]["declared_difference"] == "disabled_both"


def test_cm10_009_unaffected_feature_bytes_identical() -> None:
    snapshot = _snapshot()
    snapshot["entities"].append({"entity_type": "dna", "source_entity_id": "dna", "count": 1, "ordered_instance_ids": ["dna1"], "sequence": "AC"})
    handoff = _handoff()
    handoff["source_complex_sha256"] = canonical_sha256(snapshot)
    pair = materialize_resampling_pair(snapshot, handoff, wt_features={**_features("wt"), "dna": {"value": 1}}, mutant_features={**_features("mut"), "dna": {"value": 1}}, tool_identity=TOOL)
    assert pair["feature_records"][2]["declared_difference"] == "byte_identical_unaffected"


def test_cm10_010_per_entity_hash_differences_declared() -> None:
    record = _materialize()["feature_records"][0]
    assert record["wt_sha256"] != record["mutant_sha256"]
    assert record["tool_sha256"] == TOOL["tool_sha256"]


def test_cm10_011_manifest_pairing_and_unmatched_status() -> None:
    pair = _materialize()
    candidates = [{"candidate_id": f"w{index}", "backend_coordinates": {"ordered_seed": coordinate["ordered_seed"], "sample_index": coordinate["sample_index"]}} for index, coordinate in enumerate(pair["expected_coordinates"])]
    manifest = pair_terminal_manifests(pair, {"candidates": candidates}, {"candidates": candidates[:-1]})
    assert manifest["matched_cardinality"] == 3
    assert manifest["unmatched"] == [{"coordinate": {"ordered_seed": 202, "sample_index": 1}, "reason": "missing_mutant"}]
    assert manifest["terminal_status"] == "failed"


@pytest.mark.parametrize("mode", ["regenerate_mutated_protein_v1", "paired_regenerate_changed_protein_v1", "features_disabled_control_v1"])
def test_shared_copy_resampling_rejected_without_mutating_input(mode: str) -> None:
    snapshot = json.loads(FIXTURE.read_text())["cm_complex_snapshot_v1"]
    original = copy.deepcopy(snapshot)
    handoff = _handoff(mode)
    handoff["source_complex_sha256"] = canonical_sha256(snapshot)
    with pytest.raises(ResamplingError, match="per-instance sequence and feature binding"):
        materialize_resampling_pair(snapshot, handoff, wt_features=_features("wt"),
                                   mutant_features=_features("mut"), tool_identity=TOOL)
    assert snapshot == original


@pytest.mark.parametrize("invalid", ["stale_source", "reused_changed_features", "changed_unaffected_features", "disabled_nonempty"])
def test_resampling_rejects_undeclared_authority_changes(invalid: str) -> None:
    snapshot, handoff = _snapshot(), _handoff()
    wt_features, mutant_features = _features("wt"), _features("mut")
    if invalid == "stale_source":
        handoff["source_complex_sha256"] = "0" * 64
        message = "source complex is stale"
    elif invalid == "reused_changed_features":
        mutant_features["repeat_prot"] = wt_features["repeat_prot"]
        message = "feature bytes were reused"
    elif invalid == "changed_unaffected_features":
        mutant_features["unaffected_prot"] = {"msa": "changed"}
        message = "unaffected entity feature bytes changed"
    else:
        handoff["feature_policy"] = {"mode": "features_disabled_control_v1"}
        message = "requires empty WT and mutant features"
    original = copy.deepcopy(snapshot)
    with pytest.raises(ResamplingError, match=message):
        materialize_resampling_pair(snapshot, handoff, wt_features=wt_features,
                                   mutant_features=mutant_features, tool_identity=TOOL)
    assert snapshot == original


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_after_registration", [False, True])
async def test_cm10_013_actual_resampling_launch_cannot_authorize_state_analysis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_after_registration: bool) -> None:
    """A real child request plus its pair context is not comparison authority."""

    monkeypatch.setenv("BMS_CM_AUTHORIZATION_ENABLED", "1")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'resampling.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()
    try:
        source_root = tmp_path / "source"
        source_root.mkdir()
        (source_root / "cm_complex_snapshots_v1.json").write_text(json.dumps([_snapshot()]))
        parent_id = "00000000-0000-4000-8000-000000000040"
        parent = await register_prepared_request(
            session,
            job=Job(id=parent_id, name="parent", model_id="conformational_mapping", mode="map", status="queued", params={}, output_dir=str(source_root)),
            principal_id="alice",
            request={
                "request_id": parent_id, "request_sha256": "a" * 64, "backend": "protenix_v2_ensemble",
                "analysis_policy": {
                    "sign_zero_epsilon": 0.000001, "clash_detector_id": "bms_clash", "clash_detector_version": "1",
                    "outer_support_minimum": 0.8, "inner_support_minimum": 0.6, "sign_consistency_minimum": 0.8,
                    "clash_free_minimum": 0.9, "rank_stability_minimum": 0.6, "minimum_common_ranked_universe_size": 3,
                },
            },
            coordinate_plan={"coordinate_plan_sha256": "b" * 64, "expected_cardinality": 1, "coordinates": [{"target_id": "target-a"}]},
            resume_key="0" * 64, capability_sha256="c" * 64,
        )
        handoff = _handoff()
        handoff["feature_policy"] = {
            "mode": "regenerate_mutated_protein_v1",
            "protein_msa_enabled": True,
            "templates_enabled": True,
            "rna_msa_enabled": True,
        }
        handoff.update({"target_id": "target-a", "mutation_set_id": "e" * 64, "mutation_set_string": "A1V"})
        handoff["resampling_settings"]["runtime_policy"] = {"use_default_params": True}
        session.add(ConformationalMappingRecord(
            id="00000000-0000-4000-8000-000000000041", request_id=parent.request_id,
            record_type="handoff", record_key="handoff", content_sha256=canonical_sha256(handoff), payload_json=handoff,
        ))
        await session.commit()
        monkeypatch.setattr(cm_router, "get_results_dir", lambda: tmp_path / "results")
        monkeypatch.setattr(cm_router, "_runtime_registry", lambda _backend: {"test_runtime": True})
        request = Request({"type": "http", "method": "POST", "scheme": "http", "path": "/", "headers": [], "client": ("test", 1), "server": ("test", 80)})
        request.state.authenticated_principal = {"id": "alice", "roles": ["scientist"]}
        body = ResamplingLaunchRequest(
            handoff_key="handoff", wt_features=_features("wt"),
            mutant_features=_features("mut"), tool_identity=TOOL,
        )
        if fail_after_registration:
            async def fail_after_flush(*args, **kwargs):
                await register_prepared_request(*args, **kwargs)
                raise RuntimeError("injected after child registration")

            monkeypatch.setattr(cm_router, "register_prepared_request", fail_after_flush)
            with pytest.raises(HTTPException) as failed:
                await cm_router.launch_resampling(parent_id, body, request, session)
            assert failed.value.status_code == 500
            assert list((tmp_path / "results").iterdir()) == []
            # Both child authorities and pair record must have rolled back.
            assert list((await session.scalars(select(Job.id))).all()) == [parent_id]
            assert list((await session.scalars(select(ConformationalMappingRequest.request_id))).all()) == [parent_id]
            assert list((await session.scalars(select(ConformationalMappingRecord.record_type))).all()) == ["handoff"]
            return
        launched = await cm_router.launch_resampling(parent_id, body, request, session)
        child = await session.scalar(select(ConformationalMappingRequest).where(ConformationalMappingRequest.request_id == launched["request_id"]))
        assert child is not None
        child_root = tmp_path / "results" / f"conformational_mapping_{child.request_id}"
        assert (child_root / "cm_resampling_pair_request_v1.json").is_file()
        produced_request = json.loads((child_root / "cm_request_v1.json").read_text())
        assert "state_landscape_comparison" not in produced_request
        assert derive_state_landscape_analysis_for_request(produced_request, {}, [], []) is None
    finally:
        await session.close()
        await engine.dispose()
