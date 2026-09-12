from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from services.ont_ngs_decision_projection import (
    OntNgsDecisionProjectionError,
    _CHECKS,
    _project_check,
    _project_topology_source,
    project_verification_manifest,
)
from tests.test_construct_verification_phase2 import REFERENCE, _run_case

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((ROOT / "schemas/ngs/ont_fastq_qc_result_v1.schema.json").read_text())


@pytest.mark.parametrize("case", [
    {},
    {"observed": None},
    {"malformed_observed_state": True},
    {"malformed_support": True},
    {"alignment_counts": (31, 30, 1)},
    {"topology_state": "unavailable"},
    {"declared_reference_digest": None},
    {"alignment_index_state": "missing"},
    {"observed": "N" + REFERENCE[1:]},
])
def test_real_verifier_failure_branches_are_readable(tmp_path: Path, case: dict) -> None:
    """The actual verifier, not a success-shaped stand-in, defines sparse metrics."""
    process, manifest, _ = _run_case(tmp_path, **case)
    assert process.returncode == 0, process.stderr
    before = copy.deepcopy(manifest)
    projected = project_verification_manifest(manifest)
    assert manifest == before
    assert projected["verdict"] == manifest["verdict"]
    assert projected["reason_codes"] == manifest["reason_codes"]
    assert projected["summary"] == manifest["summary"]
    for source, (name, _, _) in _CHECKS.items():
        raw = manifest["checks"][source]
        check = projected["checks"][name]
        assert check["status"] == raw["status"]
        assert check["reason_codes"] == raw["reason_codes"]
        if source != "topology":
            assert check["metrics"] == raw["metrics"]
        assert set(check["units"]) <= set(check["metrics"])
    validator = Draft202012Validator({
        "$defs": SCHEMA["$defs"], **SCHEMA["properties"]["verification"],
    })
    assert list(validator.iter_errors(projected)) == []


def test_check_projects_additive_evidence_without_inventing_units() -> None:
    check = {"status": "review", "reason_codes": ["VARIANT_CALLING_UNAVAILABLE"],
             "metrics": {"error": "variant calling unavailable", "identity_fraction": None},
             "producer_note": "additional metadata"}
    projected = _project_check(check, purpose="Observed identity", units={"identity_fraction": "fraction"})
    assert projected["metrics"] == check["metrics"]
    assert projected["units"] == {"identity_fraction": "fraction"}


def test_topology_projects_only_observed_provenance_fields() -> None:
    projected = _project_topology_source({
        "status": "review", "reason_codes": ["TOPOLOGY_EVIDENCE_UNAVAILABLE"],
        "metrics": {"state": "unavailable", "provenance": {"reference_sha256": "a" * 64}},
    })
    assert projected["metrics"] == {"state": "unavailable", "evidence_sha256": {"reference": "a" * 64}}


@pytest.mark.parametrize("change", [
    {"status": "invented_pass"}, {"reason_codes": "FAIL"}, {"metrics": []},
])
def test_invalid_check_envelope_remains_rejected(change: dict) -> None:
    raw = {"status": "review", "reason_codes": [], "metrics": {}, **change}
    with pytest.raises(OntNgsDecisionProjectionError):
        _project_check(raw, purpose="Observed identity", units={})
