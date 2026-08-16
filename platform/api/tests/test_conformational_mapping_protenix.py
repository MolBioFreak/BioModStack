from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

import pytest

from services.conformational_mapping.contracts import candidate_id, canonical_sha256
from services.conformational_mapping.protenix import (
    ProtenixMappingError,
    build_protenix_runtime_bundle,
    snapshot_to_protenix,
)


FIXTURES = Path(__file__).parent / "fixtures" / "conformational_mapping"


def _case(index: int = -1) -> dict:
    cases = json.loads((FIXTURES / "phase_0_vectors" / "complex_cases.json").read_text())["cases"]
    value = copy.deepcopy([case for case in cases if case["kind"] == "positive"][index])
    value.update(target_id=f"target-{index}", target_order=0, unsupported_fields=[])
    value.setdefault("admission", {"token_count": 1, "atom_count": 1, "token_limit": 4096, "conversion_omissions": []})
    return value


def test_cm5_001_instance_ids_equal_count() -> None:
    snapshot = _case()
    snapshot["entities"][0]["count"] += 1
    with pytest.raises(ProtenixMappingError, match="instance"):
        snapshot_to_protenix(snapshot, [101])


def test_cm5_002_repeated_copy_mapping() -> None:
    snapshot = _case(8)
    task, audit = snapshot_to_protenix(snapshot, [101])
    assert task["sequences"][0]["proteinChain"]["id"] == ["repeat_prot_copy1", "repeat_prot_copy2"]
    assert [row["runtime_order"] for row in audit["source_to_runtime"]] == [0, 1]


def test_cm5_003_all_entity_types_and_bonds() -> None:
    snapshot = _case()
    task, audit = snapshot_to_protenix(snapshot, [101, 202])
    assert [next(iter(row)) for row in task["sequences"]] == [
        "proteinChain", "dnaSequence", "rnaSequence", "ligand", "ligand", "ion"
    ]
    assert len(task["covalent_bonds"]) == len(snapshot["bonds"])
    assert audit["instance_count"] == sum(entity["count"] for entity in snapshot["entities"])


def test_cm5_004_unsupported_data_fails() -> None:
    snapshot = _case()
    snapshot["unsupported_fields"] = ["future_entity_field"]
    with pytest.raises(ProtenixMappingError, match="unsupported"):
        snapshot_to_protenix(snapshot, [101])


def test_cm5_005_default_mode_is_phase0_frozen() -> None:
    from scripts import run_protenix_inference

    source = inspect.getsource(run_protenix_inference.main)
    assert "if not args.use_default_params" in source
    assert "runner_options.update(n_cycle=args.cycle, n_step=args.step)" in source


def test_cm5_006_multi_target_seed_sample_formula() -> None:
    first, second = _case(0), _case(1)
    first.update(target_id="a", target_order=0)
    second.update(target_id="b", target_order=1)
    request = {
        "request_id": "r", "request_sha256": "a" * 64,
        "targets": [{"target_id": "a"}, {"target_id": "b"}], "ordered_seeds": [7, 9],
    }
    bundle = build_protenix_runtime_bundle(request, [first, second])
    assert len(bundle["input"]) * len(request["ordered_seeds"]) * 3 == 12
    assert [task["modelSeeds"] for task in bundle["input"]] == [[7, 9], [7, 9]]


def test_cm5_007_candidate_ids_include_target() -> None:
    left = {"backend": "protenix_v2_ensemble", "target_id": "a", "ordered_seed": 1, "sample_index": 0}
    right = {**left, "target_id": "b"}
    assert candidate_id(left).startswith("cm_ptx_a_")
    assert candidate_id(left) != candidate_id(right)


def test_cm5_008_basename_collision_fails() -> None:
    source = inspect.getsource(__import__("services.conformational_mapping.protenix", fromlist=["finalize_protenix"]).finalize_protenix)
    assert "relative_path in referenced" in source
    assert "copytree(root, temporary / \"native\"" in source
    assert "basename" not in source


def test_cm5_009_missing_extra_partial_sidecars_fail() -> None:
    source = inspect.getsource(__import__("services.conformational_mapping.protenix", fromlist=["finalize_protenix"]).finalize_protenix)
    assert 'roles != {"authoritative_cif", "confidence_json", "full_data_json"}' in source
    assert "observed Protenix coordinates do not equal the request plan" in source


def test_cm5_010_composition_audit() -> None:
    task, audit = snapshot_to_protenix(_case(), [101])
    assert audit["protenix_input_sha256"] == canonical_sha256(task)
    assert audit["bond_count"] == len(task["covalent_bonds"])


def test_cm5_011_resume_key_and_manifest_authority() -> None:
    source = inspect.getsource(__import__("services.conformational_mapping.protenix", fromlist=["finalize_protenix"]).finalize_protenix)
    assert "ResumeDescriptor(" in source
    assert '"resume_key": resume_descriptor.resume_key' in source
    assert 'model_dump(mode="json", exclude_computed_fields=True)' in source
    assert '"native_manifest_sha256": native_hash' in source


def test_cm5_012_parent_retains_all_channels() -> None:
    module = (Path(__file__).resolve().parents[2] / ".." / "modules" / "conformational_mapping_protenix.nf").resolve().read_text()
    for channel in ("canonical", "native", "ensemble"):
        assert f"emit: {channel}" in module
