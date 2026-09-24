"""Server-owned workflow adapter and Project native-owner authority."""

from __future__ import annotations

from typing import Any


TYPED_CORE_JOB_MODELS = {
    "boltz2",
    "boltz_cp_experimental",
    "boltzgen",
    "esmfold2",
    "molecular_dynamics",
    "nanopore",
    "ngs_alignment",
    "oligo_builder",
    "oligo_design",
    "ont_fastq_qc",
    "ppiflow",
    "protein_local_redesign",
    "protein_modification_experimental",
    "protenix",
    "rf3",
    "sequence_qc",
    "template_antibody_denovo",
    "antibody_denovo",
    "bindcraft2",
    "binder_refinement",
    "caliby_binder",
    "frustrampnn",
    "ligandmpnn",
}
TYPED_CORE_JOB_ADAPTERS = {
    f"bms.core-job.{model_id}.adapter.v1": model_id
    for model_id in sorted(TYPED_CORE_JOB_MODELS)
}
PROJECT_SCHEDULED_TYPED_CORE_ADAPTERS = {"bms.ngs.job-reference.adapter.v1"}

WORKFLOW_ADAPTER_REGISTRY: dict[str, set[str]] = {
    "generic_test": {"generic.test.adapter.v1"},
    "typed_core_job": set(TYPED_CORE_JOB_ADAPTERS),
    "conformational_mapping": {
        "bms.cm.protenix_v2.adapter.v1",
        "bms.cm.confornets.adapter.v1",
    },
}

PROJECT_NATIVE_OWNER_REGISTRY: dict[str, dict[str, Any]] = {
    "structure_prediction": {
        "setup_path": "/submit",
        "supports_initial_values": True,
        "supports_draft_reporting": True,
    },
    "antibody_denovo": {
        "setup_path": "/submit",
        "supports_initial_values": True,
        "supports_draft_reporting": True,
    },
    "conformational_mapping": {
        "setup_path": "/submit",
        "supports_initial_values": True,
        "supports_draft_reporting": True,
    },
}


# JobSubmission's native generation forms report/reopen these exact modes.
for _model, _modes in {
    "boltzgen": ("protein_binder", "nanobody_binder", "peptide_binder"),
    "ppiflow": ("protein_binder", "antibody_binder", "nanobody_binder"),
}.items():
    for _mode in _modes:
        PROJECT_NATIVE_OWNER_REGISTRY[f"{_model}:{_mode}"] = {
            "setup_path": "/submit", "setup_query": {"model": [_model], "mode": [_mode]},
            "supports_initial_values": True, "supports_draft_reporting": True,
        }


def register_workflow_adapter(workflow_family: str, adapter_id: str) -> None:
    """Register a server-owned workflow adapter; callers cannot register via HTTP."""
    WORKFLOW_ADAPTER_REGISTRY.setdefault(workflow_family, set()).add(adapter_id)


def is_workflow_adapter_registered(workflow_family: Any, adapter_id: Any) -> bool:
    """Return whether one exact workflow-family adapter pair is registered."""
    return bool(
        isinstance(workflow_family, str)
        and workflow_family
        and workflow_family == workflow_family.strip()
        and isinstance(adapter_id, str)
        and adapter_id
        and adapter_id == adapter_id.strip()
        and adapter_id in WORKFLOW_ADAPTER_REGISTRY.get(workflow_family, set())
    )


def is_project_native_owner_registered(native_owner_id: Any) -> bool:
    """Return whether one native owner has a complete Project state contract."""
    if not isinstance(native_owner_id, str) or native_owner_id != native_owner_id.strip():
        return False
    contract = PROJECT_NATIVE_OWNER_REGISTRY.get(native_owner_id)
    return bool(
        isinstance(contract, dict)
        and set(contract) in ({
            "setup_path",
            "supports_initial_values",
            "supports_draft_reporting",
        }, {"setup_path", "setup_query", "supports_initial_values", "supports_draft_reporting"})
        and contract["setup_path"] == "/submit"
        and contract["supports_initial_values"] is True
        and contract["supports_draft_reporting"] is True
        and ("setup_query" not in contract or (
            isinstance(contract["setup_query"], dict)
            and set(contract["setup_query"]) == {"model", "mode"}
            and all(isinstance(values, list) and len(values) == 1
                    and isinstance(values[0], str) and bool(values[0])
                    for values in contract["setup_query"].values())
        ))
    )


def is_project_native_destination_registered(native_owner_id: Any, destination: Any) -> bool:
    from urllib.parse import parse_qs, urlsplit
    if not is_project_native_owner_registered(native_owner_id) or not isinstance(destination, str):
        return False
    try:
        parsed = urlsplit(destination)
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    contract = PROJECT_NATIVE_OWNER_REGISTRY[native_owner_id]
    if parsed.scheme or parsed.netloc or parsed.fragment or parsed.path != contract["setup_path"]:
        return False
    if "setup_query" in contract:
        return query == contract["setup_query"]
    return query.get("template") == [native_owner_id]
