"""Narrow S-01 foundation; no producer is activated by this package.

Provenance is authority. Params are transport only. This module does not infer
scientific correspondence, units, residue scopes, or metric meaning. Producers
must supply approved descriptors and consumers must supply trusted source identity.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
import math
import re
from types import MappingProxyType
from typing import Any

REVISION_KEY = "core_protein_scientific_contract"
REVISION = 1

# Exact model/mode inventory: config/models/*.yaml and nextflow.py explicit
# routes. No inference from the generic protein_design.nf fallback. ESMFold2
# complex is deliberately absent: its registry admits only predict today.
SUPPORTED_CALLERS = MappingProxyType({
    **{(model, mode): "structure_prediction" for model in ("boltz2", "protenix") for mode in ("predict", "complex")},
    **{(model, "predict"): "structure_prediction" for model in ("esmfold2", "esmfold2_experimental")},
    **{("boltzgen", mode): "boltzgen" for mode in (
        "ligand_binder", "ntp_binder", "scaffold_around_ligand", "backbone_docking", "nanobody_binder", "peptide_binder")},
    **{("boltzgen_child", mode): "boltzgen" for mode in ("nanobody_binder", "peptide_binder", "protein_binder")},
    **{("fampnn", mode): "fampnn" for mode in ("design", "fixed_backbone", "binder_design")},
    ("fampnn_child", "sequence_design"): "fampnn",
    **{("antibody_denovo", mode): "antibody" for mode in (
        "antibody_denovo_pipeline", "antibody_refinement_pipeline", "backbone", "sequence",
        "validation", "immunogenicity", "stability", "maturation")},
    **{("antibody_child", mode): "antibody" for mode in ("validation", "validation_batch")},
    ("protein_modification_experimental", "de_novo_design"): "protein_design",
    ("protein_modification_experimental", "region_redesign"): "local_redesign",
    ("protein_local_redesign", "local_redesign"): "local_redesign",
})
# Compile-time release gate, NOT environment/config/request driven. Populate only
# after the corresponding producers, children, consumers and scopes pass integration.
ACTIVATED_CALLERS: frozenset[tuple[str, str]] = frozenset()


def reject_reserved_marker(payload: Any) -> None:
    """Reject the exact reserved key anywhere in an untrusted JSON payload."""
    pending = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            reserved = {REVISION_KEY, 'core_protein_requested_params',
                        'esmf_requested_settings_json', 'openmm_requested_settings_json',
                        'fampnn_analysis_policy', 'fampnn_analysis_declaration',
                        'fampnn_analysis_declaration_path', 'fampnn_analysis_declaration_sha256'}
            forged = reserved.intersection(value)
            if forged:
                raise ValueError(f"{sorted(forged)[0]} is server-owned")
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)


def _revision(value: Any) -> int:
    if type(value) is not int or value != REVISION:
        raise ValueError("invalid core protein scientific contract revision")
    return REVISION


def revision_for_job(job: Any) -> int | None:
    """Read only trusted persisted Job.provenance; invalid is never legacy."""
    provenance = getattr(job, "provenance", None)
    if provenance is None:
        return None
    if not isinstance(provenance, Mapping):
        raise ValueError("invalid scientific revision provenance")
    return _revision(provenance[REVISION_KEY]) if REVISION_KEY in provenance else None


def admission_revision(model_id: str, mode: str, *, parent: Any = None,
                       scientific_child: bool = False) -> int | None:
    """Resolve NEW admission from the current caller's release gate only.

    Parent/child metadata is not revision authority. Existing persisted rows and
    true same-attempt resumes use revision_for_job instead of this function.
    """
    caller = (model_id, mode)
    if caller not in SUPPORTED_CALLERS:
        return None
    return REVISION if caller in ACTIVATED_CALLERS else None


def admitted_payload(params: Mapping, provenance: Mapping, revision: int | None) -> tuple[dict, dict]:
    """Copy validated inputs; never copy results or modify historical rows."""
    reject_reserved_marker(params)
    reject_reserved_marker(provenance)
    new_params, new_provenance = deepcopy(dict(params)), deepcopy(dict(provenance))
    if revision is not None:
        new_params[REVISION_KEY] = new_provenance[REVISION_KEY] = _revision(revision)
    return new_params, new_provenance


def workflow_params(job: Any, params: Mapping) -> dict:
    """Rebuild the transport marker from persisted authority, without mutation."""
    result = deepcopy(dict(params))
    result.pop(REVISION_KEY, None)
    result.pop('fampnn_analysis_declaration', None)
    result.pop('fampnn_analysis_policy', None)
    reject_reserved_marker(result)
    revision = revision_for_job(job)
    if revision is not None:
        result[REVISION_KEY] = revision
        if getattr(job, 'model_id', None) in {'boltzgen', 'boltzgen_child'}:
            from services.boltzgen_request_compatibility import compile_boltzgen_settings
            result = compile_boltzgen_settings(result)
        declaration = (getattr(job, 'provenance', None) or {}).get('fampnn_analysis_declaration')
        if declaration is not None:
            result['fampnn_analysis_declaration'] = deepcopy(declaration)
    return result


_DESCRIPTOR_KEYS = frozenset({"metric_key", "unit", "direction", "scope", "producer_version", "derivation_version"})
_SOURCE_KEYS = frozenset({"artifact_sha256", "candidate_id", "document_id"})
_METRIC_KEYS = _DESCRIPTOR_KEYS | {"state", "value", "reason_code", "source"}


def _closed(payload: Any, keys: frozenset | set, label: str) -> dict:
    if not isinstance(payload, Mapping) or set(payload) != keys:
        raise ValueError(f"{label} requires exact schema fields")
    return dict(payload)


def _text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} requires nonempty text")


def validate_descriptor(payload: Any) -> dict:
    """Same key/unit/direction/scope vocabulary as design_metrics.metric_record.

    No aliases or unit inference. Owner-supplied descriptors establish exact
    meaning; this foundation intentionally does not introduce a metric registry.
    """
    descriptor = _closed(payload, _DESCRIPTOR_KEYS, "metric descriptor")
    for key, value in descriptor.items():
        _text(value, key)
    if descriptor["direction"] not in {"lower_is_better", "higher_is_better", "neutral"}:
        raise ValueError("invalid metric direction")
    return descriptor


def _source(payload: Any) -> dict:
    source = _closed(payload, _SOURCE_KEYS, "metric source")
    for key, value in source.items():
        _text(value, key)
    if not re.fullmatch(r"[0-9a-f]{64}", source["artifact_sha256"]):
        raise ValueError("invalid source artifact SHA-256")
    return source


def validate_metric(payload: Any, *, expected_source: Mapping | None = None) -> dict:
    """Validate representation; external source authority is required at ingestion.

    Identity equality is necessary, not biological correspondence proof. The
    caller must establish candidate/document mapping, not infer it from filenames,
    sorting, lengths or hashes. No existing legacy records are rewritten here.
    """
    metric = _closed(payload, _METRIC_KEYS, "metric")
    validate_descriptor({key: metric[key] for key in _DESCRIPTOR_KEYS})
    state, value, reason = metric["state"], metric["value"], metric["reason_code"]
    if state == "ok":
        if type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value)):
            raise ValueError("ok metric requires a finite real scalar, without coercion")
        if reason is not None:
            raise ValueError("ok metric requires null reason_code")
    elif state in ("unavailable", "invalid"):
        if value is not None:
            raise ValueError("non-ok metric requires null value")
        _text(reason, "reason_code")
    else:
        raise ValueError("invalid metric state")
    metric["source"] = _source(metric["source"])
    if expected_source is not None and metric["source"] != _source(expected_source):
        raise ValueError("metric source does not match trusted candidate/document/source authority")
    return metric


def validate_metrics(payloads: list, descriptors: list, *, expected_source: Mapping) -> list[dict]:
    """Validate an exact descriptor set and reject duplicate/conflicting IDs."""
    expected_source = _source(expected_source)
    if type(payloads) is not list or type(descriptors) is not list:
        raise ValueError("metrics and descriptors require lists")
    by_id = {}
    for payload in descriptors:
        descriptor = validate_descriptor(payload)
        key = descriptor["metric_key"]
        if key in by_id:
            raise ValueError("duplicate metric descriptor ID")
        by_id[key] = descriptor
    result, seen = [], set()
    for payload in payloads:
        metric = validate_metric(payload, expected_source=expected_source)
        key = metric["metric_key"]
        if key in seen:
            raise ValueError("duplicate metric ID")
        if {field: metric[field] for field in _DESCRIPTOR_KEYS} != by_id.get(key):
            raise ValueError("metric descriptor conflict or unknown metric ID")
        seen.add(key)
        result.append(metric)
    if seen != set(by_id):
        raise ValueError("missing required metric envelope")
    return result


def canonical_metric_json(payload: Any) -> str:
    return json.dumps(validate_metric(payload), allow_nan=False, sort_keys=True, separators=(",", ":"))
