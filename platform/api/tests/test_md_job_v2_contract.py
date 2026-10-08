from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
for candidate in (str(API_ROOT), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from services.md.chemistry_catalog import ChemistryCatalog, RuntimeProbeResult  # noqa: E402
from services.md.launch_contract import MDLaunchError, materialize_md_job_spec, normalize_md_job_spec  # noqa: E402

CATALOG_DIR = API_ROOT / "config" / "md_chemistry_profiles"
ONE_AKI_FIXTURE = API_ROOT / "tests" / "fixtures" / "md" / "1AKI.pdb"
GROMACS_SIF_SHA256 = "97c117ea07496c0d1b13d80be84d33345b89063b47ccfb83f6cbff0145f1385b"


def _catalog() -> ChemistryCatalog:
    return ChemistryCatalog(config_dir=CATALOG_DIR, probe=lambda: RuntimeProbeResult(
        runtime_id="gromacs-2025.3", runtime_version="2025.3", available=True,
        asset_ids=frozenset({"amber99sb-ildn.ff"}), checked_at="2026-07-29T00:00:00Z",
        sif_sha256=GROMACS_SIF_SHA256,
    ))


def _v2_spec(profile: dict[str, Any], catalog_digest: str) -> dict[str, Any]:
    return {
        "schema": "bms.md.job.v2", "job_id": "assigned-by-server", "engine": "gromacs",
        "replicas": 1, "random_seed": 20260729, "input": {"structure": str(ONE_AKI_FIXTURE)},
        "chemistry": {"profile_id": profile["id"], "profile_sha256": profile["profile_sha256"],
                      "catalog_digest": catalog_digest,
                      "requested_scope": profile["scientific_validation"]["scope"]["launch_scope"]},
        "preparation": {"box_type": "dodecahedron", "padding_nm": 1.0, "salt_molar": 0.15, "neutralize": True},
        "stages": {
            "minimization": {"enabled": True, "steps": 50000, "force_tolerance_kj_mol_nm": 1000},
            "nvt": {"enabled": True, "steps": 50000, "temperature_k": 300},
            "npt": {"enabled": True, "steps": 50000, "temperature_k": 300, "pressure_bar": 1},
            "production": {"enabled": True, "steps": 5000, "timestep_fs": 2, "temperature_k": 300,
                           "pressure_bar": 1, "checkpoint_interval_minutes": 15,
                           "trajectory_interval_steps": 500, "energy_interval_steps": 100},
        },
        "execution": {"gpu_id": "0", "ntmpi": 1, "ntomp": 8, "gpu_offload": "full", "pin": "on"},
    }


def test_v2_schema_is_available_and_rejects_free_form_chemistry() -> None:
    schema = json.loads((REPO_ROOT / "schemas" / "md_job_v2.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["schema"] == {"const": "bms.md.job.v2"}
    assert schema["properties"]["chemistry"]["additionalProperties"] is False
    assert "force_field" not in schema["properties"]["chemistry"]["properties"]
    assert "water_model" not in schema["properties"]["chemistry"]["properties"]
    assert "full_forces" in schema["properties"]["execution"]["properties"]["gpu_offload"]["enum"]
    assert "approved_pack_ids" in schema["properties"]["chemistry"]["properties"]


def test_drt4_approved_pack_records_fail_closed_and_bind_required_chemistry() -> None:
    pack_dir = API_ROOT / "config" / "md_approved_packs"
    records = {
        path.stem: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in pack_dir.glob("*.yaml")
    }
    product = records["drt4_9vdp_wt_product_dna_tyr125_linkage_v1"]
    substrate = records["drt4_9vdo_wt_dna_dctp_2mn_v1"]
    assert product["installed"] is False and product["operator_enabled"] is False
    assert product["required_covalent_links"] == [{
        "protein_residue": "TYR:125", "protein_atom": "OH",
        "partner": "product_DNA_5prime_phosphate", "partner_atom": "P",
    }]
    assert product["linkage_validation"]["authoritative_mmcif_parsed"] is True
    assert product["linkage_validation"]["required_link_observed"] is False
    assert product["linkage_validation"]["deposited_struct_conn_present"] is False
    assert substrate["dctp"]["required"] is True
    assert substrate["manganese"]["element"] == "Mn" and substrate["manganese"]["count_per_active_site"] == 2
    assert substrate["scientific_validation"]["validated"] is False


def test_v2_normalization_binds_catalog_generation_and_server_resolved_profile() -> None:
    catalog = _catalog(); view = catalog.view(); profile = view.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    normalized = normalize_md_job_spec(params={"md_job_spec": _v2_spec(profile, view.catalog_digest)},
        job_id="v2-job", resolve_runtime_path=lambda value: value, chemistry_catalog=catalog, chemistry_view=view)
    assert normalized["schema"] == "bms.md.job.v2" and normalized["job_id"] == "v2-job"
    assert normalized["chemistry"] == {
        "profile_id": profile["id"], "profile_sha256": profile["profile_sha256"],
        "catalog_digest": view.catalog_digest, "requested_scope": "smoke_auto",
        "assurance": "smoke_fixture", "family": "amber", "version": profile["version"],
        "resolved_preparation": profile["v1_preparation"], "runtime_identity": profile["runtime_identity"],
    }
    assert "force_field" not in normalized["preparation"] and "water_model" not in normalized["preparation"]


def test_create_route_keeps_validation_preview_out_of_caller_owned_spec() -> None:
    """The raw request must reach materialization; resolved previews are server-only."""
    source = (API_ROOT / "routers" / "jobs.py").read_text(encoding="utf-8")
    start = source.index('if job_data.model_id == "molecular_dynamics" and job_data.mode == "simulate":')
    end = source.index("# Skip validation for template jobs", start)
    preview_block = source[start:end]
    assert "normalize_md_job_spec(" in preview_block
    assert 'job_data.params["md_job_spec"] = normalize_md_job_spec(' not in preview_block


def test_v2_normalization_rejects_stale_catalog_generation() -> None:
    catalog = _catalog(); view = catalog.view(); profile = view.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    with pytest.raises(MDLaunchError) as error:
        normalize_md_job_spec(params={"md_job_spec": _v2_spec(profile, "f" * 64)}, job_id="v2-job",
            resolve_runtime_path=lambda value: value, chemistry_catalog=catalog, chemistry_view=view)
    assert error.value.code == "MD_CHEMISTRY_CATALOG_STALE" and error.value.status_code == 409


def test_v2_normalization_rejects_unapproved_drt4_pack_without_silently_dropping_it() -> None:
    catalog = _catalog(); view = catalog.view(); profile = view.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    spec = _v2_spec(profile, view.catalog_digest)
    spec["chemistry"]["approved_pack_ids"] = ["drt4_9vdo_wt_dna_dctp_2mn_v1"]
    with pytest.raises(MDLaunchError) as error:
        normalize_md_job_spec(
            params={"md_job_spec": spec}, job_id="drt4-blocked",
            resolve_runtime_path=lambda value: value,
            chemistry_catalog=catalog, chemistry_view=view,
        )
    assert error.value.code == "MD_APPROVED_PACK_UNAVAILABLE"
    assert error.value.status_code == 409


def test_v2_materialization_persists_server_bound_contract_and_immutable_snapshot(tmp_path: Path) -> None:
    catalog = _catalog(); view = catalog.view(); profile = view.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    materialized = materialize_md_job_spec(
        params={"md_job_spec": _v2_spec(profile, view.catalog_digest)}, job_id="v2-materialized",
        output_dir=tmp_path / "out", resolve_runtime_path=lambda _value: str(ONE_AKI_FIXTURE),
        chemistry_catalog=catalog,
    )
    persisted = json.loads(Path(materialized["md_job_config"]).read_text(encoding="utf-8"))
    snapshot = Path(persisted["input"]["structure"])
    assert persisted["schema"] == "bms.md.job.v2"
    assert persisted["chemistry"]["profile_id"] == profile["id"]
    assert persisted["input"]["structure_sha256"] == profile["launch_constraints"]["structure_sha256"]
    assert snapshot.read_bytes() == ONE_AKI_FIXTURE.read_bytes()
    assert snapshot.stat().st_mode & 0o222 == 0


def test_capabilities_advertise_v2_without_removing_retained_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    import main
    from routers import molecular_dynamics
    monkeypatch.setattr(molecular_dynamics, "get_chemistry_catalog", lambda: _catalog())
    with TestClient(main.app) as client:
        response = client.get("/api/molecular-dynamics/capabilities")
    assert response.status_code == 200
    assert response.json()["contract_schemas"] == ["bms.md.job.v2", "bms.md.job.v1"]


def test_drt4_approved_pack_inventory_is_public_bounded_and_fail_closed() -> None:
    import main
    with TestClient(main.app) as client:
        response = client.get("/api/molecular-dynamics/approved-packs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema"] == "bms.md.approved-pack-inventory.v1"
    assert payload["bounded"] is True and payload["count"] == 3
    assert payload["selectable_pack_ids"] == []
    by_id = {item["id"]: item for item in payload["packs"]}
    assert by_id["drt4_9vdo_wt_dna_dctp_2mn_v1"]["states"]["selectable"] is False
    assert by_id["drt4_9vdp_wt_product_dna_tyr125_linkage_v1"]["blockers"]
