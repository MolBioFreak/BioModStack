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


def _prepared_spec(*, engine: str = "gromacs") -> dict[str, Any]:
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
    with pytest.raises(ValueError, match="native job schema"):
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

    with pytest.raises(ValueError, match="native job schema"):
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
def test_resource_contract_rejects_output_admission_over_boundaries(
    tmp_path: Path,
    mutation: Any,
) -> None:
    spec = _spec(_catalog().get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1") or {})
    spec["replicas"] = 8
    mutation(spec)

    with pytest.raises(Exception) as error:
        _materialize_structure(tmp_path, spec)

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
def test_resource_contract_rejects_over_boundary_values_before_persistence(
    tmp_path: Path,
    mutation: Any,
) -> None:
    spec = _spec(_catalog().get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1") or {})
    mutation(spec)

    with pytest.raises(Exception) as error:
        _materialize_structure(tmp_path, spec)

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


def test_smoke_scope_materializes_non_example_structure(tmp_path: Path) -> None:
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    selected = tmp_path / "selected.pdb"
    # A parseable independent single-residue source, not modified 1AKI bytes.
    selected.write_text(
        "ATOM      1  N   ALA A   1      11.104  13.207   9.447  1.00 20.00           N  \n"
        "ATOM      2  CA  ALA A   1      12.560  13.207   9.447  1.00 20.00           C  \n"
        "ATOM      3  C   ALA A   1      13.100  14.600   9.447  1.00 20.00           C  \n"
        "ATOM      4  O   ALA A   1      12.500  15.600   9.447  1.00 20.00           O  \n"
        "ATOM      5  CB  ALA A   1      13.100  12.400  10.600  1.00 20.00           C  \nTER\nEND\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(selected.read_bytes()).hexdigest()
    assert digest != profile["launch_constraints"]["structure_sha256"]
    result = _materialize_structure(tmp_path, _spec(profile), catalog=catalog, runtime_structure=selected)
    snapshot = result["md_job_spec"]["input"]
    assert snapshot["structure_sha256"] == digest
    assert Path(snapshot["structure"]).read_bytes() == selected.read_bytes()


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
            params={"md_job_spec": preview, "md_source_provenance": {"source_sha256": ONE_AKI_SHA256}},
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
        REPO_ROOT / "scripts" / "bms_md" / "gromacs_pipeline.py": "version_output = _run_command",
    }

    for path, first_engine_use in entrypoint_expectations.items():
        source = path.read_text(encoding="utf-8")
        assert source.index("prepare_verified_worker_inputs") < source.index(first_engine_use)


def test_input_spec_is_not_mutated_during_materialization(tmp_path: Path) -> None:
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    source = _spec(profile)
    original = copy.deepcopy(source)

    _materialize_structure(tmp_path, source, catalog=catalog)

    assert source == original


def test_json_schema_accepts_smoke_profile_contract(tmp_path: Path) -> None:
    schema = json.loads(MD_JOB_SCHEMA_PATH.read_text(encoding="utf-8"))
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    structure_contract = _materialize_structure(tmp_path, _spec(profile), catalog=catalog)["md_job_spec"]

    validate_json_schema(instance=structure_contract, schema=schema)


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
    contract = _spec(_catalog().get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1") or {})
    contract["stages"]["production"]["checkpoint_interval_minutes"] = 0.999

    with pytest.raises(ValidationError):
        validate_json_schema(instance=contract, schema=schema)
