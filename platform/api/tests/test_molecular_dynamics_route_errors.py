from __future__ import annotations

import ast
import importlib
import json
import sys
import types
from datetime import datetime as real_datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import BackgroundTasks, HTTPException
from jsonschema.exceptions import SchemaError


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
JOBS_PATH = API_ROOT / "routers" / "jobs.py"
for candidate in (str(API_ROOT), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def _jobs_module():
    if "services.binder_design" not in sys.modules:
        binder_stub = types.ModuleType("services.binder_design")
        setattr(binder_stub, "normalize_binder_design_params", lambda _mode, params: params)
        setattr(binder_stub, "validate_binder_target_path", lambda _params: None)
        sys.modules["services.binder_design"] = binder_stub
    return importlib.import_module("routers.jobs")


def _prepared_spec() -> dict[str, Any]:
    return {
        "schema": "bms.md.job.v1",
        "job_id": "assigned-by-bms",
        "engine": "openmm",
        "replicas": 1,
        "random_seed": 20260717,
        "input": {"coordinates": "prepared.gro", "topology": "topol.top"},
        "preparation": {
            "chemistry_assurance": "external_unreviewed",
            "force_field": "external",
            "water_model": "external",
            "box_type": "dodecahedron",
            "padding_nm": 1.0,
            "salt_molar": 0.15,
            "positive_ion": "NA",
            "negative_ion": "CL",
            "solvent_group": "SOL",
            "solvent_coordinates": "spc216.gro",
            "neutralize": True,
        },
        "stages": {
            "minimization": {"enabled": False, "steps": 50_000, "force_tolerance_kj_mol_nm": 1000},
            "nvt": {"enabled": False, "steps": 50_000, "temperature_k": 300},
            "npt": {"enabled": False, "steps": 50_000, "temperature_k": 300, "pressure_bar": 1},
            "production": {
                "enabled": True,
                "steps": 5_000,
                "timestep_fs": 2,
                "temperature_k": 300,
                "pressure_bar": 1,
                "checkpoint_interval_minutes": 15,
                "trajectory_interval_steps": 500,
                "energy_interval_steps": 100,
            },
        },
        "execution": {"gpu_id": "0", "ntmpi": 1, "ntomp": 8, "gpu_offload": "full", "pin": "on"},
    }


def _mapped_http_error(exc: Exception) -> HTTPException:
    jobs = _jobs_module()
    with pytest.raises(HTTPException) as error:
        jobs._raise_md_launch_http_error(exc)
    return error.value


def _detail(mapped: HTTPException) -> dict[str, Any]:
    assert isinstance(mapped.detail, dict)
    return mapped.detail


def test_md_launch_error_mapper_preserves_typed_status_and_code() -> None:
    launch = importlib.import_module("services.md.launch_contract")

    mapped = _mapped_http_error(
        launch.MDLaunchError("MD_INPUT_PATH_FORBIDDEN", "The MD input is forbidden.", status_code=403)
    )

    assert mapped.status_code == 403
    assert mapped.detail == {"code": "MD_INPUT_PATH_FORBIDDEN", "message": "The MD input is forbidden."}


def test_md_launch_error_mapper_maps_chemistry_selection_to_typed_422() -> None:
    catalog = importlib.import_module("services.md.chemistry_catalog")

    mapped = _mapped_http_error(catalog.ChemistryProfileSelectionError("MD_PROFILE_TEST", "Profile rejected."))

    assert mapped.status_code == 422
    assert mapped.detail == {"code": "MD_PROFILE_TEST", "message": "Profile rejected."}


def test_md_launch_error_mapper_maps_catalog_failure_to_sanitized_typed_503() -> None:
    catalog = importlib.import_module("services.md.chemistry_catalog")

    mapped = _mapped_http_error(
        catalog.ChemistryCatalogError("malformed YAML at /home/operator/private/catalog.yaml")
    )

    assert mapped.status_code == 503
    assert mapped.detail == {
        "code": "MD_LAUNCH_SERVICE_UNAVAILABLE",
        "message": "The molecular-dynamics launch service is temporarily unavailable.",
    }
    assert "/home/" not in json.dumps(mapped.detail)


def test_md_launch_error_mapper_maps_generic_contract_errors_without_host_paths() -> None:
    mapped = _mapped_http_error(ValueError("invalid contract at /home/private/schema.json"))
    detail = _detail(mapped)

    assert mapped.status_code == 422
    assert detail["code"] == "MD_JOB_CONTRACT_INVALID"
    assert "/home/" not in json.dumps(detail)


@pytest.mark.parametrize(
    "exc",
    [
        OSError("disk failure at /home/private/results"),
        FileNotFoundError("missing /home/private/md_job_v1.schema.json"),
        json.JSONDecodeError("bad JSON at /home/private/schema.json", "{}", 0),
        SchemaError("invalid schema at /home/private/schema.json"),
    ],
)
def test_md_launch_error_mapper_maps_transient_service_errors_to_safe_503(exc: Exception) -> None:
    mapped = _mapped_http_error(exc)
    detail = _detail(mapped)

    assert mapped.status_code == 503
    assert detail["code"] == "MD_LAUNCH_SERVICE_UNAVAILABLE"
    assert "/home/" not in json.dumps(detail)


def test_preview_and_final_materialization_calls_are_both_wired_to_typed_mapper() -> None:
    tree = ast.parse(JOBS_PATH.read_text(encoding="utf-8"))
    create_job = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "create_job"
    )
    mapped_calls: set[str] = set()
    for node in ast.walk(create_job):
        if not isinstance(node, ast.Try):
            continue
        body_calls = {
            call.func.id
            for child in node.body
            for call in ast.walk(child)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
        handler_calls = {
            call.func.id
            for handler in node.handlers
            for child in handler.body
            for call in ast.walk(child)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
        if "_raise_md_launch_http_error" in handler_calls:
            mapped_calls.update(body_calls & {"normalize_md_job_spec", "materialize_md_job_spec"})

    assert mapped_calls == {"normalize_md_job_spec", "materialize_md_job_spec"}

    for node in ast.walk(create_job):
        if not isinstance(node, ast.Try):
            continue
        body_calls = {
            call.func.id
            for child in node.body
            for call in ast.walk(child)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
        if body_calls & {"normalize_md_job_spec", "materialize_md_job_spec"}:
            caught_names: set[str] = set()
            for handler in node.handlers:
                if handler.type is None:
                    continue
                caught_names.update(
                    item.id for item in ast.walk(handler.type) if isinstance(item, ast.Name)
                )
            assert "ChemistryCatalogError" in caught_names


def test_final_persistence_failure_maps_to_503_and_removes_call_owned_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch = importlib.import_module("services.md.launch_contract")
    coordinates = tmp_path / "prepared" / "system.gro"
    topology = tmp_path / "prepared" / "topol.top"
    coordinates.parent.mkdir()
    coordinates.write_text("coordinates\n", encoding="utf-8")
    topology.write_text("topology\n", encoding="utf-8")

    def fail_json_dump(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("persistence failed at /home/private/results")

    monkeypatch.setattr(launch.json, "dump", fail_json_dump)
    with pytest.raises(Exception) as materialize_error:
        launch.materialize_md_job_spec(
            params={"md_job_spec": _prepared_spec()},
            job_id="persistence-failure",
            output_dir=tmp_path / "out",
            resolve_runtime_path=lambda value: str(coordinates if value.endswith(".gro") else topology),
        )

    mapped = _mapped_http_error(materialize_error.value)
    detail = _detail(mapped)
    assert mapped.status_code == 503
    assert detail["code"] == "MD_LAUNCH_SERVICE_UNAVAILABLE"
    assert "/home/" not in json.dumps(detail)
    contract_dir = tmp_path / "out" / "inputs"
    assert not (contract_dir / "md_job_config.json").exists()
    assert not list(contract_dir.glob(".*.tmp"))
    assert not list(contract_dir.glob("structure.*"))
    assert not list(contract_dir.glob("coordinates.*"))
    assert not list(contract_dir.glob("topology.*"))


def test_materialization_conflict_preserves_preexisting_authoritative_contract(
    tmp_path: Path,
) -> None:
    launch = importlib.import_module("services.md.launch_contract")
    coordinates = tmp_path / "prepared" / "system.gro"
    topology = tmp_path / "prepared" / "topol.top"
    coordinates.parent.mkdir()
    coordinates.write_text("coordinates\n", encoding="utf-8")
    topology.write_text("topology\n", encoding="utf-8")
    contract_dir = tmp_path / "out" / "inputs"
    contract_dir.mkdir(parents=True)
    config_path = contract_dir / "md_job_config.json"
    authoritative = b'{"authoritative": true}\n'
    config_path.write_bytes(authoritative)

    with pytest.raises(Exception) as error:
        launch.materialize_md_job_spec(
            params={"md_job_spec": _prepared_spec()},
            job_id="conflicting-attempt",
            output_dir=tmp_path / "out",
            resolve_runtime_path=lambda value: str(coordinates if value.endswith(".gro") else topology),
        )

    assert getattr(error.value, "code", None) == "MD_LAUNCH_OUTPUT_CONFLICT"
    assert getattr(error.value, "status_code", None) == 409
    assert config_path.read_bytes() == authoritative
    assert not list(contract_dir.glob(".*.tmp"))
    assert not list(contract_dir.glob("coordinates.*"))
    assert not list(contract_dir.glob("topology.*"))


class _FixedDatetime:
    @classmethod
    def now(cls) -> real_datetime:
        return real_datetime(2026, 7, 19, 12, 34, 56)


class _AcceptingRegistry:
    def reload(self) -> None:
        return None

    def validate_job_params(self, *_args: Any, **_kwargs: Any) -> list[str]:
        return []


class _NoPersistenceSession:
    def add(self, _value: object) -> None:
        raise AssertionError("invalid MD launch reached persistence")

    async def execute(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("invalid MD launch reached persistence")


def _install_md_route_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    results_root: Path,
) -> Any:
    jobs = _jobs_module()
    monkeypatch.setattr(jobs, "require_molecular_dynamics_feature", lambda _model_id: None)
    monkeypatch.setattr(jobs, "_raise_if_workflow_launches_disabled", lambda _action: None)
    monkeypatch.setattr(jobs, "get_registry", lambda: _AcceptingRegistry())
    monkeypatch.setattr(
        jobs,
        "normalize_md_job_spec",
        lambda *, params, **_kwargs: dict(params["md_job_spec"]),
    )
    monkeypatch.setattr(jobs, "get_results_dir", lambda: results_root)
    monkeypatch.setattr(jobs, "datetime", _FixedDatetime)
    return jobs


@pytest.mark.asyncio
@pytest.mark.parametrize("name_kind", ["absolute", "traversal"])
async def test_md_route_rejects_unsafe_output_name_before_mkdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name_kind: str,
) -> None:
    results_root = tmp_path / "results"
    results_root.mkdir()
    escaped = tmp_path / "escaped"
    name = str(escaped) if name_kind == "absolute" else "../escaped"
    jobs = _install_md_route_stubs(monkeypatch, results_root=results_root)
    schemas = importlib.import_module("schemas")

    with pytest.raises(HTTPException) as error:
        await jobs.create_job(
            schemas.JobCreate(
                name=name,
                model_id="molecular_dynamics",
                mode="simulate",
                params={"md_job_spec": _prepared_spec()},
            ),
            BackgroundTasks(),
            _NoPersistenceSession(),
        )

    assert error.value.status_code == 403
    assert _detail(error.value)["code"] == "MD_OUTPUT_PATH_FORBIDDEN"
    assert str(tmp_path) not in json.dumps(error.value.detail)
    assert list(results_root.iterdir()) == []
    assert not escaped.exists()


@pytest.mark.asyncio
async def test_md_route_rejects_symlink_output_escape_before_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results_root = tmp_path / "results"
    outside = tmp_path / "outside"
    results_root.mkdir()
    outside.mkdir()
    (results_root / "safe-name_20260719_123456").symlink_to(outside, target_is_directory=True)
    jobs = _install_md_route_stubs(monkeypatch, results_root=results_root)
    schemas = importlib.import_module("schemas")

    with pytest.raises(HTTPException) as error:
        await jobs.create_job(
            schemas.JobCreate(
                name="safe-name",
                model_id="molecular_dynamics",
                mode="simulate",
                params={"md_job_spec": _prepared_spec()},
            ),
            BackgroundTasks(),
            _NoPersistenceSession(),
        )

    assert error.value.status_code == 403
    assert _detail(error.value)["code"] == "MD_OUTPUT_PATH_FORBIDDEN"
    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_md_route_final_materialization_failure_removes_call_owned_empty_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results_root = tmp_path / "results"
    results_root.mkdir()
    jobs = _install_md_route_stubs(monkeypatch, results_root=results_root)
    schemas = importlib.import_module("schemas")

    def fail_materialization(*, output_dir: Path, **_kwargs: Any) -> dict[str, Any]:
        (output_dir / "inputs").mkdir(parents=True, exist_ok=True)
        raise OSError("final persistence failed at /home/private/results")

    monkeypatch.setattr(jobs, "materialize_md_job_spec", fail_materialization)
    gpu = importlib.import_module("services.gpu_orchestrator")
    monkeypatch.setattr(gpu, "estimate_vram", lambda *_args, **_kwargs: 0)

    with pytest.raises(HTTPException) as error:
        await jobs.create_job(
            schemas.JobCreate(
                name="cleanup-test",
                model_id="molecular_dynamics",
                mode="simulate",
                params={"md_job_spec": _prepared_spec()},
            ),
            BackgroundTasks(),
            _NoPersistenceSession(),
        )

    assert error.value.status_code == 503
    assert _detail(error.value)["code"] == "MD_LAUNCH_SERVICE_UNAVAILABLE"
    assert list(results_root.iterdir()) == []


@pytest.mark.asyncio
async def test_md_route_failure_preserves_preexisting_output_directory_and_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results_root = tmp_path / "results"
    output_dir = results_root / "existing-job_20260719_123456"
    output_dir.mkdir(parents=True)
    artifact = output_dir / "unrelated-result.bin"
    artifact.write_bytes(b"preexisting artifact\n")
    jobs = _install_md_route_stubs(monkeypatch, results_root=results_root)
    schemas = importlib.import_module("schemas")

    def fail_materialization(*, output_dir: Path, **_kwargs: Any) -> dict[str, Any]:
        (output_dir / "inputs").mkdir(parents=True, exist_ok=True)
        raise OSError("final persistence failed")

    monkeypatch.setattr(jobs, "materialize_md_job_spec", fail_materialization)
    gpu = importlib.import_module("services.gpu_orchestrator")
    monkeypatch.setattr(gpu, "estimate_vram", lambda *_args, **_kwargs: 0)

    with pytest.raises(HTTPException):
        await jobs.create_job(
            schemas.JobCreate(
                name="existing-job",
                model_id="molecular_dynamics",
                mode="simulate",
                params={"md_job_spec": _prepared_spec()},
            ),
            BackgroundTasks(),
            _NoPersistenceSession(),
        )

    assert artifact.read_bytes() == b"preexisting artifact\n"
    assert output_dir.is_dir()
