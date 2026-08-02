from __future__ import annotations

import json
import math
import os
import sys
import hashlib
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from model_registry import ModelRegistry  # noqa: E402
from routers import jobs as jobs_router  # noqa: E402
from routers import conformational_mapping as cm_router  # noqa: E402
from schemas import JobCreate  # noqa: E402
from services import nextflow  # noqa: E402
from services.conformational_mapping import request_builder  # noqa: E402
from services.conformational_mapping.contracts import (  # noqa: E402
    canonical_json_bytes,
    canonical_sha256,
    validate_schema,
)
from services.conformational_mapping.request_builder import (  # noqa: E402
    bind_materialized_source_snapshot,
    ConformationalMappingRequestError,
    build_confornets_coordinate_plan,
    MaterializedRequest,
    materialize_trusted_internal_request,
    validate_request_params,
)
from services.conformational_mapping.state_landscape_analysis import (  # noqa: E402
    MAX_STATE_LANDSCAPE_COMPARISONS,
)
from database import Base, ConformationalMappingRequest, ConformationalMappingSource  # noqa: E402
from routers.conformational_mapping import SubmitRequest  # noqa: E402
from template_registry import TemplateRegistry  # noqa: E402


BACKENDS = ("protenix_v2_ensemble", "confornets", "external_import")


def test_server_confornets_identity_matches_the_executed_upstream_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "confornets-canonical.sif"
    image.write_bytes(b"canonical-confornets")
    monkeypatch.setattr(cm_router, "get_container_dir", lambda: tmp_path)

    identity = cm_router._server_confornets_identity()

    assert identity["backend_version"] == "canonical-4df561a"
    assert identity["backend_commit"] == "4df561a1fbd0fd2b9c7a230fa62957a837d9f72d"
    assert identity["container_digest"] == f"sha256:{hashlib.sha256(image.read_bytes()).hexdigest()}"


def test_snapshot_reseal_rejects_invalid_existing_request_and_plan_authority(tmp_path: Path) -> None:
    request_materialized = materialize_trusted_internal_request(
        _request_params("external_import"),
        output_dir=tmp_path / "request",
        request_id="00000000-0000-4000-8000-000000000701",
    )
    request = json.loads(request_materialized.request_path.read_text())
    request["request_sha256"] = "0" * 64
    request_materialized.request_path.write_bytes(canonical_json_bytes(request))
    with pytest.raises(ConformationalMappingRequestError, match="request_sha256"):
        bind_materialized_source_snapshot(
            request_materialized, source_snapshot_sha256="a" * 64,
        )

    plan_materialized = materialize_trusted_internal_request(
        _request_params("external_import"),
        output_dir=tmp_path / "plan",
        request_id="00000000-0000-4000-8000-000000000702",
    )
    plan = json.loads(plan_materialized.coordinate_plan_path.read_text())
    plan["request_id"] = "00000000-0000-4000-8000-000000000799"
    plan["coordinate_plan_sha256"] = canonical_sha256({
        key: value for key, value in plan.items() if key != "coordinate_plan_sha256"
    })
    plan_materialized.coordinate_plan_path.write_bytes(canonical_json_bytes(plan))
    with pytest.raises(ConformationalMappingRequestError, match="trusted authority"):
        bind_materialized_source_snapshot(plan_materialized, source_snapshot_sha256="a" * 64)

    coordinate_materialized = materialize_trusted_internal_request(
        _request_params("external_import"),
        output_dir=tmp_path / "coordinates",
        request_id="00000000-0000-4000-8000-000000000703",
    )
    coordinate_plan = json.loads(coordinate_materialized.coordinate_plan_path.read_text())
    coordinate_plan["coordinates"][0]["staged_index"] = 99
    coordinate_plan["coordinate_plan_sha256"] = canonical_sha256({
        key: value for key, value in coordinate_plan.items() if key != "coordinate_plan_sha256"
    })
    coordinate_materialized.coordinate_plan_path.write_bytes(canonical_json_bytes(coordinate_plan))
    with pytest.raises(ConformationalMappingRequestError, match="trusted authority"):
        bind_materialized_source_snapshot(
            coordinate_materialized, source_snapshot_sha256="a" * 64,
        )

    for suffix, request_id, mutation in (
        (
            "extra", "00000000-0000-4000-8000-000000000704",
            lambda coordinate: coordinate.__setitem__("unexpected", "value"),
        ),
        (
            "source", "00000000-0000-4000-8000-000000000705",
            lambda coordinate: coordinate.__setitem__("source_content_sha256", "f" * 64),
        ),
    ):
        tampered = materialize_trusted_internal_request(
            _request_params("external_import"),
            output_dir=tmp_path / suffix,
            request_id=request_id,
        )
        tampered_plan = json.loads(tampered.coordinate_plan_path.read_text())
        mutation(tampered_plan["coordinates"][0])
        tampered_plan["coordinate_plan_sha256"] = canonical_sha256({
            key: value for key, value in tampered_plan.items() if key != "coordinate_plan_sha256"
        })
        tampered.coordinate_plan_path.write_bytes(canonical_json_bytes(tampered_plan))
        with pytest.raises(ConformationalMappingRequestError, match="trusted authority"):
            bind_materialized_source_snapshot(tampered, source_snapshot_sha256="a" * 64)


@pytest.mark.parametrize(
    ("keep_old_plan_binding", "add_coordinate_field", "message"),
    [
        (True, False, "request and coordinate plan are not bound"),
        (False, True, "coordinate plan does not match request authority"),
    ],
)
def test_snapshot_reseal_fully_resigned_replacement_reaches_cross_contract_checks(
    tmp_path: Path,
    keep_old_plan_binding: bool,
    add_coordinate_field: bool,
    message: str,
) -> None:
    materialized = materialize_trusted_internal_request(
        _request_params("external_import"),
        output_dir=tmp_path / ("binding" if keep_old_plan_binding else "coordinate"),
        request_id=(
            "00000000-0000-4000-8000-000000000706"
            if keep_old_plan_binding else "00000000-0000-4000-8000-000000000707"
        ),
    )
    request = json.loads(materialized.request_path.read_text(encoding="utf-8"))
    plan = json.loads(materialized.coordinate_plan_path.read_text(encoding="utf-8"))
    old_request_sha256 = request["request_sha256"]

    request["analysis_policy"]["outer_support_minimum"] = 0.75
    request["request_sha256"] = canonical_sha256({
        key: value for key, value in request.items() if key != "request_sha256"
    })
    if not keep_old_plan_binding:
        plan["request_sha256"] = request["request_sha256"]
    else:
        assert plan["request_sha256"] == old_request_sha256
    if add_coordinate_field:
        plan["coordinates"][0]["unexpected"] = "fully-resigned"
    plan["coordinate_plan_sha256"] = canonical_sha256({
        key: value for key, value in plan.items() if key != "coordinate_plan_sha256"
    })
    materialized.request_path.write_bytes(canonical_json_bytes(request))
    materialized.coordinate_plan_path.write_bytes(canonical_json_bytes(plan))
    replacement = MaterializedRequest(
        request_path=materialized.request_path,
        coordinate_plan_path=materialized.coordinate_plan_path,
        launch_params=materialized.launch_params,
        request_sha256=request["request_sha256"],
        coordinate_plan_sha256=plan["coordinate_plan_sha256"],
    )

    with pytest.raises(ConformationalMappingRequestError, match=message):
        bind_materialized_source_snapshot(replacement, source_snapshot_sha256="a" * 64)


def _request_params(backend: str = "protenix_v2_ensemble") -> dict[str, object]:
    params: dict[str, object] = {
        "backend": backend,
        "targets": [{"target_id": "target-a", "target_order": 0}],
        "ordered_seeds": [101, 202, 303, 404, 505],
        "generated_json_ordered_seeds": [101, 202, 303, 404, 505],
        "cli_ordered_seeds": [101, 202, 303, 404, 505],
        "samples_per_seed": 5,
        "feature_policy": {
            "mode": "regenerate_mutated_protein_v1",
            "protein_msa_enabled": True,
            "templates_enabled": True,
            "rna_msa_enabled": True,
        },
        "runtime_policy": {"use_default_params": True},
        "analysis_policy": {
            "sign_zero_epsilon": 0.000001,
            "clash_detector_id": "bms_clash",
            "clash_detector_version": "1",
            "outer_support_minimum": 0.8,
            "inner_support_minimum": 0.6,
            "sign_consistency_minimum": 0.8,
            "clash_free_minimum": 0.9,
            "rank_stability_minimum": 0.6,
            "minimum_common_ranked_universe_size": 3,
        },
    }
    if backend == "protenix_v2_ensemble":
        params["protenix_snapshot_id"] = "snapshot-7"
    elif backend == "external_import":
        params["import_receipt_id"] = "9" * 64
        params["resolved_import_entries"] = [
            {
                "staged_index": 0,
                "source_content_sha256": "8" * 64,
            }
        ]
    elif backend == "confornets":
        params["ordered_seeds"] = [101]
        params["generated_json_ordered_seeds"] = [101]
        params["cli_ordered_seeds"] = [101]
        params["samples_per_seed"] = 3
        params["targets"] = [
            {
                "target_id": "target-a",
                "target_order": 0,
                "sequence": "MKTIIALSYIFCLVFADYKDDDDA",
                "molecule_type": "protein",
                "chain_count": 1,
            }
        ]
        params["confornets"] = {
            "sequence": "MKTIIALSYIFCLVFADYKDDDDA",
            "chain_id": "A",
            "task": "diversity",
            "test_case_id": "monomer-case",
            "benchmark_name": "bms-confornets",
            "references": [],
            "runs": 2,
            "saved_steps": [5, 10],
            "confornet_count": 2,
            "samples": 3,
            "max_steps": 10,
            "num_recycles": 0,
            "num_diffusion_steps": 20,
            "learning_rate": 0.001,
            "gradient_clip": 10.0,
            "skip_msa": True,
            "compute_confidence": False,
            "save_full_confidence": False,
            "compute_evaluation": False,
            "checkpoint": {"path": "/opt/confornets/checkpoint.pt", "sha256": "b" * 64},
            "config": None,
            "transfer_source": None,
            "backend_identity": {
                "backend_version": "0.1",
                "backend_commit": "cba896f556354c2e8ce8090312cc4649185f5612",
                "runtime_identity": "python3-confornets",
                "container_digest": "sha256:" + "c" * 64,
                "model_id": "confornets",
                "feature_identity_sha256": "d" * 64,
                "repo_path": "/opt/confornets",
            },
        }
    return params


def _flag_value(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


def _configure_state_comparison_candidate_count(
    params: dict[str, object], *, mode: str, candidate_count: int
) -> None:
    seeds = list(range(1, candidate_count + 1))
    params["ordered_seeds"] = seeds
    params["generated_json_ordered_seeds"] = seeds
    params["cli_ordered_seeds"] = seeds
    params["samples_per_seed"] = 1
    authority: dict[str, object] = {
        "mode": mode,
        "target_id": "target-a",
        "scope": "all_within_target" if mode == "pairwise" else "all_other_within_target",
    }
    if mode == "reference":
        authority["reference_backend_coordinates"] = {
            "backend": "protenix_v2_ensemble",
            "target_id": "target-a",
            "ordered_seed": 1,
            "sample_index": 0,
        }
    params["state_landscape_comparison"] = authority


def test_cm3_001_model_and_template_discoverable() -> None:
    model = ModelRegistry().get_model("conformational_mapping")
    template = TemplateRegistry(API_ROOT / "config" / "templates").get_template(
        "conformational_mapping"
    )

    assert model is not None
    assert model.workflow == "conformational_mapping"
    assert {mode.id for mode in model.modes} == {"map"}
    assert {param.name for param in model.params} >= {
        "backend",
        "targets",
        "ordered_seeds",
        "samples_per_seed",
        "feature_policy",
        "runtime_policy",
        "analysis_policy",
        "confornets",
    }
    assert template is not None
    assert template.preset_params == {
        "template_model_id": "conformational_mapping",
        "template_mode_id": "map",
        "workflow_model_topic": "conformational_mapping",
    }


def test_cm3_002_launcher_exposes_only_typed_contract_controls() -> None:
    template = TemplateRegistry(API_ROOT / "config" / "templates").get_template(
        "conformational_mapping"
    )
    assert template is not None
    launcher_controls = {param.name for param in template.user_params}
    assert launcher_controls >= {
        "backend",
        "ordered_seeds",
        "samples_per_seed",
        "runtime_policy",
        "analysis_policy",
    }
    assert launcher_controls.isdisjoint(
        {"source", "created_by", "path", "staged_path", "runtime_identity"}
    )
    assert "typed" in template.status.lower()
    model = ModelRegistry().get_model("conformational_mapping")
    assert model is not None
    raw_controls = {
        "targets",
        "ordered_seeds",
        "feature_policy",
        "runtime_policy",
        "analysis_policy",
        "confornets",
    }
    assert all(
        param.hidden for param in model.params if param.name in raw_controls
    )
    assert {param.name for param in model.params}.isdisjoint({"source", "created_by"})


def test_cm3_003_matrix_routes_canonical_entrypoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nextflow, "resolve_nextflow_executable", lambda: "/usr/bin/nextflow")

    for backend in BACKENDS:
        materialized = materialize_trusted_internal_request(
            _request_params(backend),
            output_dir=tmp_path / backend,
            request_id=f"00000000-0000-4000-8000-00000000000{BACKENDS.index(backend) + 1}",
        )
        command = nextflow.build_nextflow_command(
            "conformational_mapping",
            "map",
            materialized.launch_params,
            str(tmp_path / backend),
            job_id=f"cm-{backend}",
        )

        assert command[1:4] == ["run", "workflows/conformational_mapping.nf", "-profile"]
        assert _flag_value(command, "-profile") == (
            "conformational_mapping,workstation_ryzen7960x"
        )
        assert _flag_value(command, "--cm_request_path") == str(materialized.request_path)


def test_cm3_004_cm_namespace_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nextflow, "resolve_nextflow_executable", lambda: "/usr/bin/nextflow")
    materialized = materialize_trusted_internal_request(
        _request_params("confornets"),
        output_dir=tmp_path,
        request_id="00000000-0000-4000-8000-000000000004",
    )

    request = json.loads(materialized.request_path.read_text(encoding="utf-8"))
    validate_schema("cm_request_v1", request)
    assert materialized.request_path.read_bytes() == canonical_json_bytes(request)
    assert request["request_sha256"] == canonical_sha256(
        {key: value for key, value in request.items() if key != "request_sha256"}
    )
    assert request["confornets"] == _request_params("confornets")["confornets"]
    assert request["source"]["kind"] == "api_submission_v1"
    assert request["created_by"] == {"principal_id": "biomodstack-api"}
    assert not list(tmp_path.glob("*.tmp"))

    plan = json.loads(materialized.coordinate_plan_path.read_text(encoding="utf-8"))
    direct_plan = build_confornets_coordinate_plan(
        _request_params("confornets")["confornets"],  # type: ignore[arg-type]
        target_id="target-a",
    )
    assert plan["expected_cardinality"] == 24
    assert len(plan["coordinates"]) == 24
    assert plan["coordinates"] == direct_plan
    assert set(plan["coordinates"][0]) == {
        "backend",
        "target_id",
        "task",
        "test_case_id",
        "reference_id",
        "run_index",
        "saved_step",
        "confornet_index",
        "sample_index",
    }
    assert {row["reference_id"] for row in plan["coordinates"]} == {None}
    assert plan["request_sha256"] == request["request_sha256"]
    assert plan["coordinate_plan_sha256"] == canonical_sha256(
        {key: value for key, value in plan.items() if key != "coordinate_plan_sha256"}
    )

    command = nextflow.build_nextflow_command(
        "conformational_mapping",
        "map",
        materialized.launch_params,
        str(tmp_path),
        job_id="cm-normalized",
    )
    forwarded_flags = {token for token in command if token.startswith("--")}
    assert forwarded_flags == {"--out_dir", "--job_id", "--cm_request_path"}
    assert not any(flag.startswith("--cn_") for flag in forwarded_flags)
    assert "--ordered_seeds" not in forwarded_flags
    assert "--generated_json_ordered_seeds" not in forwarded_flags
    assert "--cli_ordered_seeds" not in forwarded_flags


def test_cm3_004a_second_publication_failure_leaves_no_new_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request_path = tmp_path / "cm_request_v1.json"
    plan_path = tmp_path / "cm_coordinate_plan_v1.json"
    real_replace = os.replace
    failed = False

    def fail_plan_once(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        nonlocal failed
        if Path(destination) == plan_path and not failed:
            failed = True
            raise OSError("injected second publication failure")
        real_replace(source, destination)

    monkeypatch.setattr(request_builder.os, "replace", fail_plan_once)
    with pytest.raises(OSError, match="second publication"):
        materialize_trusted_internal_request(
            _request_params("confornets"),
            output_dir=tmp_path,
            request_id="00000000-0000-4000-8000-000000000401",
        )
    assert not request_path.exists()
    assert not plan_path.exists()


def test_cm3_004aa_second_publication_failure_restores_existing_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = materialize_trusted_internal_request(
        _request_params("confornets"),
        output_dir=tmp_path,
        request_id="00000000-0000-4000-8000-000000000402",
    )
    prior_request = prior.request_path.read_bytes()
    prior_plan = prior.coordinate_plan_path.read_bytes()
    real_replace = os.replace
    failed = False

    def fail_plan_once(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        nonlocal failed
        if Path(destination) == prior.coordinate_plan_path and not failed:
            failed = True
            raise OSError("injected existing-pair publication failure")
        real_replace(source, destination)

    monkeypatch.setattr(request_builder.os, "replace", fail_plan_once)
    with pytest.raises(OSError, match="existing-pair"):
        materialize_trusted_internal_request(
            _request_params("confornets"),
            output_dir=tmp_path,
            request_id="00000000-0000-4000-8000-000000000403",
        )
    assert prior.request_path.read_bytes() == prior_request
    assert prior.coordinate_plan_path.read_bytes() == prior_plan


@pytest.mark.parametrize("authority", ["source", "created_by"])
def test_cm3_004b_caller_cannot_set_server_authority(authority: str) -> None:
    params = _request_params("confornets")
    params[authority] = {"forged": True}
    with pytest.raises(ConformationalMappingRequestError, match="unknown request fields"):
        validate_request_params(params)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"confornet_count": 1}, "at least 2"),
        ({"task": "mse", "references": []}, "staged reference"),
        (
            {
                "task": "transfer",
                "saved_steps": [0],
                "runs": 1,
                "confornet_count": 1,
                "transfer_source": None,
            },
            "transfer source",
        ),
    ],
)
def test_cm3_004c_task_invariants_fail_before_schedule(
    mutation: dict[str, object], message: str
) -> None:
    params = _request_params("confornets")
    settings = dict(params["confornets"])  # type: ignore[arg-type]
    settings.update(mutation)
    params["confornets"] = settings
    with pytest.raises(ConformationalMappingRequestError, match=message):
        validate_request_params(params)


def test_cm3_004d_future_backend_controls_are_hash_bound(tmp_path: Path) -> None:
    for backend, field, value, request_id in (
        ("protenix_v2_ensemble", "protenix_snapshot_id", "snapshot-7", "00000000-0000-4000-8000-000000000017"),
        ("external_import", "import_receipt_id", "9" * 64, "00000000-0000-4000-8000-000000000018"),
    ):
        params = _request_params(backend)
        params[field] = value
        materialized = materialize_trusted_internal_request(
            params,
            output_dir=tmp_path / backend,
            request_id=request_id,
        )
        request = json.loads(materialized.request_path.read_text(encoding="utf-8"))
        assert request[field] == value
        assert request["request_sha256"] == canonical_sha256(
            {key: item for key, item in request.items() if key != "request_sha256"}
        )


def test_cm3_004da_state_comparison_authority_is_admitted_and_hash_bound(tmp_path: Path) -> None:
    absent = materialize_trusted_internal_request(
        _request_params("protenix_v2_ensemble"),
        output_dir=tmp_path / "absent",
        request_id="00000000-0000-4000-8000-000000000018",
    )
    absent_request = json.loads(absent.request_path.read_text(encoding="utf-8"))
    assert "state_landscape_comparison" not in absent_request

    authority = {
        "mode": "pairwise",
        "target_id": "target-a",
        "scope": "all_within_target",
    }
    params = _request_params("protenix_v2_ensemble")
    params.update(
        ordered_seeds=[101, 202],
        generated_json_ordered_seeds=[101, 202],
        cli_ordered_seeds=[101, 202],
        samples_per_seed=1,
    )
    params["state_landscape_comparison"] = authority

    validated = validate_request_params(params)
    assert validated.request_fields["state_landscape_comparison"] == authority
    materialized = materialize_trusted_internal_request(
        params,
        output_dir=tmp_path,
        request_id="00000000-0000-4000-8000-000000000019",
    )
    request = json.loads(materialized.request_path.read_text(encoding="utf-8"))
    assert request["state_landscape_comparison"] == authority
    assert request["request_sha256"] == canonical_sha256(
        {key: item for key, item in request.items() if key != "request_sha256"}
    )

    invalid_target = _request_params("protenix_v2_ensemble")
    invalid_target["state_landscape_comparison"] = {**authority, "target_id": "not-a-request-target"}
    with pytest.raises(ConformationalMappingRequestError, match="state landscape comparison target"):
        validate_request_params(invalid_target)

    unknown_authority_field = _request_params("protenix_v2_ensemble")
    unknown_authority_field["state_landscape_comparison"] = {**authority, "silently_drop_me": True}
    with pytest.raises(ConformationalMappingRequestError, match="state_landscape_comparison"):
        validate_request_params(unknown_authority_field)

    invalid_scope = _request_params("protenix_v2_ensemble")
    invalid_scope["state_landscape_comparison"] = {**authority, "scope": "all_other_within_target"}
    with pytest.raises(ConformationalMappingRequestError, match="state_landscape_comparison"):
        validate_request_params(invalid_scope)

    incomplete_reference = _request_params("protenix_v2_ensemble")
    incomplete_reference["state_landscape_comparison"] = {
        "mode": "reference",
        "target_id": "target-a",
        "scope": "all_other_within_target",
        "reference_backend_coordinates": {
            "backend": "protenix_v2_ensemble",
            "target_id": "target-a",
            "ordered_seed": 101,
        },
    }
    with pytest.raises(ConformationalMappingRequestError, match="state_landscape_comparison"):
        validate_request_params(incomplete_reference)


@pytest.mark.parametrize(
    ("ordered_seeds", "authority", "message"),
    [
        (
            [101],
            {"mode": "pairwise", "target_id": "target-a", "scope": "all_within_target"},
            "at least two planned coordinates",
        ),
        (
            [101, 202],
            {
                "mode": "reference", "target_id": "target-a", "scope": "all_other_within_target",
                "reference_backend_coordinates": {
                    "backend": "protenix_v2_ensemble", "target_id": "target-a",
                    "ordered_seed": 999, "sample_index": 0,
                },
            },
            "does not match exactly one planned coordinate",
        ),
        (
            [101],
            {
                "mode": "reference", "target_id": "target-a", "scope": "all_other_within_target",
                "reference_backend_coordinates": {
                    "backend": "protenix_v2_ensemble", "target_id": "target-a",
                    "ordered_seed": 101, "sample_index": 0,
                },
            },
            "requires another planned coordinate",
        ),
    ],
)
def test_cm3_004daa_impossible_state_comparison_authority_is_rejected_at_admission(
    ordered_seeds: list[int], authority: dict[str, object], message: str
) -> None:
    params = _request_params("protenix_v2_ensemble")
    params["ordered_seeds"] = ordered_seeds
    params["generated_json_ordered_seeds"] = ordered_seeds
    params["cli_ordered_seeds"] = ordered_seeds
    params["samples_per_seed"] = 1
    params["state_landscape_comparison"] = authority

    with pytest.raises(ConformationalMappingRequestError, match=message):
        validate_request_params(params)


def test_cm3_004dab_reference_state_comparison_authority_with_two_planned_coordinates_is_admitted() -> None:
    params = _request_params("protenix_v2_ensemble")
    params["ordered_seeds"] = [101, 202]
    params["generated_json_ordered_seeds"] = [101, 202]
    params["cli_ordered_seeds"] = [101, 202]
    params["samples_per_seed"] = 1
    params["state_landscape_comparison"] = {
        "mode": "reference", "target_id": "target-a", "scope": "all_other_within_target",
        "reference_backend_coordinates": {
            "backend": "protenix_v2_ensemble", "target_id": "target-a",
            "ordered_seed": 101, "sample_index": 0,
        },
    }

    assert validate_request_params(params).coordinate_plan


@pytest.mark.parametrize(
    ("mode", "candidate_count", "admitted"),
    [
        (
            "pairwise",
            3,
            True,
        ),
        (
            "pairwise",
            4,
            False,
        ),
        ("reference", 6, True),
        ("reference", 7, False),
    ],
)
def test_cm3_004dac_state_comparison_work_cap_is_enforced_at_admission(
    mode: str, candidate_count: int, admitted: bool
) -> None:
    params = _request_params("protenix_v2_ensemble")
    _configure_state_comparison_candidate_count(
        params, mode=mode, candidate_count=candidate_count,
    )

    if admitted:
        assert len(validate_request_params(params).coordinate_plan) == candidate_count
    else:
        with pytest.raises(ConformationalMappingRequestError, match="comparison rows"):
            validate_request_params(params)


@pytest.mark.parametrize(
    ("candidate_count", "admitted"),
    [
        (6, True),  # Five reference pairs × the 20,000-residue backend envelope.
        (7, False),
    ],
)
def test_cm3_004dacb_state_comparison_row_work_envelope_is_enforced_at_admission(
    candidate_count: int, admitted: bool
) -> None:
    params = _request_params("protenix_v2_ensemble")
    _configure_state_comparison_candidate_count(
        params, mode="reference", candidate_count=candidate_count,
    )

    if admitted:
        assert len(validate_request_params(params).coordinate_plan) == candidate_count
    else:
        with pytest.raises(ConformationalMappingRequestError, match="comparison rows"):
            validate_request_params(params)


def test_cm3_004db_typed_submit_request_accepts_only_declared_state_comparison_authority() -> None:
    authority = {
        "mode": "pairwise",
        "target_id": "target-a",
        "scope": "all_within_target",
    }
    body = SubmitRequest(
        name="state authority",
        backend="protenix_v2_ensemble",
        ordered_seeds=[101],
        samples_per_seed=1,
        feature_policy={"mode": "regenerate_mutated_protein_v1"},
        runtime_policy={"use_default_params": True},
        analysis_policy={},
        state_landscape_comparison=authority,
    )
    assert body.state_landscape_comparison == authority

    with pytest.raises(ValidationError, match="unexpected"):
        SubmitRequest(
            name="state authority",
            backend="protenix_v2_ensemble",
            ordered_seeds=[101],
            samples_per_seed=1,
            feature_policy={"mode": "regenerate_mutated_protein_v1"},
            runtime_policy={"use_default_params": True},
            analysis_policy={},
            state_landscape_comparison=authority,
            unexpected=True,
        )


@pytest.mark.asyncio
async def test_cm3_004dc_submit_route_persists_state_comparison_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the real submit handler through request materialization and persistence."""

    fixture = json.loads(
        (API_ROOT / "tests" / "fixtures" / "conformational_mapping" / "schemas" / "positive" / "all_schemas.json").read_text()
    )
    snapshot_bytes = canonical_json_bytes([fixture["cm_complex_snapshot_v1"]])
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "snapshot.json").write_bytes(snapshot_bytes)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'submit.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()
    try:
        session.add(ConformationalMappingSource(
            source_id="snapshot", principal_id="alice", source_kind="complex_snapshot",
            storage_root=str(source_root), relative_path="snapshot.json",
            content_sha256=hashlib.sha256(snapshot_bytes).hexdigest(), size_bytes=len(snapshot_bytes),
            metadata_json={}, immutable=True,
        ))
        await session.commit()
        monkeypatch.setattr(cm_router, "get_results_dir", lambda: tmp_path / "results")
        monkeypatch.setattr(cm_router, "_runtime_registry", lambda _backend: {"test_runtime": True})
        request = Request({
            "type": "http", "method": "POST", "scheme": "http", "path": "/api/conformational-mapping/requests",
            "headers": [], "client": ("testclient", 50000), "server": ("testserver", 80),
        })
        request.state.authenticated_principal = {"id": "alice", "roles": ["scientist"]}
        authority = {"mode": "pairwise", "target_id": "target-a", "scope": "all_within_target"}
        response = await cm_router.submit_request(
            SubmitRequest(
                name="state authority route",
                backend="protenix_v2_ensemble",
                ordered_seeds=[101, 202], samples_per_seed=1,
                feature_policy=_request_params("protenix_v2_ensemble")["feature_policy"],
                runtime_policy={"use_default_params": True},
                analysis_policy=_request_params("protenix_v2_ensemble")["analysis_policy"],
                registered_snapshot_id="snapshot",
                state_landscape_comparison=authority,
            ),
            request,
            Response(),
            session,
        )
        persisted = await session.scalar(select(ConformationalMappingRequest).where(
            ConformationalMappingRequest.request_id == response["request_id"]
        ))
        assert persisted is not None
        assert persisted.request_json["state_landscape_comparison"] == authority
    finally:
        await session.close()
        await engine.dispose()


def test_cm3_004e_unknown_raw_cn_flags_fail_closed() -> None:
    params = _request_params("confornets")
    params["cn_sequence"] = "FORGED"
    with pytest.raises(ConformationalMappingRequestError, match="unknown request fields"):
        validate_request_params(params)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        {"confornet_count": 1},
        {"task": "mse", "references": []},
        {"task": "transfer", "transfer_source": None},
    ],
)
async def test_cm3_004f_task_invariants_reject_public_job_without_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: dict[str, object],
) -> None:
    class NoPersistenceSession:
        add_calls = 0

        def add(self, _value: object) -> None:
            self.add_calls += 1
            raise AssertionError("invalid conformational request created a job row")

        async def execute(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("invalid conformational request reached persistence")

    params = _request_params("confornets")
    settings = dict(params["confornets"])  # type: ignore[arg-type]
    settings.update(mutation)
    params["confornets"] = settings
    session = NoPersistenceSession()
    monkeypatch.delenv("BMS_CORE_RUNTIME_MODE", raising=False)
    monkeypatch.setattr(jobs_router, "get_results_dir", lambda: tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        await jobs_router.create_job(
            JobCreate(
                name="cm-invalid-task",
                model_id="conformational_mapping",
                mode="map",
                params=params,
            ),
            BackgroundTasks(),
            session,  # type: ignore[arg-type]
        )
    assert exc_info.value.status_code == 403
    assert "generic" in str(exc_info.value.detail).lower()
    assert "disabled" in str(exc_info.value.detail).lower()
    assert session.add_calls == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("checkpoint", {"path": "/etc/passwd", "sha256": "b" * 64}),
        ("repo_path", "/etc"),
        ("config", {"path": "../../etc/hosts", "sha256": "c" * 64}),
        (
            "references",
            [{
                "reference_id": "host-probe",
                "staged_path": "/etc/hosts",
                "content_sha256": "d" * 64,
                "state": "probe",
                "source": "caller_assertion",
            }],
        ),
    ],
)
async def test_cm3_generic_jobs_endpoint_fails_closed_before_path_or_identity_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    class NoAuthoritySession:
        def add(self, _value: object) -> None:
            raise AssertionError("generic conformational launch reached persistence")

        async def execute(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("generic conformational launch reached persistence")

    params = _request_params("confornets")
    settings = dict(params["confornets"])  # type: ignore[arg-type]
    if field == "repo_path":
        settings["backend_identity"] = {
            **settings["backend_identity"],  # type: ignore[dict-item]
            "repo_path": value,
        }
    else:
        settings[field] = value
    params["confornets"] = settings
    monkeypatch.delenv("BMS_CORE_RUNTIME_MODE", raising=False)
    monkeypatch.setattr(
        jobs_router,
        "get_results_dir",
        lambda: (_ for _ in ()).throw(AssertionError("generic launch inspected result paths")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await jobs_router.create_job(
            JobCreate(
                name="cm-public-authority-probe",
                model_id="conformational_mapping",
                mode="map",
                params=params,
            ),
            BackgroundTasks(),
            NoAuthoritySession(),  # type: ignore[arg-type]
        )
    assert exc_info.value.status_code == 403
    assert "generic" in str(exc_info.value.detail).lower()
    assert "disabled" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_cm3_005_seed_conflict_rejected_before_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class NoPersistenceSession:
        add_calls = 0

        def add(self, _value: object) -> None:
            self.add_calls += 1
            raise AssertionError("invalid conformational request created a job row")

        async def execute(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("invalid conformational request reached persistence")

    params = _request_params("protenix_v2_ensemble")
    params["cli_ordered_seeds"] = [505, 404, 303, 202, 101]
    session = NoPersistenceSession()
    monkeypatch.delenv("BMS_CORE_RUNTIME_MODE", raising=False)
    monkeypatch.setattr(jobs_router, "get_results_dir", lambda: tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        await jobs_router.create_job(
            JobCreate(
                name="cm-seed-conflict",
                model_id="conformational_mapping",
                mode="map",
                params=params,
            ),
            BackgroundTasks(),
            session,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 403
    assert "generic" in str(exc_info.value.detail).lower()
    assert "disabled" in str(exc_info.value.detail).lower()
    assert session.add_calls == 0
    assert list(tmp_path.iterdir()) == []


def test_cm3_006_unsupported_complex_rejected() -> None:
    multi_chain = _request_params("confornets")
    multi_chain["confornets"] = {
        **multi_chain["confornets"],  # type: ignore[dict-item]
        "sequence": "MKTIIA:LSYIFC",
    }
    with pytest.raises(ConformationalMappingRequestError, match="single-chain protein"):
        validate_request_params(multi_chain)

    three_references = _request_params("confornets")
    three_references["confornets"] = {
        **three_references["confornets"],  # type: ignore[dict-item]
        "references": [
            {
                "reference_id": value,
                "staged_path": f"/staged/{value}.pdb",
                "content_sha256": str(index) * 64,
                "state": value,
                "source": "authenticated_upload",
            }
            for index, value in enumerate(
                ("open", "closed", "intermediate"), start=1
            )
        ],
    }
    with pytest.raises(ConformationalMappingRequestError, match="at most two"):
        validate_request_params(three_references)


def test_cm3_007_unknown_backend_fails() -> None:
    unknown = _request_params("protenix_v2_ensemble")
    unknown["backend"] = "surprise_backend"
    with pytest.raises(ConformationalMappingRequestError, match="unknown backend"):
        validate_request_params(unknown)

    unknown_field = _request_params("protenix_v2_ensemble")
    unknown_field["silently_drop_me"] = True
    with pytest.raises(ConformationalMappingRequestError, match="unknown request fields"):
        validate_request_params(unknown_field)

    workflow_text = (REPO_ROOT / "workflows" / "conformational_mapping.nf").read_text(
        encoding="utf-8"
    )
    assert "switch (request.backend)" in workflow_text
    assert "default:" in workflow_text
    assert "Unknown conformational-mapping backend" in workflow_text
    assert "dynamic include" not in workflow_text.lower()
    assert "fallback" not in workflow_text.lower()


def test_cm3_008_confornets_experimental_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nextflow, "resolve_nextflow_executable", lambda: "/usr/bin/nextflow")
    legacy_params = {
        "task": "transfer",
        "sequence": "MKTIIALSYIFCLVFADYKDDDDA",
        "source_test_cases": "open_ref,closed_ref",
        "num_runs": 2,
        "k_confornets": 2,
        "num_samples": 5,
    }
    original = json.loads(json.dumps(legacy_params))
    command = nextflow.build_nextflow_command(
        "confornets_experimental",
        "design",
        legacy_params,
        str(tmp_path),
        job_id="legacy-cn",
    )

    assert nextflow.resolve_nextflow_entrypoint(
        effective_profile="confornets_experimental",
        model_id="confornets_experimental",
        mode="design",
    ) == "workflows/confornets_experimental.nf"
    assert _flag_value(command, "-profile") == (
        "confornets_experimental,workstation_ryzen7960x"
    )
    assert _flag_value(command, "--cn_source_test_cases") == "open_ref,closed_ref"
    assert _flag_value(command, "--cn_task") == "transfer"
    assert legacy_params == original
    assert ModelRegistry().get_model("confornets_experimental") is not None
    assert TemplateRegistry(API_ROOT / "config" / "templates").get_template(
        "confornets_experimental"
    ) is not None


def test_published_conformational_mapping_replaces_legacy_experimental_copy() -> None:
    models = ModelRegistry()
    templates = TemplateRegistry(API_ROOT / "config" / "templates")

    canonical_model = models.get_model("conformational_mapping")
    canonical_template = templates.get_template("conformational_mapping")
    legacy_model = models.get_model("confornets_experimental")
    legacy_template = templates.get_template("confornets_experimental")

    assert canonical_model is not None and canonical_model.experimental is False
    assert canonical_template is not None and canonical_template.experimental is False
    assert canonical_template.enabled is True
    assert legacy_model is not None and legacy_model.experimental is True
    assert legacy_template is not None and legacy_template.experimental is True
    assert legacy_template.enabled is False
    assert {template.id for template in templates.list_templates(enabled_only=True)} & {
        "conformational_mapping",
        "confornets_experimental",
    } == {"conformational_mapping"}
