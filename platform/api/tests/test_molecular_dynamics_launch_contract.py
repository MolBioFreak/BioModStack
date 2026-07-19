from __future__ import annotations

import copy
import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError
from jsonschema import validate as validate_json_schema

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
CATALOG_DIR = API_ROOT / "config" / "md_chemistry_profiles"
ONE_AKI_FIXTURE = API_ROOT / "tests" / "fixtures" / "md" / "1AKI.pdb"
ONE_AKI_SHA256 = "c75d7a689617248cdd92dc6633531d2506fb9bef1e6e21e26c8f579ae6955abb"
GROMACS_SIF_SHA256 = "97c117ea07496c0d1b13d80be84d33345b89063b47ccfb83f6cbff0145f1385b"
MD_JOB_SCHEMA_PATH = REPO_ROOT / "schemas" / "md_job_v1.schema.json"
for candidate in (str(API_ROOT), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from services.md.chemistry_catalog import (  # noqa: E402
    ChemistryCatalog,
    ChemistryProfileSelectionError,
    RuntimeProbeResult,
)
from services.md.launch_contract import materialize_md_job_spec, normalize_md_job_spec  # noqa: E402


def _install_allowed_roots(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    roots = {"inputs": root / "inputs", "bms_results": root / "results"}
    for allowed_root in roots.values():
        allowed_root.mkdir(parents=True, exist_ok=True)
    paths_module = importlib.import_module("paths")
    jobs_module = importlib.import_module("routers.jobs")
    monkeypatch.setattr(paths_module, "get_allowed_roots", lambda: roots)
    monkeypatch.setattr(jobs_module, "get_allowed_roots", lambda: roots, raising=False)


def test_md_route_strict_resolver_accepts_only_existing_files_under_allowed_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_allowed_roots(monkeypatch, tmp_path)
    jobs_module = importlib.import_module("routers.jobs")
    resolver = getattr(jobs_module, "_resolve_md_input_path_for_runtime", None)
    assert resolver is not None, "MD route strict resolver is missing"
    allowed = tmp_path / "inputs" / "md" / "system.gro"
    allowed.parent.mkdir(parents=True)
    allowed.write_text("prepared\n", encoding="utf-8")

    assert resolver("inputs/md/system.gro") == str(allowed.resolve())
    assert resolver(str(allowed.resolve())) == str(allowed.resolve())


@pytest.mark.parametrize("untrusted", ["/etc/hosts", "../../etc/hosts", "unresolved.gro"])
def test_md_route_strict_resolver_rejects_outside_and_unresolved_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    untrusted: str,
) -> None:
    _install_allowed_roots(monkeypatch, tmp_path)
    jobs_module = importlib.import_module("routers.jobs")
    resolver = getattr(jobs_module, "_resolve_md_input_path_for_runtime", None)
    assert resolver is not None, "MD route strict resolver is missing"

    with pytest.raises(Exception) as error:
        resolver(untrusted)

    assert getattr(error.value, "code", None) == "MD_INPUT_PATH_FORBIDDEN"
    assert getattr(error.value, "status_code", None) == 403
    assert "/etc/hosts" not in str(error.value)


def _probe() -> RuntimeProbeResult:
    return RuntimeProbeResult(
        runtime_id="gromacs-2025.3",
        runtime_version="2025.3",
        available=True,
        asset_ids=frozenset({"amber99sb-ildn.ff"}),
        checked_at="2026-07-19T03:30:00Z",
        error_code=None,
        sif_sha256=GROMACS_SIF_SHA256,
    )


def _catalog() -> ChemistryCatalog:
    return ChemistryCatalog(config_dir=CATALOG_DIR, probe=_probe)


def _spec(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "bms.md.job.v1",
        "job_id": "assigned-by-bms",
        "engine": "gromacs",
        "replicas": 1,
        "random_seed": 20260717,
        "input": {"structure": "uploads/md/1AKI.pdb"},
        "preparation": {
            "chemistry_assurance": "smoke_fixture",
            "chemistry_profile_id": profile["id"],
            "chemistry_profile_sha256": profile["profile_sha256"],
            "chemistry_profile_scope": profile["scientific_validation"]["scope"]["launch_scope"],
            "force_field": "amber99sb-ildn",
            "water_model": "tip3p",
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
            "minimization": {"enabled": True, "steps": 50_000, "force_tolerance_kj_mol_nm": 1000},
            "nvt": {"enabled": True, "steps": 50_000, "temperature_k": 300},
            "npt": {"enabled": True, "steps": 50_000, "temperature_k": 300, "pressure_bar": 1},
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


def _prepared_spec(*, engine: str = "openmm") -> dict[str, Any]:
    spec = _spec(_catalog().get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1") or {})
    spec["engine"] = engine
    spec["input"] = {"coordinates": "prepared.gro", "topology": "topol.top"}
    spec["preparation"] = {
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
    }
    for stage in ("minimization", "nvt", "npt"):
        spec["stages"][stage]["enabled"] = False
    return spec


def _write_authorized_structure(tmp_path: Path) -> Path:
    runtime_structure = tmp_path / "runtime" / "1AKI.pdb"
    runtime_structure.parent.mkdir(parents=True)
    runtime_structure.write_bytes(ONE_AKI_FIXTURE.read_bytes())
    return runtime_structure


def _materialize_prepared(tmp_path: Path, spec: dict[str, Any]) -> dict[str, Any]:
    coordinates = tmp_path / "prepared" / "system.gro"
    topology = tmp_path / "prepared" / "topol.top"
    coordinates.parent.mkdir(parents=True, exist_ok=True)
    coordinates.write_text("prepared coordinates\n", encoding="utf-8")
    topology.write_text("prepared topology\n", encoding="utf-8")
    return materialize_md_job_spec(
        params={"md_job_spec": spec},
        job_id="prepared-resource-job",
        output_dir=tmp_path / "out",
        resolve_runtime_path=lambda value: str(coordinates if value.endswith(".gro") else topology),
        chemistry_catalog=_catalog(),
    )


def _materialize_prepared_topology(
    tmp_path: Path,
    topology_text: str,
    *,
    sidecars: dict[str, str] | None = None,
    runtime_identity_resolver: Any | None = None,
) -> dict[str, Any]:
    source_root = tmp_path / "prepared-source"
    source_root.mkdir(parents=True, exist_ok=True)
    coordinates = source_root / "system.gro"
    topology = source_root / "topol.top"
    coordinates.write_text("prepared coordinates\n", encoding="utf-8")
    topology.write_text(topology_text, encoding="utf-8")
    for relative, content in (sidecars or {}).items():
        sidecar = source_root / relative
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(content, encoding="utf-8")
    kwargs: dict[str, Any] = {}
    if runtime_identity_resolver is not None:
        kwargs["runtime_identity_resolver"] = runtime_identity_resolver
    return materialize_md_job_spec(
        params={"md_job_spec": _prepared_spec()},
        job_id="prepared-topology-job",
        output_dir=tmp_path / "out",
        resolve_runtime_path=lambda value: str(coordinates if value.endswith(".gro") else topology),
        chemistry_catalog=_catalog(),
        **kwargs,
    )


def _materialize_structure(
    tmp_path: Path,
    spec: dict[str, Any],
    *,
    catalog: ChemistryCatalog | None = None,
    runtime_structure: Path | None = None,
) -> dict[str, Any]:
    structure = runtime_structure or _write_authorized_structure(tmp_path)
    return materialize_md_job_spec(
        params={"md_job_spec": spec},
        job_id="parent-job-123",
        output_dir=tmp_path / "out",
        resolve_runtime_path=lambda value: str(structure),
        chemistry_catalog=catalog or _catalog(),
    )


def test_exact_1aki_smoke_contract_is_authorized_after_runtime_path_resolution(tmp_path: Path) -> None:
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None

    materialized = _materialize_structure(tmp_path, _spec(profile), catalog=catalog)

    config_path = Path(materialized["md_job_config"])
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    snapshot = Path(persisted["input"]["structure"])
    assert persisted["job_id"] == "parent-job-123"
    assert snapshot.parent == config_path.parent
    assert snapshot.name == "structure.pdb"
    assert snapshot.read_bytes() == ONE_AKI_FIXTURE.read_bytes()
    assert snapshot.stat().st_mode & 0o222 == 0
    assert persisted["input"]["structure_sha256"] == ONE_AKI_SHA256
    assert "runtime/1AKI.pdb" not in json.dumps(persisted)
    assert persisted["replicas"] == 1
    assert persisted["preparation"]["chemistry_assurance"] == "smoke_fixture"
    assert persisted["preparation"]["chemistry_profile_sha256"] == profile["profile_sha256"]
    assert materialized["md_job_spec"] == persisted


def test_frontend_smoke_default_cadence_normalizes_within_500_production_steps(tmp_path: Path) -> None:
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    spec = _spec(profile)
    spec["stages"]["production"].update(
        steps=500,
        trajectory_interval_steps=500,
        energy_interval_steps=100,
    )

    materialized = _materialize_structure(tmp_path, spec, catalog=catalog)

    production = materialized["md_job_spec"]["stages"]["production"]
    assert production["trajectory_interval_steps"] <= production["steps"]
    assert production["energy_interval_steps"] <= production["steps"]


def test_materialize_md_job_spec_requires_exactly_one_input_shape(tmp_path: Path) -> None:
    profile = _catalog().get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    invalid = _spec(profile)
    invalid["input"] = {"coordinates": "prepared.gro"}
    with pytest.raises(ValueError, match="coordinates plus topology"):
        materialize_md_job_spec(
            params={"md_job_spec": invalid},
            job_id="parent-job-123",
            output_dir=tmp_path,
            resolve_runtime_path=lambda value: value,
            chemistry_catalog=_catalog(),
        )


def test_materialize_md_job_spec_rejects_mixed_structure_and_prepared_input(tmp_path: Path) -> None:
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    invalid = _spec(profile)
    invalid["input"].update(coordinates="prepared.gro", topology="topol.top")
    runtime_structure = _write_authorized_structure(tmp_path)

    with pytest.raises(ValueError, match="exactly one input mode"):
        _materialize_structure(tmp_path, invalid, catalog=catalog, runtime_structure=runtime_structure)


@pytest.mark.parametrize(
    ("remove_field", "expected_code"),
    [
        (lambda spec: spec.pop("engine"), "MD_JOB_CONTRACT_INVALID"),
        (lambda spec: spec.pop("replicas"), "MD_JOB_CONTRACT_INVALID"),
        (lambda spec: spec["preparation"].pop("force_field"), "MD_JOB_CONTRACT_INVALID"),
        (lambda spec: spec["preparation"].pop("water_model"), "MD_JOB_CONTRACT_INVALID"),
    ],
)
def test_structure_launch_rejects_omitted_required_raw_fields(
    tmp_path: Path,
    remove_field: Any,
    expected_code: str,
) -> None:
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    invalid = _spec(profile)
    remove_field(invalid)

    with pytest.raises(Exception) as error:
        _materialize_structure(tmp_path, invalid, catalog=catalog)

    assert getattr(error.value, "code", None) == expected_code
    assert getattr(error.value, "status_code", None) == 422


@pytest.mark.parametrize(
    "mutation",
    [
        lambda spec: spec.update(replicas=1.9),
        lambda spec: spec["stages"]["production"].update(steps=5_000.9),
    ],
)
def test_structure_launch_rejects_coercible_non_integer_values(
    tmp_path: Path,
    mutation: Any,
) -> None:
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    invalid = _spec(profile)
    mutation(invalid)

    with pytest.raises(Exception) as error:
        _materialize_structure(tmp_path, invalid, catalog=catalog)

    assert getattr(error.value, "code", None) == "MD_RESOURCE_CONTRACT_VIOLATION"
    assert getattr(error.value, "status_code", None) == 422


def test_prepared_resource_contract_accepts_exact_aggregate_and_field_boundaries(tmp_path: Path) -> None:
    spec = _prepared_spec(engine="openmm")
    spec["replicas"] = 8
    spec["stages"]["minimization"].update(steps=5_000_000)
    spec["stages"]["nvt"].update(steps=5_000_000)
    spec["stages"]["npt"].update(steps=2_500_000)
    spec["stages"]["production"].update(
        steps=50_000_000,
        timestep_fs=4.0,
        checkpoint_interval_minutes=1440,
        trajectory_interval_steps=50_000_000,
        energy_interval_steps=50_000_000,
    )
    spec["execution"].update(ntmpi=1, ntomp=128)

    materialized = _materialize_prepared(tmp_path, spec)

    assert materialized["md_job_spec"]["replicas"] == 8


def test_prepared_resource_contract_accepts_npt_step_boundary(tmp_path: Path) -> None:
    spec = _prepared_spec(engine="openmm")
    spec["stages"]["npt"].update(steps=5_000_000)

    assert _materialize_prepared(tmp_path, spec)["md_job_spec"]["stages"]["npt"]["steps"] == 5_000_000


def test_prepared_resource_contract_accepts_exact_output_record_boundaries(tmp_path: Path) -> None:
    spec = _prepared_spec(engine="openmm")
    spec["replicas"] = 8
    spec["stages"]["production"].update(
        steps=50_000_000,
        checkpoint_interval_minutes=1.0,
        trajectory_interval_steps=400,
        energy_interval_steps=80,
    )

    materialized = _materialize_prepared(tmp_path, spec)

    production = materialized["md_job_spec"]["stages"]["production"]
    assert production["checkpoint_interval_minutes"] == 1.0
    assert 8 * (production["steps"] // production["trajectory_interval_steps"]) == 1_000_000
    assert 8 * (production["steps"] // production["energy_interval_steps"]) == 5_000_000


@pytest.mark.parametrize(
    "mutation",
    [
        lambda spec: spec["stages"]["production"].update(checkpoint_interval_minutes=0.999),
        lambda spec: spec["stages"]["production"].update(
            steps=50_000_000,
            trajectory_interval_steps=399,
            energy_interval_steps=80,
        ),
        lambda spec: spec["stages"]["production"].update(
            steps=50_000_000,
            trajectory_interval_steps=400,
            energy_interval_steps=79,
        ),
    ],
)
def test_prepared_resource_contract_rejects_output_admission_over_boundaries(
    tmp_path: Path,
    mutation: Any,
) -> None:
    spec = _prepared_spec(engine="openmm")
    spec["replicas"] = 8
    mutation(spec)

    with pytest.raises(Exception) as error:
        _materialize_prepared(tmp_path, spec)

    assert getattr(error.value, "code", None) == "MD_RESOURCE_CONTRACT_VIOLATION"
    assert getattr(error.value, "status_code", None) == 422
    assert not (tmp_path / "out" / "inputs" / "md_job_config.json").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda spec: spec.update(replicas=1024),
        lambda spec: spec["stages"]["minimization"].update(enabled=True, steps=5_000_001),
        lambda spec: spec["stages"]["nvt"].update(enabled=True, steps=5_000_001),
        lambda spec: spec["stages"]["npt"].update(enabled=True, steps=5_000_001),
        lambda spec: spec["stages"]["production"].update(steps=10**15),
        lambda spec: spec["stages"]["production"].update(timestep_fs=4.0001),
        lambda spec: spec["stages"]["production"].update(checkpoint_interval_minutes=1440.1),
        lambda spec: spec["stages"]["production"].update(trajectory_interval_steps=5_001),
        lambda spec: spec["stages"]["production"].update(energy_interval_steps=5_001),
        lambda spec: spec["execution"].update(ntmpi=2),
        lambda spec: spec["execution"].update(ntomp=129),
        lambda spec: spec["execution"].update(ntmpi=10**9, ntomp=10**9),
        lambda spec: (
            spec.update(replicas=8),
            spec["stages"]["minimization"].update(enabled=True, steps=5_000_000),
            spec["stages"]["nvt"].update(enabled=True, steps=5_000_000),
            spec["stages"]["npt"].update(enabled=True, steps=5_000_000),
            spec["stages"]["production"].update(steps=50_000_000),
        ),
    ],
)
def test_prepared_resource_contract_rejects_over_boundary_values_before_persistence(
    tmp_path: Path,
    mutation: Any,
) -> None:
    spec = _prepared_spec(engine="openmm")
    mutation(spec)

    with pytest.raises(Exception) as error:
        _materialize_prepared(tmp_path, spec)

    assert getattr(error.value, "code", None) == "MD_RESOURCE_CONTRACT_VIOLATION"
    assert getattr(error.value, "status_code", None) == 422
    assert not (tmp_path / "out" / "inputs" / "md_job_config.json").exists()


def test_materialize_md_job_spec_rejects_missing_runtime_input(tmp_path: Path) -> None:
    profile = _catalog().get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    with pytest.raises(Exception) as error:
        _materialize_structure(
            tmp_path,
            _spec(profile),
            runtime_structure=tmp_path / "missing.pdb",
        )

    assert getattr(error.value, "code", None) == "MD_INPUT_MISSING"
    assert getattr(error.value, "status_code", None) == 409
    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        (lambda spec: spec.update(replicas=2), "replicas"),
        (lambda spec: spec["preparation"].update(salt_molar=0.16), "salt_molar"),
        (lambda spec: spec["preparation"].update(padding_nm=1.1), "padding_nm"),
        (lambda spec: spec["stages"]["nvt"].update(temperature_k=301), "temperature_k"),
        (lambda spec: spec["stages"]["production"].update(pressure_bar=2), "pressure_bar"),
        (lambda spec: spec["stages"]["production"].update(timestep_fs=1), "timestep_fs"),
        (lambda spec: spec["stages"]["production"].update(steps=5_001), "production.steps"),
        (lambda spec: spec["stages"]["minimization"].update(steps=50_001), "minimization.steps"),
        (lambda spec: spec["stages"]["nvt"].update(steps=50_001), "nvt.steps"),
        (lambda spec: spec["stages"]["npt"].update(steps=50_001), "npt.steps"),
    ],
)
def test_smoke_scope_rejects_unauthorized_protocol_and_ionic_values(
    tmp_path: Path,
    mutation: Any,
    expected_fragment: str,
) -> None:
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    spec = _spec(profile)
    mutation(spec)

    with pytest.raises(ChemistryProfileSelectionError) as error:
        _materialize_structure(tmp_path, spec, catalog=catalog)

    assert error.value.code == "MD_CHEMISTRY_SCOPE_VIOLATION"
    assert expected_fragment in str(error.value)


def test_smoke_scope_rejects_wrong_structure_hash(tmp_path: Path) -> None:
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    wrong_structure = tmp_path / "not-1aki.pdb"
    wrong_structure.write_text("ATOM\n", encoding="utf-8")

    with pytest.raises(ChemistryProfileSelectionError) as error:
        _materialize_structure(tmp_path, _spec(profile), catalog=catalog, runtime_structure=wrong_structure)

    assert error.value.code == "MD_CHEMISTRY_SCOPE_VIOLATION"
    assert "structure_sha256" in str(error.value)


def test_smoke_structure_hashing_is_bounded_to_100_mib(tmp_path: Path) -> None:
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    oversized = tmp_path / "oversized.pdb"
    with oversized.open("wb") as handle:
        handle.truncate(100 * 1024 * 1024 + 1)

    with pytest.raises(ChemistryProfileSelectionError) as error:
        _materialize_structure(tmp_path, _spec(profile), catalog=catalog, runtime_structure=oversized)

    assert error.value.code == "MD_CHEMISTRY_SCOPE_VIOLATION"
    assert "100 MiB" in str(error.value)


def test_prepared_openmm_serializes_external_unreviewed_without_profile_claims(tmp_path: Path) -> None:
    coordinates = tmp_path / "system.gro"
    topology = tmp_path / "topol.top"
    coordinates.write_text("prepared\n", encoding="utf-8")
    topology.write_text("prepared\n", encoding="utf-8")

    materialized = materialize_md_job_spec(
        params={"md_job_spec": _prepared_spec()},
        job_id="parent-job-123",
        output_dir=tmp_path / "out",
        resolve_runtime_path=lambda value: str(coordinates if value.endswith(".gro") else topology),
        chemistry_catalog=_catalog(),
    )

    preparation = materialized["md_job_spec"]["preparation"]
    materialized_input = materialized["md_job_spec"]["input"]
    assert preparation["chemistry_assurance"] == "external_unreviewed"
    assert not any(key.startswith("chemistry_profile_") for key in preparation)
    for field in ("coordinates", "topology"):
        snapshot = Path(materialized_input[field])
        assert snapshot.parent == tmp_path / "out" / "inputs"
        assert snapshot.is_file()
        assert materialized_input[f"{field}_sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
    assert str(coordinates) not in json.dumps(materialized["md_job_spec"])
    assert str(topology) not in json.dumps(materialized["md_job_spec"])


def test_prepared_gromacs_is_rejected_before_path_resolution_or_output_side_effects(tmp_path: Path) -> None:
    resolver_calls: list[str] = []
    spec = _prepared_spec(engine="gromacs")

    with pytest.raises(Exception) as error:
        normalize_md_job_spec(
            params={"md_job_spec": spec},
            job_id="prepared-gromacs-rejected",
            resolve_runtime_path=lambda value: resolver_calls.append(value) or value,
            chemistry_catalog=_catalog(),
        )

    assert getattr(error.value, "code", None) == "MD_ENGINE_INPUT_UNSUPPORTED"
    assert getattr(error.value, "status_code", None) == 422
    assert resolver_calls == []
    assert not (tmp_path / "out").exists()


def test_prepared_openmm_snapshots_only_declared_nested_topology_closure(tmp_path: Path) -> None:
    materialized = _materialize_prepared_topology(
        tmp_path,
        '#include "molecule/main.itp"\n[ system ]\nPrepared\n',
        sidecars={
            "molecule/main.itp": '#include "nested/atoms.itp"\n[ moleculetype ]\nProtein 3\n',
            "molecule/nested/atoms.itp": "[ atoms ]\n",
            "undeclared-neighbor.itp": "must not be copied\n",
        },
    )

    input_config = materialized["md_job_spec"]["input"]
    closure = input_config["topology_closure"]
    closure_root = Path(closure["root"])
    assert [(entry["path"], entry["parent"], entry["include"]) for entry in closure["files"]] == [
        ("molecule/main.itp", "topology.top", "molecule/main.itp"),
        ("molecule/nested/atoms.itp", "molecule/main.itp", "nested/atoms.itp"),
    ]
    for entry in closure["files"]:
        snapshot = closure_root / entry["path"]
        assert snapshot.is_file()
        assert entry["bytes"] == snapshot.stat().st_size
        assert entry["sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
        assert snapshot.stat().st_mode & 0o222 == 0
    assert not (closure_root / "undeclared-neighbor.itp").exists()


@pytest.mark.parametrize(
    ("include", "expected_code"),
    [
        ("../outside.itp", "MD_TOPOLOGY_INCLUDE_FORBIDDEN"),
        ("/etc/hosts", "MD_TOPOLOGY_INCLUDE_FORBIDDEN"),
        ("missing.itp", "MD_TOPOLOGY_INCLUDE_MISSING"),
    ],
)
def test_prepared_topology_rejects_unsafe_or_missing_local_includes_without_path_leakage(
    tmp_path: Path,
    include: str,
    expected_code: str,
) -> None:
    with pytest.raises(Exception) as error:
        _materialize_prepared_topology(tmp_path, f'#include "{include}"\n')

    assert getattr(error.value, "code", None) == expected_code
    assert getattr(error.value, "status_code", None) == 422
    assert str(tmp_path) not in str(error.value)
    assert not (tmp_path / "out" / "inputs" / "md_job_config.json").exists()


def test_prepared_topology_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside.itp"
    outside.write_text("[ atoms ]\n", encoding="utf-8")
    source_root = tmp_path / "prepared-source"
    source_root.mkdir()
    (source_root / "escape.itp").symlink_to(outside)

    with pytest.raises(Exception) as error:
        _materialize_prepared_topology(tmp_path, '#include "escape.itp"\n')

    assert getattr(error.value, "code", None) == "MD_TOPOLOGY_INCLUDE_FORBIDDEN"
    assert str(tmp_path) not in str(error.value)


def test_prepared_topology_rejects_recursive_include_cycle(tmp_path: Path) -> None:
    with pytest.raises(Exception) as error:
        _materialize_prepared_topology(
            tmp_path,
            '#include "a.itp"\n',
            sidecars={"a.itp": '#include "b.itp"\n', "b.itp": '#include "a.itp"\n'},
        )

    assert getattr(error.value, "code", None) == "MD_TOPOLOGY_INCLUDE_CYCLE"


def test_runtime_force_field_include_requires_and_persists_exact_sif_identity(tmp_path: Path) -> None:
    include = "amber99sb-ildn.ff/forcefield.itp"
    identity = {
        "runtime_id": "openmm-md-8.5.2",
        "runtime_version": "8.5.2+gromacs-2025.3",
        "sif_sha256": "d" * 64,
        "asset_ids": ["amber99sb-ildn.ff"],
    }

    materialized = _materialize_prepared_topology(
        tmp_path,
        f'#include "{include}"\n',
        runtime_identity_resolver=lambda: identity,
    )

    closure = materialized["md_job_spec"]["input"]["topology_closure"]
    assert closure["runtime_identity"] == identity
    assert closure["runtime_includes"] == [{"parent": "topology.top", "include": include}]
    assert closure["files"] == []


def test_runtime_force_field_include_is_rejected_without_bound_identity(tmp_path: Path) -> None:
    with pytest.raises(Exception) as error:
        _materialize_prepared_topology(
            tmp_path,
            '#include "amber99sb-ildn.ff/forcefield.itp"\n',
            runtime_identity_resolver=lambda: None,
        )

    assert getattr(error.value, "code", None) == "MD_TOPOLOGY_RUNTIME_IDENTITY_REQUIRED"


def test_raw_callers_cannot_claim_topology_closure_manifest(tmp_path: Path) -> None:
    spec = _prepared_spec()
    spec["input"]["topology_closure"] = {"root": "/tmp/claimed", "files": []}

    with pytest.raises(Exception) as error:
        _materialize_prepared(tmp_path, spec)

    assert getattr(error.value, "code", None) == "MD_TOPOLOGY_CLOSURE_FORBIDDEN"
    assert getattr(error.value, "status_code", None) == 422


@pytest.mark.parametrize("digest_field", ["structure_sha256", "coordinates_sha256", "topology_sha256"])
def test_raw_callers_cannot_claim_server_generated_input_digests(
    tmp_path: Path,
    digest_field: str,
) -> None:
    profile = _catalog().get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    spec = _spec(profile) if digest_field == "structure_sha256" else _prepared_spec()
    spec["input"][digest_field] = "0" * 64

    with pytest.raises(Exception) as error:
        if digest_field == "structure_sha256":
            _materialize_structure(tmp_path, spec)
        else:
            _materialize_prepared(tmp_path, spec)

    assert getattr(error.value, "code", None) == "MD_INPUT_DIGEST_FORBIDDEN"
    assert getattr(error.value, "status_code", None) == 422


def test_source_replaced_between_preview_and_materialization_is_rejected_without_config(tmp_path: Path) -> None:
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    source = _write_authorized_structure(tmp_path)
    preview = normalize_md_job_spec(
        params={"md_job_spec": _spec(profile)},
        job_id="validation-preview",
        resolve_runtime_path=lambda value: str(source),
        chemistry_catalog=catalog,
    )
    source.write_text("replacement bytes\n", encoding="utf-8")

    with pytest.raises(ChemistryProfileSelectionError):
        materialize_md_job_spec(
            params={"md_job_spec": preview},
            job_id="final-job",
            output_dir=tmp_path / "race-out",
            resolve_runtime_path=lambda value: str(source),
            chemistry_catalog=catalog,
        )

    assert not (tmp_path / "race-out" / "inputs" / "md_job_config.json").exists()


def test_source_replaced_after_materialization_does_not_change_job_snapshot(tmp_path: Path) -> None:
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    source = _write_authorized_structure(tmp_path)
    materialized = _materialize_structure(tmp_path, _spec(profile), catalog=catalog, runtime_structure=source)
    snapshot = Path(materialized["md_job_spec"]["input"]["structure"])

    source.write_text("changed after materialization\n", encoding="utf-8")

    assert snapshot.read_bytes() == ONE_AKI_FIXTURE.read_bytes()
    assert materialized["md_job_spec"]["input"]["structure_sha256"] == ONE_AKI_SHA256


def test_materialization_uses_one_captured_catalog_view_across_forced_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    available = True

    def probe() -> RuntimeProbeResult:
        if available:
            return _probe()
        return RuntimeProbeResult(
            runtime_id="gromacs-2025.3",
            runtime_version=None,
            available=False,
            asset_ids=frozenset(),
            checked_at="2026-07-19T04:32:00Z",
            error_code="runtime_probe_failed",
        )

    catalog = ChemistryCatalog(config_dir=CATALOG_DIR, probe=probe)
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    original_view = catalog.view
    view_calls = 0

    def capture_then_refresh():
        nonlocal available, view_calls
        view_calls += 1
        if view_calls > 1:
            pytest.fail("materialization fetched a later catalog view")
        captured = original_view()
        available = False
        catalog.refresh()
        return captured

    monkeypatch.setattr(catalog, "view", capture_then_refresh)

    materialized = _materialize_structure(tmp_path, _spec(profile), catalog=catalog)

    assert view_calls == 1
    assert materialized["md_job_spec"]["preparation"]["chemistry_profile_sha256"] == profile["profile_sha256"]


def test_snapshot_publication_race_preserves_winner_and_cleans_only_loser_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch = importlib.import_module("services.md.launch_contract")
    coordinates = tmp_path / "prepared" / "system.gro"
    topology = tmp_path / "prepared" / "topol.top"
    coordinates.parent.mkdir()
    coordinates.write_bytes(b"loser coordinates\n")
    topology.write_bytes(b"loser topology\n")
    winner = b"concurrent topology winner\n"
    real_link = launch.os.link

    def race_link(source: str | Path, destination: str | Path, *args: Any, **kwargs: Any) -> None:
        destination_path = Path(destination)
        if destination_path.name == "topology.top" and not destination_path.exists():
            destination_path.write_bytes(winner)
        real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(launch.os, "link", race_link)

    with pytest.raises(Exception) as error:
        launch.materialize_md_job_spec(
            params={"md_job_spec": _prepared_spec()},
            job_id="snapshot-publication-race",
            output_dir=tmp_path / "out",
            resolve_runtime_path=lambda value: str(coordinates if value.endswith(".gro") else topology),
            chemistry_catalog=_catalog(),
        )

    contract_dir = tmp_path / "out" / "inputs"
    assert getattr(error.value, "code", None) == "MD_LAUNCH_OUTPUT_CONFLICT"
    assert getattr(error.value, "status_code", None) == 409
    assert (contract_dir / "topology.top").read_bytes() == winner
    assert not (contract_dir / "coordinates.gro").exists()
    assert not (contract_dir / "md_job_config.json").exists()
    assert not list(contract_dir.glob(".*.tmp"))


def test_contract_publication_race_preserves_winner_and_removes_loser_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch = importlib.import_module("services.md.launch_contract")
    coordinates = tmp_path / "prepared" / "system.gro"
    topology = tmp_path / "prepared" / "topol.top"
    coordinates.parent.mkdir()
    coordinates.write_bytes(b"loser coordinates\n")
    topology.write_bytes(b"loser topology\n")
    winner = b'{"concurrent": "winner"}\n'
    real_link = launch.os.link

    def race_link(source: str | Path, destination: str | Path, *args: Any, **kwargs: Any) -> None:
        destination_path = Path(destination)
        if destination_path.name == "md_job_config.json" and not destination_path.exists():
            destination_path.write_bytes(winner)
        real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(launch.os, "link", race_link)

    with pytest.raises(Exception) as error:
        launch.materialize_md_job_spec(
            params={"md_job_spec": _prepared_spec()},
            job_id="contract-publication-race",
            output_dir=tmp_path / "out",
            resolve_runtime_path=lambda value: str(coordinates if value.endswith(".gro") else topology),
            chemistry_catalog=_catalog(),
        )

    contract_dir = tmp_path / "out" / "inputs"
    assert getattr(error.value, "code", None) == "MD_LAUNCH_OUTPUT_CONFLICT"
    assert getattr(error.value, "status_code", None) == 409
    assert (contract_dir / "md_job_config.json").read_bytes() == winner
    assert not (contract_dir / "coordinates.gro").exists()
    assert not (contract_dir / "topology.top").exists()
    assert not list(contract_dir.glob(".*.tmp"))


def test_worker_rejects_snapshot_tampering_before_input_use(tmp_path: Path) -> None:
    profile = _catalog().get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    materialized = _materialize_structure(tmp_path, _spec(profile))
    config_path = Path(materialized["md_job_config"])
    snapshot = Path(materialized["md_job_spec"]["input"]["structure"])
    snapshot.chmod(0o600)
    snapshot.write_text("tampered\n", encoding="utf-8")
    contract_module = importlib.import_module("scripts.bms_md.contract")
    loader = getattr(contract_module, "load_verified_job_config", None)
    assert loader is not None, "worker verified-config loader is missing"

    with pytest.raises(Exception) as error:
        loader(config_path)

    assert getattr(error.value, "code", None) == "MD_INPUT_SNAPSHOT_MISMATCH"


def test_worker_rejects_declared_topology_include_tampering(tmp_path: Path) -> None:
    materialized = _materialize_prepared_topology(
        tmp_path,
        '#include "nested/atoms.itp"\n',
        sidecars={"nested/atoms.itp": "[ atoms ]\n"},
    )
    config_path = Path(materialized["md_job_config"])
    closure = materialized["md_job_spec"]["input"]["topology_closure"]
    include_snapshot = Path(closure["root"]) / closure["files"][0]["path"]
    include_snapshot.chmod(0o600)
    include_snapshot.write_text("tampered include\n", encoding="utf-8")
    contract_module = importlib.import_module("scripts.bms_md.contract")

    with pytest.raises(Exception) as error:
        contract_module.prepare_verified_worker_inputs(config_path, tmp_path / "worker")

    assert getattr(error.value, "code", None) == "MD_INPUT_SNAPSHOT_MISMATCH"


def test_worker_private_topology_closure_copies_only_manifest_files_and_survives_snapshot_replacement(
    tmp_path: Path,
) -> None:
    materialized = _materialize_prepared_topology(
        tmp_path,
        '#include "nested/atoms.itp"\n',
        sidecars={
            "nested/atoms.itp": "[ atoms ]\n",
            "undeclared.itp": "not declared\n",
        },
    )
    contract_module = importlib.import_module("scripts.bms_md.contract")
    rewritten = contract_module.prepare_verified_worker_inputs(
        Path(materialized["md_job_config"]),
        tmp_path / "worker",
    )

    private_topology = Path(rewritten["input"]["topology"])
    private_root = Path(rewritten["input"]["topology_closure"]["root"])
    private_include = private_root / "nested" / "atoms.itp"
    assert private_topology.parent == private_root
    assert private_include.read_text(encoding="utf-8") == "[ atoms ]\n"
    assert not (private_root / "undeclared.itp").exists()
    original_private = private_include.read_bytes()
    snapshot_include = Path(materialized["md_job_spec"]["input"]["topology_closure"]["root"]) / "nested/atoms.itp"
    snapshot_include.chmod(0o600)
    snapshot_include.write_bytes(b"replacement after preparation\n")

    assert private_include.read_bytes() == original_private
    assert private_include.stat().st_mode & 0o222 == 0


def test_worker_private_structure_copy_survives_snapshot_replacement(tmp_path: Path) -> None:
    profile = _catalog().get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    materialized = _materialize_structure(tmp_path, _spec(profile))
    contract_module = importlib.import_module("scripts.bms_md.contract")
    rewritten = contract_module.prepare_verified_worker_inputs(
        Path(materialized["md_job_config"]),
        tmp_path / "worker",
    )
    private_structure = Path(rewritten["input"]["structure"])
    snapshot = Path(materialized["md_job_spec"]["input"]["structure"])
    snapshot.chmod(0o600)
    snapshot.write_text("replacement after preparation\n", encoding="utf-8")

    assert private_structure.read_bytes() == ONE_AKI_FIXTURE.read_bytes()
    assert private_structure.stat().st_mode & 0o222 == 0


def test_all_md_execution_entrypoints_prepare_private_verified_inputs_before_engine_use() -> None:
    entrypoint_expectations = {
        REPO_ROOT / "scripts" / "bms_md" / "adapters" / "gromacs.py": "allocation = assert_single_cuda_device",
        REPO_ROOT / "scripts" / "bms_md" / "adapters" / "openmm.py": "assert_single_cuda_device(config)",
        REPO_ROOT / "scripts" / "bms_md" / "gromacs_pipeline.py": "version_output = _run_command",
        REPO_ROOT / "scripts" / "bms_md" / "openmm_pipeline.py": "allocation = assert_single_cuda_device",
    }

    for path, first_engine_use in entrypoint_expectations.items():
        source = path.read_text(encoding="utf-8")
        assert source.index("prepare_verified_worker_inputs") < source.index(first_engine_use)


def test_prepared_v1_rejects_any_attached_profile_claim(tmp_path: Path) -> None:
    coordinates = tmp_path / "system.gro"
    topology = tmp_path / "topol.top"
    coordinates.write_text("prepared\n", encoding="utf-8")
    topology.write_text("prepared\n", encoding="utf-8")
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    spec = _prepared_spec()
    spec["preparation"].update(
        chemistry_profile_id=profile["id"],
        chemistry_profile_sha256=profile["profile_sha256"],
        chemistry_profile_scope="smoke_auto",
    )

    with pytest.raises(ChemistryProfileSelectionError) as error:
        materialize_md_job_spec(
            params={"md_job_spec": spec},
            job_id="parent-job-123",
            output_dir=tmp_path / "out",
            resolve_runtime_path=lambda value: str(coordinates if value.endswith(".gro") else topology),
            chemistry_catalog=catalog,
        )

    assert error.value.code == "MD_CHEMISTRY_PROFILE_FORBIDDEN"


def test_structure_profile_engine_mismatch_is_rejected(tmp_path: Path) -> None:
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    spec = _spec(profile)
    spec["engine"] = "openmm"
    for stage in ("minimization", "nvt", "npt"):
        spec["stages"][stage]["enabled"] = False

    with pytest.raises(ChemistryProfileSelectionError) as error:
        _materialize_structure(tmp_path, spec, catalog=catalog)

    assert error.value.code == "MD_CHEMISTRY_COMBINATION_UNSUPPORTED"


def test_input_spec_is_not_mutated_during_materialization(tmp_path: Path) -> None:
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    source = _spec(profile)
    original = copy.deepcopy(source)

    _materialize_structure(tmp_path, source, catalog=catalog)

    assert source == original


def test_json_schema_accepts_smoke_profile_and_prepared_external_assurance_contracts(tmp_path: Path) -> None:
    schema = json.loads(MD_JOB_SCHEMA_PATH.read_text(encoding="utf-8"))
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    structure_contract = _materialize_structure(tmp_path, _spec(profile), catalog=catalog)["md_job_spec"]

    coordinates = tmp_path / "schema-system.gro"
    topology = tmp_path / "schema-topol.top"
    coordinates.write_text("prepared\n", encoding="utf-8")
    topology.write_text("prepared\n", encoding="utf-8")
    prepared_contract = materialize_md_job_spec(
        params={"md_job_spec": _prepared_spec()},
        job_id="prepared-schema-job",
        output_dir=tmp_path / "prepared-out",
        resolve_runtime_path=lambda value: str(coordinates if value.endswith(".gro") else topology),
        chemistry_catalog=catalog,
    )["md_job_spec"]

    validate_json_schema(instance=structure_contract, schema=schema)
    validate_json_schema(instance=prepared_contract, schema=schema)


@pytest.mark.parametrize(
    "mixed_input",
    [
        {"structure": "1aki.pdb", "coordinates": "system.gro"},
        {"structure": "1aki.pdb", "topology": "topol.top"},
        {"structure": "1aki.pdb", "coordinates": "system.gro", "topology": "topol.top"},
    ],
)
def test_json_schema_rejects_every_mixed_input_shape(mixed_input: dict[str, str]) -> None:
    schema = json.loads(MD_JOB_SCHEMA_PATH.read_text(encoding="utf-8"))
    profile = _catalog().get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    contract = _spec(profile)
    contract["input"] = mixed_input

    with pytest.raises(ValidationError):
        validate_json_schema(instance=contract, schema=schema)


def test_json_schema_requires_at_least_one_minute_checkpoint_interval() -> None:
    schema = json.loads(MD_JOB_SCHEMA_PATH.read_text(encoding="utf-8"))
    contract = _prepared_spec(engine="openmm")
    contract["stages"]["production"]["checkpoint_interval_minutes"] = 0.999

    with pytest.raises(ValidationError):
        validate_json_schema(instance=contract, schema=schema)
