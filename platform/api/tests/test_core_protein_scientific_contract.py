"""S-01 foundation only; no scientific runtime or global application import."""
from copy import deepcopy
import importlib
import importlib.util
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from schemas import JobCreate

KEY = "core_protein_scientific_contract"
CALLER = ("boltz2", "predict")


def contract():
    name = "services.core_protein_scientific_contract"
    assert importlib.util.find_spec(name) is not None, "S-01 shared contract is missing"
    return importlib.import_module(name)


@pytest.mark.parametrize("payload", [
    {KEY: 1}, {"params": {KEY: 0}},
    {"params": {"extras": [{"nested": {KEY: None}}]}},
    {"ignored_extra": {KEY: "1"}},
])
@pytest.mark.parametrize("model", ["boltz2", "template_antibody_denovo", "nanopore"])
def test_caller_cannot_supply_reserved_marker(payload, model):
    with pytest.raises(ValidationError, match="server-owned"):
        JobCreate.model_validate({"name": "new", "model_id": model, "mode": "predict", **payload})


def test_activation_is_empty_and_not_request_owned(monkeypatch):
    c = contract()
    assert c.ACTIVATED_CALLERS == frozenset()
    assert CALLER in c.SUPPORTED_CALLERS
    assert c.admission_revision(*CALLER) is None
    monkeypatch.setattr(c, "ACTIVATED_CALLERS", frozenset({CALLER, ("rf3", "predict")}))
    assert c.admission_revision(*CALLER) == 1
    for caller in [("rf3", "predict"), ("nanopore", "basecall"), ("frustrampnn", "analyze"),
                   ("protein_modification_experimental", "shape_blueprint"), ("unknown", "predict")]:
        assert c.admission_revision(*caller) is None


@pytest.mark.parametrize("value", [True, False, "1", 1.0, 0, 2, None, {}, []])
def test_invalid_persisted_revision_is_not_legacy(value):
    with pytest.raises(ValueError, match="revision"):
        contract().revision_for_job(SimpleNamespace(provenance={KEY: value}))


def test_only_persisted_provenance_is_authority():
    c = contract()
    job = SimpleNamespace(provenance=None, params={KEY: 1})
    assert c.revision_for_job(job) is None
    job.provenance = {KEY: 1}
    job.params = {KEY: 0}
    assert c.revision_for_job(job) == 1


def test_new_scientific_child_uses_current_caller_not_parent_revision(monkeypatch):
    c = contract()
    monkeypatch.setattr(c, "ACTIVATED_CALLERS", frozenset({CALLER}))
    old = SimpleNamespace(provenance={"old": "untouched"})
    marked = SimpleNamespace(provenance={KEY: 1})
    assert c.admission_revision(*CALLER, parent=old, scientific_child=True) == 1
    assert c.admission_revision(*CALLER, parent=marked, scientific_child=True) == 1
    assert c.admission_revision(*CALLER, parent=old) == 1
    assert c.admission_revision(*CALLER, scientific_child=True) == 1
    monkeypatch.setattr(c, "ACTIVATED_CALLERS", frozenset())
    assert c.admission_revision(*CALLER, parent=marked, scientific_child=True) is None


def test_old_rows_and_new_clone_are_independent(tmp_path, monkeypatch):
    import sqlite3
    c = contract()
    monkeypatch.setattr(c, "ACTIVATED_CALLERS", frozenset({CALLER}))
    original = {"params": {"sequence": "ACDE"}, "provenance": {"legacy": True}, "derived_score": 91}
    raw = json.dumps(original)
    with sqlite3.connect(tmp_path / "old.sqlite") as db:
        db.execute("create table old_jobs (payload text)")
        db.execute("insert into old_jobs values (?)", (raw,))
        old = SimpleNamespace(**json.loads(db.execute("select payload from old_jobs").fetchone()[0]))
        new_params, new_provenance = c.admitted_payload(old.params, {}, c.admission_revision(*CALLER))
        assert new_params == {"sequence": "ACDE", KEY: 1}
        assert new_provenance == {KEY: 1}
        assert db.execute("select payload from old_jobs").fetchone()[0] == raw
        assert old.params == original["params"]


def test_workflow_marker_is_rebuilt_from_provenance():
    c = contract()
    original = {"sequence": "ACDE", KEY: 0}
    marked = SimpleNamespace(provenance={KEY: 1})
    assert c.workflow_params(marked, original) == {"sequence": "ACDE", KEY: 1}
    assert c.workflow_params(SimpleNamespace(provenance={}), original) == {"sequence": "ACDE"}
    assert original[KEY] == 0


def metric_payload():
    return {
        "metric_key": "fixture.distance", "state": "ok", "value": 2.5, "reason_code": None,
        "unit": "angstrom", "direction": "lower_is_better", "scope": "input_protein_residues",
        "producer_version": "fixture-producer/1", "derivation_version": "fixture-mean/1",
        "source": {"artifact_sha256": "a" * 64, "candidate_id": "candidate-A", "document_id": "doc-A"},
    }


@pytest.mark.parametrize("value", [True, "2.5", float("nan"), float("inf"), -float("inf"), None])
def test_ok_state_rejects_noncanonical_scalar(value):
    payload = metric_payload()
    payload["value"] = value
    with pytest.raises(ValueError):
        contract().validate_metric(payload)


@pytest.mark.parametrize("state", ["unavailable", "invalid"])
def test_non_ok_state_requires_null_and_reason(state):
    c = contract()
    payload = {**metric_payload(), "state": state, "value": None, "reason_code": "missing_evidence"}
    assert c.validate_metric(payload) == payload
    for bad in [{"value": 0}, {"reason_code": None}, {"reason_code": " "}]:
        with pytest.raises(ValueError):
            c.validate_metric({**payload, **bad})


def test_closed_schema_and_no_coercion():
    c = contract()
    payload = metric_payload()
    assert c.validate_metric(payload) == payload
    assert json.loads(c.canonical_metric_json(payload)) == payload
    for change in [{"surprise": 1}, {"state": "missing"}, {"reason_code": "warning"},
                   {"unit": ""}, {"producer_version": ""}, {"derivation_version": ""},
                   {"direction": "up"}, {"source": {**payload["source"], "path": "a.pdb"}}]:
        with pytest.raises(ValueError):
            c.validate_metric({**payload, **change})


@pytest.mark.parametrize("field", ["candidate_id", "document_id", "artifact_sha256"])
def test_candidate_document_and_source_must_match_external_authority(field):
    c = contract()
    payload = metric_payload()
    expected = deepcopy(payload["source"])
    expected[field] = "b" * 64 if field == "artifact_sha256" else "other"
    with pytest.raises(ValueError, match="source"):
        c.validate_metric(payload, expected_source=expected)
    assert c.validate_metric(payload, expected_source=payload["source"]) == payload


def test_collection_requires_external_source_even_when_empty():
    c = contract()
    payload = metric_payload()
    descriptor = {k: payload[k] for k in ("metric_key", "unit", "direction", "scope", "producer_version", "derivation_version")}
    with pytest.raises(ValueError, match="source"):
        c.validate_metrics([payload], [descriptor], expected_source=None)
    with pytest.raises(ValueError, match="source"):
        c.validate_metrics([], [], expected_source=None)


@pytest.mark.parametrize("source", [
    None, "source", [], 1, True, {},
    {"artifact_sha256": "not-a-hash", "candidate_id": "candidate-A", "document_id": "doc-A"},
    {"artifact_sha256": "a" * 64, "document_id": "doc-A"},
    {"artifact_sha256": "a" * 64, "candidate_id": None, "document_id": "doc-A"},
])
@pytest.mark.parametrize("empty", [False, True])
def test_collection_rejects_invalid_external_source(source, empty):
    payload = metric_payload()
    descriptor = {k: payload[k] for k in ("metric_key", "unit", "direction", "scope", "producer_version", "derivation_version")}
    with pytest.raises(ValueError):
        contract().validate_metrics([] if empty else [payload], [] if empty else [descriptor], expected_source=source)


@pytest.mark.parametrize("field", ["candidate_id", "document_id", "artifact_sha256"])
def test_collection_enforces_external_source_equality(field):
    payload = metric_payload()
    descriptor = {k: payload[k] for k in ("metric_key", "unit", "direction", "scope", "producer_version", "derivation_version")}
    source = {**payload["source"], field: "b" * 64 if field == "artifact_sha256" else "other"}
    with pytest.raises(ValueError, match="source"):
        contract().validate_metrics([payload], [descriptor], expected_source=source)


def test_single_metric_optional_authority_and_empty_collection_with_authority():
    c = contract()
    payload = metric_payload()
    assert c.validate_metric(payload) == payload
    assert c.validate_metric(payload, expected_source=None) == payload
    assert c.validate_metrics([], [], expected_source=payload["source"]) == []


def test_duplicate_metric_ids_and_descriptor_conflicts_reject():
    c = contract()
    payload = metric_payload()
    descriptor = {k: payload[k] for k in ("metric_key", "unit", "direction", "scope", "producer_version", "derivation_version")}
    assert c.validate_metrics([payload], [descriptor], expected_source=payload["source"]) == [payload]
    for metrics, descriptors in [([payload, payload], [descriptor]), ([payload], [descriptor, descriptor]),
                                  ([{**payload, "unit": "fraction"}], [descriptor])]:
        with pytest.raises(ValueError):
            c.validate_metrics(metrics, descriptors, expected_source=payload["source"])
