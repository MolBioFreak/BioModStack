from __future__ import annotations

import hashlib
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_ROOT = REPO_ROOT / "platform/api/config/ont_signal_workbench"
SQUIGULATOR_COMMIT = "c5f0c619a28b9532388877096acb7568c34b9c4b"
SQUIGULATOR_RELEASE_SHA256 = (
    "f8b428655d586427c6e0c939d4a0383fa8569523234e3c21951edcd23372a66a"
)
SQUIGUALISER_COMMIT = "5a2404f1f43bc3227a85475c59b2b77970078b2e"


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _canonical_sha256(value: dict[str, object], field: str = "content_sha256") -> str:
    preimage = dict(value)
    preimage.pop(field, None)
    return hashlib.sha256(
        json.dumps(preimage, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def test_parameter_contract_classifies_every_pinned_upstream_option() -> None:
    contract = _load(CONFIG_ROOT / "squigulator_ideal_comparison_schema_v1.json")

    assert contract["schema"] == "bms.ont-squigulator-ideal-comparison.v1"
    assert contract["upstream"] == {
        "name": "Squigulator",
        "version": "0.5.0",
        "commit": SQUIGULATOR_COMMIT,
        "release_source_asset": "squigulator-v0.5.0-release.tar.gz",
        "release_source_asset_sha256": SQUIGULATOR_RELEASE_SHA256,
    }
    options = contract["upstream_options"]
    assert isinstance(options, list)
    assert {item["option"] for item in options} == {
        "--verbose", "--help", "--version", "--output", "--ideal",
        "--full-contigs", "--nreads", "--fasta", "--rlen", "--seed",
        "--ideal-time", "--ideal-amp", "--dwell-mean", "--profile",
        "--kmer-model", "--prefix", "--dwell-std", "--threads",
        "--batchsize", "--paf", "--amp-noise", "--paf-ref", "--sam",
        "--coverage", "--digitisation", "--sample-rate", "--range",
        "--offset-mean", "--offset-std", "--bps", "--median-before-mean",
        "--median-before-std", "--trans-count", "--trans-trunc", "--cdna",
        "--ont-friendly", "--meth-freq", "--meth-model", "--meth-all-ctx",
    }
    assert all(
        set(item) >= {"option", "authority", "supported", "reason", "digest_participation"}
        for item in options
    )
    assert {item["authority"] for item in options} <= {
        "operator_owned", "profile_fixed", "workflow_fixed", "runtime_owned", "unsupported"
    }
    assert all(item["reason"] for item in options if not item["supported"])

    parameters = contract["operator_parameters"]
    assert set(parameters) == {
        "profile_id", "seed", "scale", "point_size", "fixed_width", "base_width",
        "base_limit", "signal_sample_limit", "show_samples", "show_base_colours",
        "remove_signal_outliers",
    }
    assert parameters["seed"]["minimum"] == 1
    assert parameters["seed"]["maximum"] == 2_147_483_647
    assert parameters["seed"]["default"] == 1
    assert parameters["base_limit"]["maximum"] == 1000
    assert contract["additionalProperties"] is False


def test_eight_profile_constants_match_v050_executable_source() -> None:
    contract = _load(CONFIG_ROOT / "squigulator_ideal_comparison_schema_v1.json")
    profiles = contract["profiles"]
    assert set(profiles) == {
        "dna-r9-min", "dna-r9-prom", "rna-r9-min", "rna-r9-prom",
        "dna-r10-min", "dna-r10-prom", "rna004-min", "rna004-prom",
    }
    assert profiles["dna-r10-min"] | {
        "sample_rate": 5000,
        "translocation_speed": 400,
        "dwell_mean": 13.0,
        "dwell_standard_deviation": 4.0,
    } == profiles["dna-r10-min"]
    assert profiles["dna-r10-prom"] | {
        "sample_rate": 5000,
        "translocation_speed": 400,
        "dwell_mean": 13.0,
        "dwell_standard_deviation": 4.0,
    } == profiles["dna-r10-prom"]
    for profile_id in ("dna-r10-min", "dna-r10-prom", "rna004-min", "rna004-prom"):
        assert profiles[profile_id]["compatibility_floor"] == "approximate_profile"
        assert "crude" in profiles[profile_id]["model_quality_warning"].lower()


def test_producer_and_comparison_renderer_are_distinct_network_denied_pins() -> None:
    producer = _load(CONFIG_ROOT / "squigulator_runtime_policy_v1.json")
    renderer = _load(CONFIG_ROOT / "comparison_render_runtime_policy_v1.json")

    assert producer["schema"] == "bms.ont-squigulator-runtime-policy.v1"
    assert producer["upstream"]["commit"] == SQUIGULATOR_COMMIT
    assert producer["source_asset"]["sha256"] == SQUIGULATOR_RELEASE_SHA256
    assert producer["network"] == "none"
    assert renderer["schema"] == "bms.ont-squigualiser-comparison-runtime-policy.v1"
    assert renderer["upstream"]["commit"] == SQUIGUALISER_COMMIT
    assert renderer["network"] == "none"
    assert producer["runtime_id"] != renderer["runtime_id"]
    assert producer["oci_digest"] != renderer["oci_digest"]


def test_runtime_build_sources_pin_named_release_and_separate_wrappers() -> None:
    producer = (REPO_ROOT / "docker/ont-squigulator.Dockerfile").read_text(encoding="utf-8")
    renderer = (REPO_ROOT / "docker/ont-squigualiser-comparison.Dockerfile").read_text(encoding="utf-8")
    producer_wrapper = REPO_ROOT / "scripts/ont_squigulator_runtime.py"
    renderer_wrapper = REPO_ROOT / "scripts/ont_signal_comparison_runtime.py"

    assert "squigulator-v0.5.0-release.tar.gz" in producer
    assert "debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171" in producer
    assert SQUIGULATOR_RELEASE_SHA256 in producer
    assert SQUIGULATOR_COMMIT in producer
    assert "COPY scripts/ont_squigulator_runtime.py" in producer
    assert SQUIGUALISER_COMMIT in renderer
    assert "COPY scripts/ont_signal_comparison_runtime.py" in renderer
    assert "ont_signal_runtime.py" not in renderer
    assert producer_wrapper.is_file()
    assert renderer_wrapper.is_file()
    assert "--full-contigs" in producer_wrapper.read_text(encoding="utf-8")
    assert "--shared_x" in renderer_wrapper.read_text(encoding="utf-8")


def test_development_api_unit_receives_both_comparison_runtime_identities() -> None:
    services = (REPO_ROOT / "biomodstack_services.py").read_text(encoding="utf-8")
    assert "BMS_ONT_SQUIGULATOR_IMAGE" in services
    assert "BMS_ONT_SQUIGULATOR_IMAGE_DIGEST" in services
    assert "BMS_ONT_SQUIGUALISER_COMPARISON_IMAGE" in services
    assert "BMS_ONT_SQUIGUALISER_COMPARISON_IMAGE_DIGEST" in services


def test_worker_claims_comparison_and_preserves_two_stage_runtime_order() -> None:
    worker = (REPO_ROOT / "platform/api/services/ont_signal_worker.py").read_text(encoding="utf-8")
    assert "OntSignalComparisonJob" in worker
    assert "_process_comparison" in worker
    assert "squigulator_producer" in worker
    assert "squigualiser_comparison_renderer" in worker
    assert worker.index("squigulator_producer") < worker.index("squigualiser_comparison_renderer")
    assert '"--pids-limit", "64"' in worker
    assert '"--memory", "1g"' in worker
    assert '"--pids-limit", "128"' in worker
    assert '"--memory", "4g"' in worker


def test_canonical_ont_docs_preserve_acquired_authority_and_do_not_overclaim_live_squigulator() -> None:
    docs = (REPO_ROOT / "docs/Lab_Automation_MolBio_and_Sequencing.md").read_text(encoding="utf-8")
    assert "workflows/ngs/ont_methylation_analysis.nf" in docs
    assert "workflows/nanopore_methylation.nf" not in docs
    assert "Read and Signal Workbench" in docs
    assert "acquired signal" in docs
    assert "Squigualiser" in docs
    assert "live acceptance" in docs


def test_runtime_source_denominator_v2_covers_comparison_surface_and_preserves_v1() -> None:
    import hashlib
    import rfc8785

    v1_path = REPO_ROOT / "schemas/ngs_molbio_runtime/runtime-source-denominator-v1.json"
    v2_path = REPO_ROOT / "schemas/ngs_molbio_runtime/runtime-source-denominator-v2.json"
    v1 = _load(v1_path)
    v2 = _load(v2_path)
    assert v1["schema"] == "bms.ngs-molbio.runtime-source-denominator.v1"
    required = {
        "docker/ont-squigulator.Dockerfile",
        "docker/ont-squigualiser-comparison.Dockerfile",
        "platform/api/migrations/add_ont_signal_comparisons.py",
        "platform/api/migrations/ont_signal_comparison_schema_contract.py",
        "platform/api/config/ont_signal_workbench/squigulator_ideal_comparison_schema_v1.json",
        "platform/frontend/src/components/ngs/OntSignalIdealComparison.tsx",
        "platform/frontend/tests/vitest/ontSignalIdealComparison.test.tsx",
        "docs/Lab_Automation_MolBio_and_Sequencing.md",
        "schemas/ngs_molbio_runtime/runtime-source-denominator-v2.json",
    }
    assert required <= set(v2["paths"])
    unsigned = {key: value for key, value in v2.items() if key != "content_sha256"}
    assert v2["content_sha256"] == hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest()


def test_capability_inventory_v2_adds_squigulator_without_relabeling_squigualiser() -> None:
    inventory = _load(REPO_ROOT / "platform/api/config/ngs_molbio/capability_inventory_v2.json")
    schema = _load(REPO_ROOT / "schemas/ngs_molbio/capability-inventory-v2.schema.json")

    assert inventory["schema"] == "bms.ngs-molbio.capability-inventory.v2"
    assert inventory["schema_version"] == 2
    assert len(inventory["capabilities"]) == 22
    assert inventory["content_sha256"] == _canonical_sha256(inventory)
    assert schema["properties"]["capabilities"]["minItems"] == 22
    assert schema["properties"]["capabilities"]["maxItems"] == 22
    by_id = {item["capability_id"]: item for item in inventory["capabilities"]}
    squigulator = by_id["ngs.ont.squigulator_ideal_comparison"]
    assert squigulator["parameter_schema_id"] == "bms.ont-squigulator-ideal-comparison.v1"
    assert squigulator["canonical_source_destination"] == "/ngs"
    assert squigulator["viewer_destination"].startswith("/ngs?")
    assert "ngs_reference" in squigulator["accepted_source_roles"]
    assert "ngs_instrument_signal" in squigulator["accepted_source_roles"]
    assert all("squigulator" not in item["capability_id"] for item in inventory["capabilities"] if item["capability_id"] == "ngs.ont.squigualiser")
    for capability_id in ("ngs.ont.basecall_dna", "ngs.ont.basecall_rna"):
        row = by_id[capability_id]
        assert "emit_moves" in row["classified_parameter_keys"]
        assert "emit_moves" not in row["unclassified_parameter_keys"]
