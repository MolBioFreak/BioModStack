from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from services import ngs_molbio_capabilities, ngs_molbio_runtime_status


ROOT = Path(__file__).resolve().parents[3]
BUILDER_PATH = ROOT / "scripts/build_ngs_molbio_runtime_implementation_record.py"
N0_REPORT = ROOT / "docs/reports/ngs-molbio-phase-n0-verification-v1.json"
DENOMINATOR_V2 = ROOT / "schemas/ngs_molbio_runtime/runtime-source-denominator-v2.json"
DENOMINATOR_V2_RELATIVE = "schemas/ngs_molbio_runtime/runtime-source-denominator-v2.json"


def _load_builder():
    specification = importlib.util.spec_from_file_location(
        "ngs_molbio_runtime_record_builder_test", BUILDER_PATH
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_live_capability_authority_uses_v2_squigulator_inventory() -> None:
    inventory = ngs_molbio_capabilities.capability_inventory()

    assert inventory["schema"] == "bms.ngs-molbio.capability-inventory.v2"
    assert len(inventory["capabilities"]) == 22
    assert ngs_molbio_capabilities.capability_record(
        "ngs.ont.squigulator_ideal_comparison"
    )["capability_id"] == "ngs.ont.squigulator_ideal_comparison"


def test_builder_defaults_to_active_v2_runtime_authority() -> None:
    builder = _load_builder()

    assert builder.OUTPUT.name == "runtime_implementation_v2.json"
    assert builder.DENOMINATOR == DENOMINATOR_V2
    assert builder.DENOMINATOR_RELATIVE == DENOMINATOR_V2_RELATIVE
    assert builder.DENOMINATOR_SCHEMA == "bms.ngs-molbio.runtime-source-denominator.v2"


def test_runtime_denominator_covers_local_resource_and_path_authority() -> None:
    builder = _load_builder()
    denominator = builder._load_source_denominator()
    assert {
        "biomodstack_local_resources.py",
        "biomodstack_runtime_profile.py",
        "platform/api/telemetry_store.py",
    }.issubset(set(denominator["paths"]))


def test_runtime_denominator_covers_complete_restriction_authority() -> None:
    denominator = json.loads(DENOMINATOR_V2.read_text(encoding="utf-8"))
    required = {
        "platform/api/config/molbio/restriction/restriction_enzyme_catalog_change_report_v1.json",
        "platform/api/config/molbio/restriction/restriction_enzyme_catalog_manifest_v1.json",
        "platform/api/config/molbio/restriction/restriction_enzyme_catalog_v1.json",
        "platform/api/config/molbio/restriction/restriction_enzyme_products_v1.json",
        "platform/api/molbio_migrations.py",
        "platform/api/readiness.py",
        "platform/api/restriction_catalog_integrity.py",
        "platform/api/routers/molbio_restriction.py",
        "platform/api/services/restriction_analysis.py",
        "platform/api/services/restriction_catalog.py",
        "platform/api/services/restriction_digest.py",
        "platform/api/services/restriction_digest_save_receipt.py",
        "platform/api/services/restriction_products.py",
        "platform/api/services/sqlite_schema_attestation.py",
        "platform/api/services/assembly/golden_gate.py",
        "platform/api/services/assembly/common.py",
        "platform/api/services/assembly/ligation.py",
        "platform/api/services/assembly/types.py",
        "platform/api/services/nucleotide_validation.py",
        "platform/api/services/molbio_persistence.py",
        "platform/api/services/molbio_ops.py",
        "platform/frontend/src/components/MolBioToolkit/GCContentTrack.tsx",
        "platform/frontend/src/components/MolBioToolkit/SequenceViewer.tsx",
        "platform/frontend/src/components/MolBioToolkit/panels/DigestPanel.tsx",
        "platform/frontend/src/components/MolBioToolkit/utils/projectConstructShelf.ts",
        "platform/frontend/src/components/MolBioToolkit/panels/AssemblyPanel.tsx",
        "platform/frontend/src/components/MolBioToolkit/hooks/useSequenceOperations.ts",
        "platform/frontend/src/components/MolBioToolkit/types.ts",
        "platform/frontend/src/lib/restrictionAnalysis.ts",
        "platform/frontend/src/lib/goldenGateAuthority.ts",
        "packages/sequence-utils/src/index.js",
        "schemas/molbio/restriction_enzyme_catalog_v1.schema.json",
        "schemas/molbio/restriction_enzyme_products_v1.schema.json",
        "schemas/ngs_molbio/molbio-restriction_digest-v2.schema.json",
        "schemas/ngs_molbio/molbio-assembly-golden_gate-v1.schema.json",
        "scripts/build_restriction_enzyme_catalog.py",
    }
    assert required <= set(denominator["paths"])
    assert {'scripts/lib/shared_runtime_images.py', 'scripts/publish_runtime_images.py'} <= set(denominator['paths'])
    assert {
        'scripts/lib/runtime_image_lifecycle.py', 'scripts/retire_runtime_images.py',
        'modules/ngs/clone_validation.nf', 'modules/ngs/construct_verify.nf',
        'platform/api/services/remote_execution/bundle.py',
        'platform/api/services/remote_execution/cache.py',
        'platform/api/services/remote_execution/managed_inventory.py',
        'platform/api/tools/bms_artifact_cache.py',
        'platform/api/tools/bms_managed_runtime.py',
    } <= set(denominator['paths'])
    assert {
        'platform/frontend/src/components/MolBioToolkit/panels/sequenceSearch.ts',
        'platform/frontend/src/components/MolBioToolkit/panels/sequenceSearch.worker.ts',
        'platform/frontend/src/components/MolBioToolkit/panels/startSequenceSearch.ts',
        'platform/frontend/src/components/MolBioToolkit/panels/useInputOwnership.ts',
    } <= set(denominator['paths'])
    assert all((ROOT / path).is_file() for path in denominator['paths'])
    assert not {
        'platform/frontend/src/components/molbio-ngs/DomainWorkflowOperator.tsx',
        'platform/frontend/src/components/molbio-ngs/ExperimentReferenceLibrary.tsx',
    }.intersection(denominator['paths'])


def test_checked_in_active_runtime_record_is_accepted() -> None:
    accepted = ngs_molbio_runtime_status.runtime_implementation_record(fresh=True)
    assert accepted["implementation_state"] == "implemented_unverified"
    assert accepted["release_acceptance_state"] == "open"
    assert accepted["capability_exposure_state"] == "fail_closed"


def test_builder_uses_current_n0_authority_and_status_accepts_generated_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    builder = _load_builder()
    output = tmp_path / "runtime_implementation_v2.json"
    commit_object = tmp_path / "successor.commit"
    commit_object.write_bytes(b"test bypassed by exact verifier seam")
    monkeypatch.setattr(builder, "OUTPUT", output)
    monkeypatch.setattr(
        builder,
        "_verify_successor_authority",
        lambda _commit, _tree, _object: None,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(BUILDER_PATH),
            "--successor-source-commit",
            "1" * 40,
            "--successor-source-tree",
            "2" * 40,
            "--successor-commit-object",
            str(commit_object),
        ],
    )

    assert builder.main() == 0

    generated = json.loads(output.read_text(encoding="utf-8"))
    n0_report = json.loads(N0_REPORT.read_text(encoding="utf-8"))
    assert generated["n0_receipt_content_sha256"] == n0_report["content_sha256"]
    assert generated["n0_package_fingerprint"] == n0_report[
        "payload_fingerprint_sha256"
    ]
    monkeypatch.setattr(ngs_molbio_runtime_status, "_RECORD", output)
    monkeypatch.setattr(ngs_molbio_runtime_status, "_DENOMINATOR", DENOMINATOR_V2)
    monkeypatch.setattr(
        ngs_molbio_runtime_status, "_DENOMINATOR_RELATIVE", DENOMINATOR_V2_RELATIVE
    )
    accepted = ngs_molbio_runtime_status.runtime_implementation_record(fresh=True)
    assert accepted == generated
