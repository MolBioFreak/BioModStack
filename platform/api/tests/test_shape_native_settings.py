from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from services import shape_native_settings as native
from services import shape_requests as requests
from test_shape_submission import CUBE_OBJ, _database


def submitted(**kwargs):
    return requests.SubmittedShapeRequest(**{
        "client_request_id": "7efcaea8-4d75-4ca0-8e60-e3c834740983",
        "name": "shape-settings", "geometry_id": "geom_" + "1" * 32,
        "expected_geometry_sha256": "1" * 64,
        "expected_geometry_manifest_sha256": "2" * 64,
        "expected_point_pool_sha256": "3" * 64, "target_length": 100, **kwargs})


def test_discovery_projects_native_caliby_without_generated_bindings_or_count():
    definition = requests.sequence_settings_definition("caliby_experimental", 3)
    assert definition["mode"] == "ensemble_design"
    assert definition["initial_values"]["omit_aas"] == ["C"]
    assert set(definition["json_schema"]["properties"]) == set(native.EnsembleDesign.model_fields) - {
        "task", "schema_version", "ensembles", "num_seqs_per_pdb"}
    assert set(definition["input_settings_schema"]["properties"]) == set(native.Conformer.model_fields) - {"path", "state_id"}
    effective, inputs, _ = native.resolve_sequence("caliby_experimental", {
        "omit_aas": [], "verbose": False, "gaussian_noise_std": 0.0}, 3,
        {"fixed_pos_seq": "", "symmetry_pos": "A1:A2"})
    assert effective["omit_aas"] == [] and effective["verbose"] is False
    assert inputs["fixed_pos_seq"] == ""
    with pytest.raises(ValidationError):
        native.resolve_sequence("caliby_experimental", {"num_seqs_per_pdb": 2}, 3)
    with pytest.raises(ValidationError):
        native.resolve_sequence("caliby_experimental", {}, 3, {"path": "/foreign.pdb"})


def test_context_defaults_are_editable_and_omission_does_not_replace_native_seed():
    definition = requests.sequence_settings_definition("fampnn", 3)
    initial = definition["initial_values"]
    assert initial["fampnn_batch_size"] == 3
    assert initial["fampnn_seq_only"] is True
    assert initial["fampnn_repack_last"] is False
    assert initial["fampnn_exclude_cys"] is False
    assert initial["fampnn_checkpoint"] == "fampnn_0_3.pt"
    assert initial["fampnn_presort_by_length"] is False
    assert "fampnn_seed" not in initial
    effective, _, _ = native.resolve_sequence("fampnn", {
        "fampnn_batch_size": 1, "fampnn_seed": 0, "fampnn_psce_threshold": None,
        "fampnn_checkpoint": "fampnn_0_0.pt", "fampnn_seq_only": False}, 3)
    assert effective["fampnn_seed"] == 0 and effective["fampnn_psce_threshold"] is None
    assert effective["fampnn_batch_size"] == 1 and effective["fampnn_seq_only"] is False
    assert effective["fampnn_checkpoint"] == "fampnn_0_0.pt"


@pytest.mark.parametrize("settings", [{"fampnn_num_steps": True}, {"fampnn_num_steps": "30"},
                                     {"fampnn_scn_timestep_mode": "made-up"}, {"input_pdb": "/foreign"},
                                     {"seqs_per_design": 2}])
def test_real_model_validation_rejects_bad_or_adapter_owned_sequence_values(settings):
    with pytest.raises(ValueError):
        native.resolve_sequence("fampnn", settings, 2)


def test_historical_fa_aliases_migrate_before_defaults_without_losing_false_or_zero():
    effective, _, _ = native.resolve_sequence("fampnn", {
        "fampnn_extra_config": "seed=9 repack_last=true psce_threshold=null",
        "fampnn_seed": 0, "fampnn_repack_last": False}, 2)
    assert effective["fampnn_seed"] == 0
    assert effective["fampnn_repack_last"] is False
    assert effective["fampnn_psce_threshold"] is None
    assert "fampnn_extra_config" not in effective
    with pytest.raises(ValueError):
        native.resolve_sequence("fampnn", {"fampnn_extra_config": "invented=3"}, 2)


def test_native_request_preserves_defaults_and_rejects_inactive_or_unknown_settings():
    request = submitted()
    assert request.num_backbones == 4 and request.sequences_per_backbone == 0
    assert request.sequence_policy == "auto"
    rfd3, validators, _ = native.resolve_native({}, {}, request.validator_suite, 42)
    assert rfd3["num_timesteps"] == 200
    assert validators["esmfold2"]["seed"] == 42
    assert validators["protenix_v2"]["protenix_seeds"] == "42"
    assert validators["boltz2"]["boltz_sampling_steps"] == 50
    assert validators["protenix_v2"]["protenix_use_msa"] is False
    for kwargs in [
        {"rfd3_settings": {"num_timesteps": True}},
        {"rfd3_settings": {"shape_step_size": 0.2}},
        {"validator_suite": (), "validator_settings": {"boltz2": {}}},
        {"validator_settings": {"esmfold2": {"num_diffusion_samples": 0}}},
        {"sequence_input_settings": {"fixed_pos_seq": "A1"}},
        {"sequence_policy": "skip", "sequences_per_backbone": 1},
    ]:
        with pytest.raises(ValueError):
            submitted(**kwargs)


def test_all_discovered_defaults_resolve_at_real_owners():
    discovery = requests.shape_settings_definition()
    assert discovery["schema"] == "bms_shape_settings_v1"
    for engine in discovery["sequence_engines"]:
        definition = requests.sequence_settings_definition(engine, 2)
        effective, _, _ = native.resolve_sequence(engine, definition["initial_values"], 2)
        assert effective == definition["initial_values"]
    native.resolve_native(discovery["rfd3"]["initial_values"], {
        key: value["initial_values"] for key, value in discovery["validators"].items()},
        tuple(discovery["validators"]), 0)


@pytest.mark.asyncio
async def test_v3_real_materialization_retains_intent_effective_and_placement_independence(tmp_path):
    from services.shape_resources import admit_obj_geometry
    database, engine, factory = await _database(tmp_path)
    try:
        async with factory() as session:
            geometry = await admit_obj_geometry(session, data_root=tmp_path / "data", payload=CUBE_OBJ,
                                               filename="cube.obj", angstrom_per_unit=10.0)
            request = submitted(
                geometry_id=geometry.geometry_id, expected_geometry_sha256=geometry.geometry_sha256,
                expected_geometry_manifest_sha256=geometry.manifest["manifest_sha256"],
                expected_point_pool_sha256=geometry.point_pool_sha256,
                sequence_policy="external", sequence_engine="caliby_experimental", sequences_per_backbone=2,
                sequence_settings={"omit_aas": [], "potts_rejection_step": False},
                sequence_input_settings={"fixed_pos_seq": ""},
                rfd3_settings={"num_timesteps": 100, "cfg_t_max": None},
                validator_settings={"esmfold2": {"num_diffusion_samples": 2}},
                launch_context_id="context-one")
            staged = await requests.materialize_shape_request(session, data_root=tmp_path / "data", submitted=request)
            payload = json.loads((Path(staged.stage_dir) / "request.json").read_bytes())
            assert payload["schema"] == "bms_shape_design_request_v3"
            assert payload["requested_sequence_settings"] == request.sequence_settings
            assert payload["sequence_settings"]["omit_aas"] == []
            assert payload["sequence_settings"]["temperature"] == 0.1
            assert payload["requested_rfd3_settings"] == request.rfd3_settings
            assert payload["rfd3_settings"]["gamma_0"] == 0.6
            assert payload["validator_settings"]["esmfold2"]["num_diffusion_samples"] == 2
            assert "launch_context_id" not in payload
            assert staged.launch_params["shape_sequence_input_settings"]["fixed_pos_seq"] == ""
            assert requests.shape_job_request(staged, request).launch_context_id == "context-one"
            before = (Path(staged.stage_dir) / "request.json").read_bytes()
            replay = await requests.materialize_shape_request(session, data_root=tmp_path / "data",
                submitted=request.model_copy(update={"launch_context_id": "context-two", "execution_target_id": "remote"}))
            assert replay.request_sha256 == staged.request_sha256
            assert (Path(staged.stage_dir) / "request.json").read_bytes() == before
            with pytest.raises(requests.ShapeRequestError, match="different scientific intent"):
                await requests.materialize_shape_request(session, data_root=tmp_path / "data",
                    submitted=request.model_copy(update={"rfd3_settings": {"num_timesteps": 101}}))
    finally:
        await engine.dispose()


def test_historical_replay_uses_sealed_intent_not_current_expanded_defaults():
    old = {"schema": "bms_shape_design_request_v2", "seed": 42,
           "requested_sequence_settings": {"mpnn_temperature": 0.2},
           "sequence_settings": {"mpnn_temperature": 0.2},
           "sequence_settings_identity": {"schema_sha256": "old-definition"}}
    digest = hashlib.sha256(requests._canonical_json(old)).hexdigest()
    old["request_sha256"] = digest
    row = SimpleNamespace(request_spec=old, request_sha256=digest)
    current = {**deepcopy(old), "schema": "bms_shape_design_request_v3",
               "sequence_settings": {"mpnn_temperature": 0.2, "new-default": 5},
               "sequence_settings_identity": {"schema_sha256": "new-definition"},
               "requested_rfd3_settings": {}, "requested_validator_settings": {},
               "requested_sequence_input_settings": {}}
    assert requests._historical_v2_replay(row, current)
    assert not requests._historical_v2_replay(row, {**current, "seed": 43})
    assert not requests._historical_v2_replay(row, {**current, "requested_rfd3_settings": {"num_timesteps": 200}})
    assert not requests._historical_v2_replay(row, {**current, "requested_sequence_settings": {}})
    row.request_spec["seed"] = 9
    assert not requests._historical_v2_replay(row, current)
    earliest = {"schema": "bms_shape_design_request_v2", "seed": 42}
    old_digest = hashlib.sha256(requests._canonical_json(earliest)).hexdigest()
    earliest["request_sha256"] = old_digest
    earliest_row = SimpleNamespace(request_spec=earliest, request_sha256=old_digest)
    assert not requests._historical_v2_replay(earliest_row, current)


@pytest.mark.asyncio
async def test_typed_files_are_snapshotted_and_replay_without_originals(tmp_path):
    from services.shape_resources import admit_obj_geometry
    from scripts.lib.portable_inputs import resolve_input_path
    import shutil
    database, engine, factory = await _database(tmp_path)
    root = tmp_path / "data"
    root.mkdir()
    source = root / "bias.json"
    source.write_text('{"A": 0.25}')
    try:
        async with factory() as session:
            geometry = await admit_obj_geometry(session, data_root=root, payload=CUBE_OBJ,
                                               filename="cube.obj", angstrom_per_unit=10.0)
            request = submitted(
                geometry_id=geometry.geometry_id, expected_geometry_sha256=geometry.geometry_sha256,
                expected_geometry_manifest_sha256=geometry.manifest["manifest_sha256"],
                expected_point_pool_sha256=geometry.point_pool_sha256, sequences_per_backbone=1,
                sequence_settings={"mpnn_bias_AA_jsonl": str(source)})
            staged = await requests.materialize_shape_request(session, data_root=root, submitted=request)
            document = Path(staged.stage_dir) / "request.json"
            original_bytes = document.read_bytes()
            payload = json.loads(original_bytes)
            relative = payload["sequence_settings"]["mpnn_bias_AA_jsonl"]
            assert not Path(relative).is_absolute()
            assert payload["requested_sequence_settings"]["mpnn_bias_AA_jsonl"] == str(source)
            assert resolve_input_path(relative, owner=document).read_bytes() == source.read_bytes()
            source.unlink()
            replay = await requests.materialize_shape_request(session, data_root=root, submitted=request)
            assert replay.request_sha256 == staged.request_sha256
            assert document.read_bytes() == original_bytes
            returned = tmp_path / "returned"
            shutil.copytree(Path(staged.stage_dir), returned)
            assert resolve_input_path(relative, owner=returned / "request.json").read_text() == '{"A": 0.25}'
            bound_file = document.parent / relative
            bound_file.chmod(0o640)
            bound_file.write_text('{"A": 0.5}')
            with pytest.raises(requests.ShapeRequestError, match="hash mismatch"):
                await requests.materialize_shape_request(session, data_root=root, submitted=request)
    finally:
        await engine.dispose()



@pytest.mark.asyncio
async def test_historical_v2_database_replay_never_upgrades_retained_bytes(tmp_path):
    from services.shape_resources import admit_obj_geometry, _publish
    database, engine, factory = await _database(tmp_path)
    root = tmp_path / "data"
    try:
        async with factory() as session:
            geometry = await admit_obj_geometry(session, data_root=root, payload=CUBE_OBJ,
                                               filename="cube.obj", angstrom_per_unit=10.0)
            request = submitted(
                geometry_id=geometry.geometry_id, expected_geometry_sha256=geometry.geometry_sha256,
                expected_geometry_manifest_sha256=geometry.manifest["manifest_sha256"],
                expected_point_pool_sha256=geometry.point_pool_sha256, sequences_per_backbone=1)
            current = await requests.materialize_shape_request(session, data_root=root, submitted=request)
            # Build an independently published historical fixture, not a mutable
            # upgrade of the current request or a mock of materialization.
            old = json.loads((Path(current.stage_dir) / "request.json").read_bytes())
            for key in ("request_sha256", "native_input_sources", "native_settings_identity",
                        "rfd3_settings", "requested_rfd3_settings", "sequence_input_settings",
                        "requested_sequence_input_settings", "validator_settings", "requested_validator_settings"):
                old.pop(key)
            old_id = "5ee8eaea-0d3d-4ad9-8d0f-1c25f8c34cbb"
            old["request_id"] = "shape_" + old_id
            old["schema"] = "bms_shape_design_request_v2"
            legacy_keys = {"mpnn_temperature", "mpnn_omitAAs", "mpnn_checkpoint_type", "mpnn_checkpoint_model", "mpnn_backbone_noise"}
            old["sequence_settings"] = {key: value for key, value in old["sequence_settings"].items() if key in legacy_keys}
            old["sequence_settings_identity"]["initial_values"] = dict(old["sequence_settings"])
            old["sequence_settings_identity"]["schema_sha256"] = "1" * 64
            digest = hashlib.sha256(requests._canonical_json(old)).hexdigest()
            old["request_sha256"] = digest
            stage_relative = "requests/" + old["request_id"]
            document = root / "shape_blueprint" / stage_relative / "request.json"
            encoded = requests._canonical_json(old)
            _publish(document, encoded)
            row = database.ShapeDesignRequest(request_id=old["request_id"], geometry_id=geometry.geometry_id,
                request_sha256=digest, request_spec=old, stage_relative_path=stage_relative, job_id=None)
            session.add(row)
            await session.commit()
            replay = await requests.materialize_shape_request(session, data_root=root,
                submitted=request.model_copy(update={"client_request_id": old_id}))
            assert replay.request_sha256 == digest
            assert document.read_bytes() == encoded
            assert "shape_rfd3_settings" not in replay.launch_params
            assert replay.launch_params["shape_sequence_settings"] == old["sequence_settings"]
            persisted = await session.get(database.ShapeDesignRequest, old["request_id"])
            assert persisted.request_spec == old
    finally:
        await engine.dispose()
