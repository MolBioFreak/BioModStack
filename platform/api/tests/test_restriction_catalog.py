from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest
import rfc8785
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR = REPO_ROOT / "scripts/build_restriction_enzyme_catalog.py"
CATALOG_SCHEMA = REPO_ROOT / "schemas/molbio/restriction_enzyme_catalog_v1.schema.json"
CATALOG = REPO_ROOT / "platform/api/config/molbio/restriction/restriction_enzyme_catalog_v1.json"
MANIFEST = REPO_ROOT / "platform/api/config/molbio/restriction/restriction_enzyme_catalog_manifest_v1.json"
CHANGE_REPORT = REPO_ROOT / "platform/api/config/molbio/restriction/restriction_enzyme_catalog_change_report_v1.json"
DIGEST_V2_SCHEMA = REPO_ROOT / "schemas/ngs_molbio/molbio-restriction_digest-v2.schema.json"
SCHEMA_REGISTRY = REPO_ROOT / "platform/api/config/ngs_molbio/schema_registry_v2.json"
CAPABILITY_INVENTORY = REPO_ROOT / "platform/api/config/ngs_molbio/capability_inventory_v2.json"
ATTRIBUTION = REPO_ROOT / "docs/scientific-sources/restriction-enzyme-catalog-attribution.md"
COORDINATE_CONTRACT = REPO_ROOT / "docs/contracts/restriction-enzyme-catalog-api-v2.md"
PHASE2_BENCHMARK = REPO_ROOT / "docs/benchmarks/restriction-analysis-phase2.md"
SHA256_PATTERN = "^[0-9a-f]{64}$"
RESOURCE_POLICY_SHA256 = "790b363a2b6928fa6a7cd371ff7add77e46b36a3c8f987af49ef7ae6c434499f"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_digest(document: dict[str, object], field: str) -> str:
    preimage = copy.deepcopy(document)
    preimage.pop(field)
    return hashlib.sha256(rfc8785.dumps(preimage)).hexdigest()


def _run_generator(catalog: Path, manifest: Path, change_report: Path) -> None:
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
            "--change-report-output",
            str(change_report),
            "--initial-release",
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def _load_generator():
    specification = importlib.util.spec_from_file_location("restriction_catalog_generator_test", GENERATOR)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_phase0_source_contract_assets_exist() -> None:
    for path in (
        GENERATOR,
        CATALOG_SCHEMA,
        CATALOG,
        MANIFEST,
        CHANGE_REPORT,
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
            assert cleavage["nick"]["reverse_orientation"]["strand"] != cleavage["nick"]["strand"]
            assert cleavage["nick"]["reverse_orientation"]["boundary_offset"] == (
                row["recognition"]["length_bp"] - cleavage["nick"]["boundary_offset"]
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
    assert all(row["id_policy"] == "canonical_name_v1_casefold_unique" for row in records)
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
        "Nt.BbvCI": ("top", 2, "bottom", 5, "5790", "2017-08-10", "b592320c5331e8c12f397fbd1040109848acdec219b98382523f22db554fed82"),
        "Nb.BbvCI": ("bottom", 5, "top", 2, "5789", "2017-08-10", "9c19e7dddb53bc46f41a26ad93dda5369fd44ee16a36e205f9ce18729806b658"),
        "Nt.BspQI": ("top", 8, "bottom", -1, "16997", "2011-06-29", "b5d368dbadb89844ac4b4fc1c1883f8b2297294b28ba18dd37b90b0a5c957fc9"),
        "Nb.BssSI": ("bottom", 5, "top", 1, "140982", "2018-02-21", "96b295677ebf2535b3687a2dc47cc26017db7f758ee9809abdafe6510c542bbd"),
    }
    for name, (
        strand,
        boundary_offset,
        reverse_strand,
        reverse_boundary_offset,
        record_id,
        modified,
        page_sha256,
    ) in expected.items():
        row = records[name]
        assert row["source"]["kind"] == "bms_curated_rebase_nickase"
        assert row["source"]["record_id"] == record_id
        assert row["source"]["retrieved_on"] == "2026-08-31"
        assert row["source"]["record_modified_on"] == modified
        assert row["source"]["page_sha256"] == page_sha256
        nick = row["cleavage"]["nick"]
        assert nick["strand"] == strand
        assert nick["boundary_offset"] == boundary_offset
        assert nick["reverse_orientation"] == {
            "strand": reverse_strand,
            "boundary_offset": reverse_boundary_offset,
        }
        assert reverse_boundary_offset == row["recognition"]["length_bp"] - boundary_offset
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
    first_report = tmp_path / "first-change-report.json"
    second_catalog = tmp_path / "second-catalog.json"
    second_manifest = tmp_path / "second-manifest.json"
    second_report = tmp_path / "second-change-report.json"
    _run_generator(first_catalog, first_manifest, first_report)
    _run_generator(second_catalog, second_manifest, second_report)
    assert first_catalog.read_bytes() == second_catalog.read_bytes() == CATALOG.read_bytes()
    assert first_manifest.read_bytes() == second_manifest.read_bytes() == MANIFEST.read_bytes()
    assert first_report.read_bytes() == second_report.read_bytes() == CHANGE_REPORT.read_bytes()


def test_change_report_discriminates_every_review_class() -> None:
    generator = _load_generator()
    current = _load(CATALOG)
    templates = {row["enzyme_id"]: row for row in current["records"]}
    old_records = [copy.deepcopy(templates[name]) for name in ("EcoRI", "BsaI", "FokI", "BcgI", "AarI")]
    new_records = copy.deepcopy(old_records)
    new_records.pop(0)
    new_records.append(copy.deepcopy(templates["BamHI"]))
    new_records[0]["cleavage"]["events"][0]["top_offset"] += 1
    new_records[1]["relationships"]["neoschizomer_ids"] = ["synthetic-peer"]
    new_records[2]["supplier_provenance"]["historical_supplier_codes"] = ["SYNTHETIC"]

    old = {"catalog_id": "old", "content_sha256": "a" * 64, "records": old_records}
    new = {"catalog_id": "new", "content_sha256": "b" * 64, "records": new_records}
    report = generator.build_change_report(old, new)

    assert report["changes"]["additions"] == ["BamHI"]
    assert report["changes"]["removals"] == ["EcoRI"]
    assert report["changes"]["cleavage_geometry_changes"] == ["BsaI"]
    assert report["changes"]["relationship_changes"] == ["FokI"]
    assert report["changes"]["historical_supplier_code_commercial_report_changes"] == ["BcgI"]
    assert {row["enzyme_id"] for row in report["changes"]["record_changes"]} == {
        "BsaI", "FokI", "BcgI"
    }


def test_checked_initial_release_change_report_is_canonical_and_truthful() -> None:
    catalog = _load(CATALOG)
    report = _load(CHANGE_REPORT)
    assert report["prior_catalog"] is None
    assert report["current_catalog"] == {
        "catalog_id": catalog["catalog_id"],
        "content_sha256": catalog["content_sha256"],
        "biopython_source_records": 1088,
        "curated_nickase_supplement_records": 4,
        "total_discoverable": 1092,
    }
    assert report["summary"] == {
        "additions": 1092,
        "removals": 0,
        "recognition_changes": 0,
        "identity_changes": 0,
        "cleavage_geometry_changes": 0,
        "relationship_changes": 0,
        "source_provenance_changes": 0,
        "historical_supplier_code_commercial_report_changes": 0,
        "enzyme_kind_capability_exclusion_changes": 0,
        "record_changes": 0,
    }
    assert len(report["changes"]["additions"]) == 1092
    for change_class in (
        "removals",
        "cleavage_geometry_changes",
        "relationship_changes",
        "recognition_changes",
        "identity_changes",
        "source_provenance_changes",
        "historical_supplier_code_commercial_report_changes",
        "enzyme_kind_capability_exclusion_changes",
        "record_changes",
    ):
        assert report["changes"][change_class] == []
    assert report["content_sha256"] == _canonical_digest(report, "content_sha256")
    assert CHANGE_REPORT.read_bytes() == rfc8785.dumps(report)


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
    for authority in ("source", "catalog"):
        assert operation_schema["properties"][authority]["additionalProperties"] is False


def test_catalog_schema_rejects_every_scientific_state_contradiction() -> None:
    catalog_schema = _load(CATALOG_SCHEMA)
    record_schema = {
        "$schema": catalog_schema["$schema"],
        "$defs": catalog_schema["$defs"],
        "$ref": "#/$defs/record",
    }
    validator = Draft202012Validator(record_schema)
    records = {row["canonical_name"]: row for row in _load(CATALOG)["records"]}
    event = copy.deepcopy(records["EcoRI"]["cleavage"]["events"][0])
    top_nick = {
        "strand": "top",
        "boundary_offset": 1,
        "reverse_orientation": {"strand": "bottom", "boundary_offset": 5},
    }
    bottom_nick = {
        "strand": "bottom",
        "boundary_offset": 1,
        "reverse_orientation": {"strand": "top", "boundary_offset": 5},
    }
    cases = [
        ("known_status", records["EcoRI"], ("cleavage", "status"), "unknown"),
        ("known_event_cardinality", records["EcoRI"], ("cleavage", "events"), []),
        ("known_nick", records["EcoRI"], ("cleavage", "nick"), top_nick),
        ("known_kind", records["EcoRI"], ("enzyme_kind",), "nicking_endonuclease"),
        ("known_capability", records["EcoRI"], ("analysis_capability",), "recognition_only"),
        ("known_primary_source_pair", records["EcoRI"], ("cleavage", "source_fields", "fst3"), None),
        ("known_secondary_source_pair", records["EcoRI"], ("cleavage", "source_fields", "scd5"), 1),
        ("known_exclusion", records["EcoRI"], ("exclusion_reason",), "primary_cut_geometry_incomplete"),
        ("unknown_status", records["Aba13301I"], ("cleavage", "status"), "known_double_strand"),
        ("unknown_event_cardinality", records["Aba13301I"], ("cleavage", "events"), [event]),
        ("unknown_nick", records["Aba13301I"], ("cleavage", "nick"), bottom_nick),
        ("unknown_kind", records["Aba13301I"], ("enzyme_kind",), "double_strand_endonuclease"),
        ("unknown_capability", records["Aba13301I"], ("analysis_capability",), "digest_simulation"),
        ("unknown_source_pair", records["Aba13301I"], ("cleavage", "source_fields", "fst5"), 1),
        ("unknown_exclusion", records["Aba13301I"], ("exclusion_reason",), None),
        ("nick_status", records["Nt.BbvCI"], ("cleavage", "status"), "unknown"),
        ("nick_event_cardinality", records["Nt.BbvCI"], ("cleavage", "events"), [event]),
        ("nick_shape", records["Nt.BbvCI"], ("cleavage", "nick", "boundary_offset"), None),
        ("nick_kind", records["Nt.BbvCI"], ("enzyme_kind",), "double_strand_endonuclease"),
        ("nick_capability", records["Nt.BbvCI"], ("analysis_capability",), "digest_simulation"),
        ("nick_source_fields", records["Nt.BbvCI"], ("cleavage", "source_fields", "fst5"), 1),
        ("nick_exclusion", records["Nt.BbvCI"], ("exclusion_reason",), None),
        ("nick_source_receipt", records["Nt.BbvCI"], ("source", "page_sha256"), None),
    ]
    for label, source, path, replacement in cases:
        invalid = copy.deepcopy(source)
        target = invalid
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = replacement
        assert list(validator.iter_errors(invalid)), label


def test_catalog_schema_rejects_missing_or_same_strand_nick_reverse_orientation() -> None:
    catalog_schema = _load(CATALOG_SCHEMA)
    record_schema = {
        "$schema": catalog_schema["$schema"],
        "$defs": catalog_schema["$defs"],
        "$ref": "#/$defs/record",
    }
    validator = Draft202012Validator(record_schema)
    record = next(
        row for row in _load(CATALOG)["records"] if row["canonical_name"] == "Nt.BbvCI"
    )

    missing = copy.deepcopy(record)
    missing["cleavage"]["nick"].pop("reverse_orientation", None)
    assert list(validator.iter_errors(missing))

    same_strand = copy.deepcopy(record)
    same_strand["cleavage"]["nick"]["reverse_orientation"] = {
        "strand": same_strand["cleavage"]["nick"]["strand"],
        "boundary_offset": 5,
    }
    assert list(validator.iter_errors(same_strand))


def test_registered_v2_schema_has_exact_digest_save_request_accept_reject_parity() -> None:
    from pydantic import ValidationError
    from routers.molbio_restriction import DigestSaveRequest

    registry = _load(SCHEMA_REGISTRY)
    inventory = _load(CAPABILITY_INVENTORY)
    capability = next(
        row for row in inventory["capabilities"] if row["capability_id"] == "molbio.restriction_digest"
    )
    schema_id = capability["parameter_schema_id"]
    registry_row = next(row for row in registry["entries"] if row["schema_id"] == schema_id)
    schema_path = REPO_ROOT / registry_row["path"]
    schema = _load(schema_path)
    assert schema["$id"] == schema_id
    validator = Draft202012Validator(schema)

    valid = {
        "schema": "bms.molbio.restriction-digest-save-request.v1",
        "source": {
            "kind": "molecular_revision",
            "sequence_id": "sequence-1",
            "revision_id": "revision-7",
            "expected_content_sha256": "a" * 64,
        },
        "catalog": {
            "catalog_id": "biopython-rebase-404-bms-v1",
            "expected_catalog_sha256": "b" * 64,
        },
        "enzyme_ids": ["EcoRI"],
        "simulation_sha256": "d" * 64,
        "idempotency_key": "digest-sequence-1-r7",
        "persistence_mode": "operation_only",
    }
    accepted = [
        valid,
        {**valid, "fragment_name_prefix": None},
        {**valid, "persistence_mode": "operation_and_fragments", "fragment_name_prefix": "EcoRI fragment"},
        {**valid, "source": {**valid["source"], "topology": "linear"}},
        {**valid, "source": {**valid["source"], "topology": "circular"}},
        {**valid, "source": {**valid["source"], "topology": None}},
        {**valid, "enzyme_ids": [f"enzyme-{index}" for index in range(64)]},
    ]

    legacy_policy = {
        "algorithm_id": "bms-restriction-digest", "algorithm_version": "1", "topology": "linear",
    }
    rejected: dict[str, dict[str, object]] = {
        "missing_discriminator": {key: value for key, value in valid.items() if key != "schema"},
        "wrong_discriminator": {**valid, "schema": "bms.molbio.restriction-digest-simulation-request.v1"},
        "missing_source_kind": {**valid, "source": {key: value for key, value in valid["source"].items() if key != "kind"}},
        "inline_source": {**valid, "source": {"kind": "inline_dna", "name": "x", "dna": "ACGT", "topology": "linear"}},
        "source_content_alias": {**valid, "source": {"kind": "molecular_revision", "sequence_id": "sequence-1", "revision_id": "revision-7", "content_sha256": "a" * 64}},
        "catalog_content_alias": {**valid, "catalog": {"catalog_id": "biopython-rebase-404-bms-v1", "content_sha256": "b" * 64}},
        "missing_catalog_digest": {**valid, "catalog": {"catalog_id": "biopython-rebase-404-bms-v1"}},
        "simulation_policy": {**valid, "simulation_policy": legacy_policy},
        "result_binding": {**valid, "result_binding": {"request_sha256": "c" * 64, "simulation_sha256": "d" * 64}},
        "too_many_enzymes": {**valid, "enzyme_ids": [f"enzyme-{index}" for index in range(65)]},
        "duplicate_enzyme": {**valid, "enzyme_ids": ["EcoRI", "EcoRI"]},
        "unknown_root": {**valid, "site": "GAATTC"},
        "unknown_source": {**valid, "source": {**valid["source"], "content_length": 10}},
        "unknown_catalog": {**valid, "catalog": {**valid["catalog"], "catalog_version": "1"}},
        "bad_topology": {**valid, "source": {**valid["source"], "topology": "plasmid"}},
        "bad_simulation_sha": {**valid, "simulation_sha256": "D" * 64},
        "blank_idempotency": {**valid, "idempotency_key": "   "},
        "long_idempotency": {**valid, "idempotency_key": "x" * 256},
        "bad_persistence_mode": {**valid, "persistence_mode": "fragments_only"},
        "blank_fragment_name": {**valid, "fragment_name_prefix": "   "},
    }

    for label, payload in [(f"accepted-{index}", item) for index, item in enumerate(accepted)]:
        DigestSaveRequest.model_validate(payload)
        assert not list(validator.iter_errors(payload)), label
    for label, payload in rejected.items():
        with pytest.raises(ValidationError):
            DigestSaveRequest.model_validate(payload)
        assert list(validator.iter_errors(payload)), label

    assert capability["accepted_source_roles"] == ["molecular_revision"]
    expected_keys = {
        "schema", "source", "catalog", "enzyme_ids", "simulation_sha256",
        "idempotency_key", "persistence_mode", "fragment_name_prefix",
    }
    assert set(capability["observed_parameter_keys"]) == expected_keys
    assert set(capability["classified_parameter_keys"]) == expected_keys
    assert capability["native_mapping"]["native_request_compatibility"] == "exact"
    assert capability["materializer_owner"] is None
    assert schema["x-bms-authority-state"] == "active_exact_public_save_request"


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
    assert capability["exposure_state"] == "internal"
    assert capability["submission_owner"] == "routers.molbio_restriction.save_restriction_digest"
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



# Phase 1 runtime-loader contract. These tests intentionally import the missing
# production module only after the Phase 0 source-contract tests above.
def _phase1_authority(tmp_path: Path):
    from services.restriction_catalog import CatalogAuthority

    catalog = tmp_path / "catalog.json"
    manifest = tmp_path / "manifest.json"
    schema = tmp_path / "schema.json"
    catalog.write_bytes(CATALOG.read_bytes())
    manifest.write_bytes(MANIFEST.read_bytes())
    schema.write_bytes(CATALOG_SCHEMA.read_bytes())
    return CatalogAuthority(catalog, manifest, schema), catalog, manifest, schema


def _rewrite_bound_assets(catalog_path: Path, manifest_path: Path, mutate) -> None:
    catalog = json.loads(catalog_path.read_bytes())
    manifest = json.loads(manifest_path.read_bytes())
    mutate(catalog, manifest)
    for row in catalog.get("records", []):
        row["record_sha256"] = _canonical_digest(row, "record_sha256")
    catalog["content_sha256"] = _canonical_digest(catalog, "content_sha256")
    manifest["catalog_id"] = catalog.get("catalog_id")
    manifest["catalog_content_sha256"] = catalog["content_sha256"]
    manifest["counts"] = catalog.get("counts")
    manifest["records"] = [
        {"enzyme_id": row["enzyme_id"], "record_sha256": row["record_sha256"]}
        for row in catalog.get("records", [])
    ]
    manifest["content_sha256"] = _canonical_digest(manifest, "content_sha256")
    catalog_path.write_bytes(rfc8785.dumps(catalog))
    manifest_path.write_bytes(rfc8785.dumps(manifest))


def test_phase1_loader_rejects_self_consistent_scientific_release_replacement(
    tmp_path: Path,
) -> None:
    from services.restriction_catalog import CatalogUnavailable

    authority, catalog_path, manifest_path, _schema = _phase1_authority(tmp_path)

    def add_valid_alias(catalog, _manifest) -> None:
        record = next(row for row in catalog["records"] if row["canonical_name"] == "EcoRI")
        record["aliases"] = ["EcoRI-approved-replacement"]

    _rewrite_bound_assets(catalog_path, manifest_path, add_valid_alias)

    with pytest.raises(CatalogUnavailable) as error:
        authority.require()
    assert str(tmp_path) not in str(error.value)


def test_phase1_loader_rejects_valid_but_altered_schema_release(tmp_path: Path) -> None:
    from services.restriction_catalog import CatalogUnavailable

    authority, _catalog, _manifest, schema_path = _phase1_authority(tmp_path)
    schema = json.loads(schema_path.read_bytes())
    schema["title"] = "Valid hostile replacement schema"
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_load(CATALOG))
    schema_path.write_bytes(rfc8785.dumps(schema))

    with pytest.raises(CatalogUnavailable) as error:
        authority.require()
    assert str(tmp_path) not in str(error.value)


def test_phase1_loader_rejects_fully_self_consistent_manifest_replacement(
    tmp_path: Path,
) -> None:
    from services.restriction_catalog import CatalogUnavailable

    authority, catalog_path, manifest_path, _schema = _phase1_authority(tmp_path)

    def replace_valid_record_field(catalog, _manifest) -> None:
        record = next(row for row in catalog["records"] if row["canonical_name"] == "BamHI")
        record["aliases"] = ["BamHI-approved-replacement"]

    _rewrite_bound_assets(catalog_path, manifest_path, replace_valid_record_field)
    replacement_manifest = json.loads(manifest_path.read_bytes())
    assert replacement_manifest["content_sha256"] == _canonical_digest(
        replacement_manifest, "content_sha256"
    )

    with pytest.raises(CatalogUnavailable) as error:
        authority.require()
    assert str(tmp_path) not in str(error.value)


def test_phase1_loader_publishes_frozen_typed_projection_and_indexes(tmp_path: Path) -> None:
    from pydantic import ValidationError

    authority, *_ = _phase1_authority(tmp_path)
    view = authority.require()
    assert view.catalog_id == "biopython-rebase-404-bms-v1"
    assert view.content_sha256 == "e9a1e9ec8e5b1845f82fd613f7343722756c0ef8c5f487c704a151646317d73f"
    assert len(view.records) == 1092
    assert view.ordered_records == tuple(
        sorted(view.records, key=lambda row: (row.canonical_name.casefold(), row.enzyme_id.casefold()))
    )
    assert view.by_id["EcoRI"].canonical_name == "EcoRI"
    assert view.by_name_casefold["ecori"].enzyme_id == "EcoRI"
    assert "EcoRI" in {row.enzyme_id for row in view.by_motif["GAATTC"]}
    assert "EcoRI" in {row.enzyme_id for row in view.by_supplier_code["N"]}
    assert "EcoRI" in {row.enzyme_id for row in view.by_overhang_kind["five_prime"]}
    assert "EcoRI" in {row.enzyme_id for row in view.by_palindromic[True]}
    with pytest.raises((ValidationError, TypeError)):
        view.records[0].canonical_name = "changed"  # type: ignore[misc]


def test_phase1_loader_is_atomic_and_loads_once(tmp_path: Path) -> None:
    authority, catalog, *_ = _phase1_authority(tmp_path)
    first = authority.require()
    catalog.write_text("partial", encoding="utf-8")
    second = authority.require()
    assert second is first
    assert len(second.records) == 1092


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "malformed",
        "malformed_schema",
        "schema",
        "oversized",
        "symlink",
        "special",
        "digest",
        "count",
        "duplicate_id",
        "duplicate_name",
        "broken_relation",
        "wrong_source",
        "partial",
        "noncanonical_raw",
        "manifest_binding",
    ],
)
def test_phase1_loader_fails_closed_for_hostile_assets(tmp_path: Path, case: str) -> None:
    from services.restriction_catalog import CatalogUnavailable

    authority, catalog, manifest, _schema = _phase1_authority(tmp_path)
    if case == "missing":
        catalog.unlink()
    elif case in {"malformed", "partial"}:
        catalog.write_bytes(b'{"records":[' if case == "partial" else b"not-json")
    elif case == "malformed_schema":
        _schema.write_bytes(b"not-json")
    elif case == "schema":
        def mutate_schema(document, _manifest):
            document["unexpected"] = True
        _rewrite_bound_assets(catalog, manifest, mutate_schema)
    elif case == "oversized":
        catalog.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    elif case == "symlink":
        target = tmp_path / "target.json"
        target.write_bytes(catalog.read_bytes())
        catalog.unlink()
        catalog.symlink_to(target)
    elif case == "special":
        catalog.unlink()
        os.mkfifo(catalog)
    elif case == "digest":
        document = json.loads(catalog.read_bytes())
        document["content_sha256"] = "0" * 64
        catalog.write_bytes(rfc8785.dumps(document))
    elif case == "count":
        _rewrite_bound_assets(catalog, manifest, lambda document, _manifest: document["counts"].update(total_discoverable=1))
    elif case == "duplicate_id":
        _rewrite_bound_assets(catalog, manifest, lambda document, _manifest: document["records"][1].update(enzyme_id=document["records"][0]["enzyme_id"]))
    elif case == "duplicate_name":
        _rewrite_bound_assets(catalog, manifest, lambda document, _manifest: document["records"][1].update(canonical_name=document["records"][0]["canonical_name"]))
    elif case == "broken_relation":
        def mutate_relation(document, _manifest):
            document["records"][0]["relationships"]["equischizomer_ids"] = ["missing-enzyme"]
        _rewrite_bound_assets(catalog, manifest, mutate_relation)
    elif case == "wrong_source":
        def mutate_source(document, _manifest):
            document["source"]["package_version"] = "9.99"
            for row in document["records"]:
                if row["source"]["kind"] == "biopython_restriction_dictionary":
                    row["source"]["package_version"] = "9.99"
        _rewrite_bound_assets(catalog, manifest, mutate_source)
    elif case == "noncanonical_raw":
        catalog.write_text(json.dumps(json.loads(catalog.read_bytes()), indent=2), encoding="utf-8")
    elif case == "manifest_binding":
        document = json.loads(manifest.read_bytes())
        document["catalog_content_sha256"] = "0" * 64
        document["content_sha256"] = _canonical_digest(document, "content_sha256")
        manifest.write_bytes(rfc8785.dumps(document))

    state = authority.state()
    assert state.ready is False
    assert state.status == "catalog_unavailable"
    assert state.metadata is None
    with pytest.raises(CatalogUnavailable) as error:
        authority.require()
    assert str(tmp_path) not in str(error.value)
    assert case not in str(error.value)


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("missing source module", lambda manifest: manifest["source"].pop("module")),
        ("altered generator", lambda manifest: manifest.update(generator_version="hostile-generator")),
        ("altered canonicalization", lambda manifest: manifest.update(canonicalization="hostile-canonicalization")),
        ("wrong schema version type", lambda manifest: manifest.update(schema_version="1")),
        ("altered notice identity", lambda manifest: manifest["notices"].__setitem__(0, "hostile notice")),
        ("unknown field", lambda manifest: manifest.update(attacker_controlled=True)),
        ("missing supplement policy", lambda manifest: manifest["source"].pop("supplement_policy")),
        (
            "missing supplement count",
            lambda manifest: manifest["counts"].pop("curated_nickase_supplement_records"),
        ),
    ],
)
def test_phase1_loader_rejects_self_consistent_hostile_manifest_authority(
    tmp_path: Path, label: str, mutate
) -> None:
    from services.restriction_catalog import CatalogUnavailable

    authority, _catalog, manifest_path, _schema = _phase1_authority(tmp_path)
    manifest = json.loads(manifest_path.read_bytes())
    mutate(manifest)
    manifest["content_sha256"] = _canonical_digest(manifest, "content_sha256")
    manifest_path.write_bytes(rfc8785.dumps(manifest))

    with pytest.raises(CatalogUnavailable) as error:
        authority.require()
    assert str(tmp_path) not in str(error.value)
    assert label not in str(error.value)


def test_phase1_loader_rejects_missing_supplement_record_receipt_with_recomputed_digest(
    tmp_path: Path,
) -> None:
    from services.restriction_catalog import CatalogUnavailable

    authority, catalog_path, manifest_path, _schema = _phase1_authority(tmp_path)
    catalog = json.loads(catalog_path.read_bytes())
    nickase_ids = {
        row["enzyme_id"]
        for row in catalog["records"]
        if row["source"]["kind"] == "bms_curated_rebase_nickase"
    }
    manifest = json.loads(manifest_path.read_bytes())
    manifest["records"] = [
        receipt for receipt in manifest["records"] if receipt["enzyme_id"] not in nickase_ids
    ]
    manifest["content_sha256"] = _canonical_digest(manifest, "content_sha256")
    manifest_path.write_bytes(rfc8785.dumps(manifest))

    with pytest.raises(CatalogUnavailable):
        authority.require()


def _nested_phase1_authority(tmp_path: Path):
    from services.restriction_catalog import CatalogAuthority

    trusted = tmp_path / "trusted"
    paths = {
        "catalog": trusted / "catalog-assets" / "catalog.json",
        "manifest": trusted / "manifest-assets" / "manifest.json",
        "schema": trusted / "schema-assets" / "schema.json",
    }
    sources = {"catalog": CATALOG, "manifest": MANIFEST, "schema": CATALOG_SCHEMA}
    for name, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(sources[name].read_bytes())
    authority = CatalogAuthority(
        paths["catalog"], paths["manifest"], paths["schema"], trusted_root=trusted
    )
    return authority, trusted, paths, sources


@pytest.mark.parametrize("asset", ["catalog", "manifest", "schema"])
def test_phase1_loader_rejects_symlinked_parent_component(tmp_path: Path, asset: str) -> None:
    from services.restriction_catalog import CatalogUnavailable

    authority, _trusted, paths, sources = _nested_phase1_authority(tmp_path)
    asset_path = paths[asset]
    outside = tmp_path / f"outside-{asset}"
    outside.mkdir()
    (outside / asset_path.name).write_bytes(sources[asset].read_bytes())
    asset_path.unlink()
    asset_path.parent.rmdir()
    asset_path.parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(CatalogUnavailable) as error:
        authority.require()
    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize("asset", ["catalog", "manifest", "schema"])
@pytest.mark.parametrize("hostile_kind", ["symlink", "special"])
def test_phase1_loader_rejects_hostile_final_asset_components(
    tmp_path: Path, asset: str, hostile_kind: str
) -> None:
    from services.restriction_catalog import CatalogUnavailable

    authority, _trusted, paths, sources = _nested_phase1_authority(tmp_path)
    asset_path = paths[asset]
    asset_path.unlink()
    if hostile_kind == "symlink":
        target = tmp_path / f"outside-{asset}.json"
        target.write_bytes(sources[asset].read_bytes())
        asset_path.symlink_to(target)
    else:
        os.mkfifo(asset_path)

    with pytest.raises(CatalogUnavailable) as error:
        authority.require()
    assert str(tmp_path) not in str(error.value)


def test_phase1_loader_rejects_absolute_and_parent_path_escapes(tmp_path: Path) -> None:
    from services.restriction_catalog import CatalogAuthority, CatalogUnavailable

    _authority, trusted, paths, _sources = _nested_phase1_authority(tmp_path)
    outside_catalog = tmp_path / "escaped-catalog.json"
    outside_catalog.write_bytes(CATALOG.read_bytes())
    escaped = trusted / ".." / outside_catalog.name
    for candidate in (outside_catalog, escaped):
        authority = CatalogAuthority(
            candidate,
            paths["manifest"],
            paths["schema"],
            trusted_root=trusted,
        )
        with pytest.raises(CatalogUnavailable):
            authority.require()


def test_component_walker_rejects_absolute_empty_dot_and_parent_components(tmp_path: Path) -> None:
    from services.restriction_catalog import CatalogUnavailable, _bounded_regular_file

    root_descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for hostile in ("", ".", "..", "/absolute", "../escape", "a//b", "a/./b", "a/../b"):
            with pytest.raises(CatalogUnavailable):
                _bounded_regular_file(root_descriptor, hostile, 1024)
    finally:
        os.close(root_descriptor)


def test_phase1_loader_unavailable_state_is_sticky_and_never_partially_recovers(tmp_path: Path) -> None:
    from services.restriction_catalog import CatalogUnavailable

    authority, catalog, *_ = _phase1_authority(tmp_path)
    valid = catalog.read_bytes()
    catalog.write_bytes(b"partial")
    with pytest.raises(CatalogUnavailable):
        authority.require()
    catalog.write_bytes(valid)
    with pytest.raises(CatalogUnavailable):
        authority.require()


def test_phase2_catalog_readiness_exposes_receipt_age_bounds_and_analysis_capability(tmp_path: Path) -> None:
    authority, *_ = _phase1_authority(tmp_path)
    readiness = authority.readiness()
    assert readiness["required"] is True
    assert readiness["ready"] is True
    assert readiness["status"] == "ready"
    assert readiness["catalog_id"] == "biopython-rebase-404-bms-v1"
    assert readiness["catalog_sha256"] == "e9a1e9ec8e5b1845f82fd613f7343722756c0ef8c5f487c704a151646317d73f"
    assert readiness["counts"]["total"] == 1092
    assert readiness["bounds"]["maximum_limit"] == 250
    assert readiness["source_year"] == 2024
    assert readiness["analysis_enabled"] is True
    assert readiness["digest_enabled"] is False
    receipt = readiness["resource_policy"]
    assert isinstance(receipt, Mapping)
    assert readiness["resource_policy_sha256"] == RESOURCE_POLICY_SHA256
    assert readiness["resource_policy_sha256"] == hashlib.sha256(
        rfc8785.dumps(dict(receipt))
    ).hexdigest()


def test_phase2_benchmark_distinguishes_historical_run_policy_from_current_policy() -> None:
    benchmark = PHASE2_BENCHMARK.read_text(encoding="utf-8")
    historical = "94d0ab410dec1f2510e3b13f0434cc1561ec133f8c042e4e8c76ec32ba647e64"
    assert f"historical benchmark-run resource-policy SHA-256 `{historical}`" in benchmark
    assert "current resource policy version `1.1.0`" in benchmark
    assert f"SHA-256 `{RESOURCE_POLICY_SHA256}`" in benchmark
    assert "`canonical-json-entry-and-complete-cache-graph` version `2.0.0`" in benchmark
