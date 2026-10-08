from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import rfc8785
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR = REPO_ROOT / "scripts/build_restriction_enzyme_catalog.py"
CATALOG_SCHEMA = REPO_ROOT / "schemas/molbio/restriction_enzyme_catalog_v1.schema.json"
CATALOG = REPO_ROOT / "platform/api/config/molbio/restriction/restriction_enzyme_catalog_v1.json"
MANIFEST = REPO_ROOT / "platform/api/config/molbio/restriction/restriction_enzyme_catalog_manifest_v1.json"
DIGEST_V2_SCHEMA = REPO_ROOT / "schemas/ngs_molbio/molbio-restriction_digest-v2.schema.json"
SCHEMA_REGISTRY = REPO_ROOT / "platform/api/config/ngs_molbio/schema_registry_v2.json"
CAPABILITY_INVENTORY = REPO_ROOT / "platform/api/config/ngs_molbio/capability_inventory_v2.json"
ATTRIBUTION = REPO_ROOT / "docs/scientific-sources/restriction-enzyme-catalog-attribution.md"
COORDINATE_CONTRACT = REPO_ROOT / "docs/contracts/restriction-enzyme-catalog-api-v2.md"
SHA256_PATTERN = "^[0-9a-f]{64}$"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_digest(document: dict[str, object], field: str) -> str:
    preimage = copy.deepcopy(document)
    preimage.pop(field)
    return hashlib.sha256(rfc8785.dumps(preimage)).hexdigest()


def _run_generator(catalog: Path, manifest: Path) -> None:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--catalog-output",
            str(catalog),
            "--manifest-output",
            str(manifest),
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_phase0_source_contract_assets_exist() -> None:
    for path in (
        GENERATOR,
        CATALOG_SCHEMA,
        CATALOG,
        MANIFEST,
        DIGEST_V2_SCHEMA,
        ATTRIBUTION,
        COORDINATE_CONTRACT,
    ):
        assert path.is_file(), f"missing Phase 0 source-contract asset: {path.relative_to(REPO_ROOT)}"


def test_checked_catalog_matches_pinned_source_counts_and_capabilities() -> None:
    catalog = _load(CATALOG)
    records = catalog["records"]
    assert isinstance(records, list)
    assert catalog["source"]["package"] == "biopython"
    assert catalog["source"]["package_version"] == "1.87"
    assert catalog["source"]["embedded_rebase_release"] == "REBASE_EMBOSS_404_2024"
    assert catalog["source"]["dictionary_sha256"] == "2a79099295dbad6061ea67a11e053787c591fcb2eb10fc8c0f89ead908dfa02b"
    assert catalog["counts"] == {
        "biopython_source_records": 1088,
        "curated_nickase_supplement_records": 4,
        "total_discoverable": 1092,
        "geometry_ready_double_strand": 754,
        "commercial_geometry_ready_double_strand": 623,
        "recognition_only": 334,
        "nicking_analysis_only": 4,
    }
    assert len(records) == 1092
    assert sum(row["source"]["kind"] == "biopython_restriction_dictionary" for row in records) == 1088
    assert sum(row["source"]["kind"] == "bms_curated_rebase_nickase" for row in records) == 4
    assert sum(row["analysis_capability"] == "digest_simulation" for row in records) == 754
    assert sum(row["analysis_capability"] == "recognition_only" for row in records) == 334
    assert sum(row["analysis_capability"] == "nicking_analysis" for row in records) == 4
    assert sum(
        row["analysis_capability"] == "digest_simulation"
        and row["supplier_provenance"]["reported_commercial"]
        for row in records
    ) == 623


def test_catalog_records_are_unique_sorted_digest_bound_and_fail_closed() -> None:
    catalog = _load(CATALOG)
    records = catalog["records"]
    assert isinstance(records, list)
    assert [row["enzyme_id"] for row in records] == sorted(
        (row["enzyme_id"] for row in records), key=str.casefold
    )
    assert len({row["enzyme_id"] for row in records}) == 1092
    assert len({row["canonical_name"] for row in records}) == 1092
    for row in records:
        assert row["record_sha256"] == _canonical_digest(row, "record_sha256")
        assert set(row["recognition"]["site_iupac"]) <= set("ACGTRYSWKMBDHVN")
        cleavage = row["cleavage"]
        if row["analysis_capability"] == "digest_simulation":
            assert cleavage["status"] == "known_double_strand"
            assert cleavage["events"]
            assert cleavage["source_fields"]["fst5"] is not None
            assert cleavage["source_fields"]["fst3"] is not None
            assert row["exclusion_reason"] is None
        elif row["analysis_capability"] == "nicking_analysis":
            assert cleavage["status"] == "known_single_strand_nick"
            assert cleavage["events"] == []
            assert cleavage["nick"] is not None
            assert (cleavage["nick"]["top_offset"] is None) != (
                cleavage["nick"]["bottom_offset"] is None
            )
            assert row["exclusion_reason"] == "nicking_enzyme_not_digestible"
        else:
            assert cleavage["status"] == "unknown"
            assert cleavage["events"] == []
            assert cleavage["nick"] is None
            assert row["exclusion_reason"] == "primary_cut_geometry_incomplete"
    assert catalog["content_sha256"] == _canonical_digest(catalog, "content_sha256")
    assert CATALOG.read_bytes() == rfc8785.dumps(catalog)


def test_catalog_uses_bms_identity_and_keeps_source_ids_as_provenance() -> None:
    records = _load(CATALOG)["records"]
    assert isinstance(records, list)
    base_records = [row for row in records if row["source"]["kind"] == "biopython_restriction_dictionary"]
    missing_source_ids = [row for row in base_records if row["source"]["record_id"] is None]
    assert len(missing_source_ids) == 5
    assert all(row["enzyme_id"] and row["canonical_name"] for row in missing_source_ids)
    assert all(row["id_policy"] == "canonical_name_with_deterministic_collision_suffix" for row in records)
    assert all(row["aliases"] == [] for row in records)
    assert all(row["supplier_provenance"]["availability_claim"] == "not_evaluated" for row in records)


def test_generator_converts_primary_and_secondary_geometry_to_interbase_offsets() -> None:
    records = {row["canonical_name"]: row for row in _load(CATALOG)["records"]}
    expected = {
        "EcoRI": [(1, 5)],
        "BsaI": [(7, 11)],
        "FokI": [(14, 18)],
        "BcgI": [(-10, -12), (24, 22)],
    }
    for name, offsets in expected.items():
        events = records[name]["cleavage"]["events"]
        assert [(event["top_offset"], event["bottom_offset"]) for event in events] == offsets
    assert sum(len(row["cleavage"]["events"]) == 2 for row in records.values()) == 25


def test_curated_nickases_bind_exact_rebase_receipts_and_one_strand_cut() -> None:
    records = {row["canonical_name"]: row for row in _load(CATALOG)["records"]}
    expected = {
        "Nt.BbvCI": ("top", 2, "5790", "2017-08-10", "b592320c5331e8c12f397fbd1040109848acdec219b98382523f22db554fed82"),
        "Nb.BbvCI": ("bottom", 5, "5789", "2017-08-10", "9c19e7dddb53bc46f41a26ad93dda5369fd44ee16a36e205f9ce18729806b658"),
        "Nt.BspQI": ("top", 8, "16997", "2011-06-29", "b5d368dbadb89844ac4b4fc1c1883f8b2297294b28ba18dd37b90b0a5c957fc9"),
        "Nb.BssSI": ("bottom", 5, "140982", "2018-02-21", "96b295677ebf2535b3687a2dc47cc26017db7f758ee9809abdafe6510c542bbd"),
    }
    for name, (strand, offset, record_id, modified, page_sha256) in expected.items():
        row = records[name]
        assert row["source"]["kind"] == "bms_curated_rebase_nickase"
        assert row["source"]["record_id"] == record_id
        assert row["source"]["retrieved_on"] == "2026-08-31"
        assert row["source"]["record_modified_on"] == modified
        assert row["source"]["page_sha256"] == page_sha256
        nick = row["cleavage"]["nick"]
        assert nick["strand"] == strand
        assert nick[f"{strand}_offset"] == offset
        assert nick[f"{'bottom' if strand == 'top' else 'top'}_offset"] is None
        assert row["analysis_capability"] == "nicking_analysis"
        assert row["exclusion_reason"] == "nicking_enzyme_not_digestible"


def test_manifest_binds_sorted_records_and_deterministic_timestamp_policy() -> None:
    catalog = _load(CATALOG)
    manifest = _load(MANIFEST)
    assert manifest["schema_version"] == 1
    assert manifest["catalog_id"] == catalog["catalog_id"]
    assert manifest["catalog_content_sha256"] == catalog["content_sha256"]
    assert manifest["source"]["package_version"] == "1.87"
    assert manifest["source"]["embedded_rebase_release"] == "REBASE_EMBOSS_404_2024"
    assert manifest["generated_timestamp"] is None
    assert manifest["generated_timestamp_policy"] == "omitted_for_deterministic_release_bytes"
    assert manifest["counts"] == catalog["counts"]
    record_manifest = manifest["records"]
    assert record_manifest == sorted(record_manifest, key=lambda row: row["enzyme_id"].casefold())
    assert record_manifest == [
        {"enzyme_id": row["enzyme_id"], "record_sha256": row["record_sha256"]}
        for row in catalog["records"]
    ]
    assert manifest["content_sha256"] == _canonical_digest(manifest, "content_sha256")
    assert MANIFEST.read_bytes() == rfc8785.dumps(manifest)


def test_generator_is_byte_deterministic_across_two_clean_outputs(tmp_path: Path) -> None:
    first_catalog = tmp_path / "first-catalog.json"
    first_manifest = tmp_path / "first-manifest.json"
    second_catalog = tmp_path / "second-catalog.json"
    second_manifest = tmp_path / "second-manifest.json"
    _run_generator(first_catalog, first_manifest)
    _run_generator(second_catalog, second_manifest)
    assert first_catalog.read_bytes() == second_catalog.read_bytes() == CATALOG.read_bytes()
    assert first_manifest.read_bytes() == second_manifest.read_bytes() == MANIFEST.read_bytes()


def test_catalog_and_v2_operation_schemas_are_closed_and_valid() -> None:
    catalog_schema = _load(CATALOG_SCHEMA)
    operation_schema = _load(DIGEST_V2_SCHEMA)
    Draft202012Validator.check_schema(catalog_schema)
    Draft202012Validator.check_schema(operation_schema)
    Draft202012Validator(catalog_schema).validate(_load(CATALOG))
    assert catalog_schema["additionalProperties"] is False
    assert catalog_schema["$id"] == "bms.molbio.restriction-enzyme-catalog.v1"
    assert operation_schema["additionalProperties"] is False
    assert operation_schema["$id"] == "bms.operation-parameters.molbio.restriction_digest.v2"
    for authority in ("source", "catalog", "simulation_policy", "result_binding"):
        assert operation_schema["properties"][authority]["additionalProperties"] is False


def test_v2_operation_contract_requires_exact_authorities_and_rejects_browser_geometry() -> None:
    schema = _load(DIGEST_V2_SCHEMA)
    validator = Draft202012Validator(schema)
    valid = {
        "source": {
            "sequence_id": "sequence-1",
            "revision_id": "revision-7",
            "content_sha256": "a" * 64,
        },
        "catalog": {
            "catalog_id": "biopython-rebase-404-bms-v1",
            "content_sha256": "b" * 64,
        },
        "enzyme_ids": ["EcoRI"],
        "simulation_policy": {
            "algorithm_id": "bms-restriction-digest",
            "algorithm_version": "1",
            "topology": "linear",
            "recognition_certainty": "definite_only",
            "unknown_geometry": "reject",
            "linear_out_of_bounds": "reject",
            "overlapping_geometry": "reject_non_identical",
            "circular_coordinates": "modulo_with_unwrapped_derivation",
        },
        "result_binding": {
            "request_sha256": "c" * 64,
            "simulation_sha256": "d" * 64,
        },
        "idempotency_key": "digest-sequence-1-r7",
        "persistence_mode": "operation_only",
        "fragment_name_prefix": None,
    }
    assert not list(validator.iter_errors(valid))
    for forbidden in (
        {"site": "GAATTC"},
        {"cut_index": 3},
        {"chemistry": "invented"},
    ):
        invalid = copy.deepcopy(valid)
        invalid["enzyme_ids"] = [{"enzyme_id": "EcoRI", **forbidden}]
        assert list(validator.iter_errors(invalid)), forbidden
    invalid_root = {**valid, "site": "GAATTC"}
    assert list(validator.iter_errors(invalid_root))


def test_active_registry_and_inventory_select_v2_without_geometry_authority() -> None:
    registry = _load(SCHEMA_REGISTRY)
    rows = {row["schema_id"]: row for row in registry["entries"]}
    assert "bms.operation-parameters.molbio.restriction_digest.v1" in rows
    assert "bms.operation-parameters.molbio.restriction_digest.v2" in rows
    assert registry["content_sha256"] == _canonical_digest(registry, "content_sha256")
    v2_row = rows["bms.operation-parameters.molbio.restriction_digest.v2"]
    assert v2_row["schema_sha256"] == hashlib.sha256(DIGEST_V2_SCHEMA.read_bytes()).hexdigest()
    assert v2_row["schema_canonical_sha256"] == hashlib.sha256(
        rfc8785.dumps(_load(DIGEST_V2_SCHEMA))
    ).hexdigest()

    inventory = _load(CAPABILITY_INVENTORY)
    capability = next(
        row for row in inventory["capabilities"] if row["capability_id"] == "molbio.restriction_digest"
    )
    assert capability["parameter_schema_id"] == "bms.operation-parameters.molbio.restriction_digest.v2"
    assert capability["parameter_schema_sha256"] == v2_row["schema_sha256"]
    assert capability["native_mapping"]["source"] == "POST /api/molbio/restriction/digests"
    assert capability["exposure_state"] == "disabled"
    forbidden = {"site", "cut_index", "chemistry"}
    assert forbidden.isdisjoint(capability["observed_parameter_keys"])
    assert forbidden.isdisjoint(capability["classified_parameter_keys"])
    assert inventory["content_sha256"] == _canonical_digest(inventory, "content_sha256")


def test_source_notices_and_coordinate_contract_state_required_boundaries() -> None:
    attribution = ATTRIBUTION.read_text(encoding="utf-8")
    for phrase in (
        "Biopython 1.87",
        "REBASE EMBOSS release 404",
        "Biopython License Agreement",
        "no charge",
        "separate reviewed gate",
        "not current supplier availability",
    ):
        assert phrase in attribution
    contract = COORDINATE_CONTRACT.read_text(encoding="utf-8")
    for phrase in (
        "zero-based interbase boundaries",
        "[0, L]",
        "modulo L",
        "unwrapped derivation",
        "mirrors and swaps strands",
        "recognition match never implies a cut",
        "Unknown geometry cannot simulate fragments",
    ):
        assert phrase in contract
