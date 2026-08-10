from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from routers.conformational_mapping import SubmitRequest
from services.conformational_mapping.request_builder import (
    ConformationalMappingRequestError,
    materialize_trusted_internal_request,
)
from services.frustrampnn.settings import default_settings


def _external_import_params() -> dict[str, object]:
    return {
        "backend": "external_import",
        "targets": [{"target_id": "target-a", "target_order": 0}],
        "ordered_seeds": [0],
        "samples_per_seed": 1,
        "feature_policy": {
            "mode": "regenerate_mutated_protein_v1",
            "protein_msa_enabled": True,
            "templates_enabled": True,
            "rna_msa_enabled": True,
        },
        "runtime_policy": {"use_default_params": True},
        "analysis_policy": {
            "sign_zero_epsilon": 0.000001,
            "clash_detector_id": "bms_clash",
            "clash_detector_version": "1",
            "outer_support_minimum": 0.8,
            "inner_support_minimum": 0.6,
            "sign_consistency_minimum": 0.8,
            "clash_free_minimum": 0.9,
            "rank_stability_minimum": 0.6,
            "minimum_common_ranked_universe_size": 3,
        },
        "import_receipt_id": "9" * 64,
        "resolved_import_entries": [
            {"staged_index": 0, "source_content_sha256": "8" * 64}
        ],
    }


def test_cm_materialization_binds_canonical_default_settings_and_requiredness(
    tmp_path: Path,
) -> None:
    materialized = materialize_trusted_internal_request(
        _external_import_params(),
        output_dir=tmp_path,
        request_id="00000000-0000-4000-8000-000000000901",
    )
    request = json.loads(materialized.request_path.read_text(encoding="utf-8"))

    assert request["frustrampnn_settings"] == default_settings().model_dump(
        mode="json", exclude_none=False
    )
    assert request["frustrampnn_settings"]["settings_value_origin"] == "bms_default"
    assert request["frustrampnn_requiredness"] == "required"


def _submit_payload() -> dict[str, object]:
    return {
        "name": "CM canonical settings",
        "backend": "external_import",
        "registered_artifact_ids": ["artifact-1"],
        "registered_snapshot_id": "snapshot-1",
        "ordered_seeds": [0],
        "samples_per_seed": 1,
        "feature_policy": {
            "mode": "regenerate_mutated_protein_v1",
            "protein_msa_enabled": True,
            "templates_enabled": True,
            "rna_msa_enabled": True,
        },
        "runtime_policy": {"use_default_params": True},
        "analysis_policy": {
            "sign_zero_epsilon": 0.000001,
            "clash_detector_id": "bms_clash",
            "clash_detector_version": "1",
            "outer_support_minimum": 0.8,
            "inner_support_minimum": 0.6,
            "sign_consistency_minimum": 0.8,
            "clash_free_minimum": 0.9,
            "rank_stability_minimum": 0.6,
            "minimum_common_ranked_universe_size": 3,
        },
    }


def test_cm_submission_omission_binds_complete_canonical_defaults() -> None:
    body = SubmitRequest.model_validate(_submit_payload())

    assert body.frustrampnn_settings == default_settings()
    assert body.frustrampnn_settings.settings_value_origin == "bms_default"


def test_cm_submission_accepts_complete_settings_but_rejects_caller_owned_origin() -> None:
    complete = default_settings().model_dump(mode="json", exclude_none=False)
    complete.pop("settings_value_origin")
    complete["classification_policy"] = {
        "mode": "custom",
        "high_max": -0.75,
        "minimal_min": 0.7,
    }
    body = SubmitRequest.model_validate(
        {**_submit_payload(), "frustrampnn_settings": complete}
    )
    assert body.frustrampnn_settings.settings_value_origin == "operator_request"
    assert body.frustrampnn_settings.classification_policy.high_max == -0.75

    forged = dict(complete)
    forged["settings_value_origin"] = "bms_default"
    with pytest.raises(ValidationError, match="origin"):
        SubmitRequest.model_validate(
            {**_submit_payload(), "frustrampnn_settings": forged}
        )


def test_cm_materialization_preserves_complete_server_bound_settings(
    tmp_path: Path,
) -> None:
    supplied = default_settings().model_dump(mode="json", exclude_none=False)
    supplied["settings_value_origin"] = "operator_request"
    supplied["classification_policy"] = {
        "mode": "custom",
        "high_max": -0.75,
        "minimal_min": 0.7,
    }
    params = _external_import_params()
    params["frustrampnn_settings"] = supplied

    materialized = materialize_trusted_internal_request(
        params,
        output_dir=tmp_path,
        request_id="00000000-0000-4000-8000-000000000902",
    )
    request = json.loads(materialized.request_path.read_text(encoding="utf-8"))

    assert request["frustrampnn_settings"] == supplied
    assert request["frustrampnn_requiredness"] == "required"


def test_cm_materialization_rejects_incomplete_server_bound_settings(
    tmp_path: Path,
) -> None:
    params = _external_import_params()
    params["frustrampnn_settings"] = {
        "schema_name": "frustrampnn_settings",
        "schema_version": 1,
        "settings_value_origin": "operator_request",
    }
    with pytest.raises(ConformationalMappingRequestError, match="complete"):
        materialize_trusted_internal_request(
            params,
            output_dir=tmp_path,
            request_id="00000000-0000-4000-8000-000000000903",
        )


def test_cm_materialization_rejects_explicit_null_settings(tmp_path: Path) -> None:
    params = _external_import_params()
    params["frustrampnn_settings"] = None

    with pytest.raises(ConformationalMappingRequestError, match="complete"):
        materialize_trusted_internal_request(
            params,
            output_dir=tmp_path,
            request_id="00000000-0000-4000-8000-000000000905",
        )


@pytest.mark.parametrize("caller_requiredness", ["required", "optional", "unknown"])
def test_cm_materialization_rejects_caller_owned_requiredness(
    tmp_path: Path,
    caller_requiredness: str,
) -> None:
    params = _external_import_params()
    params["frustrampnn_requiredness"] = caller_requiredness
    with pytest.raises(ConformationalMappingRequestError, match="unknown request fields"):
        materialize_trusted_internal_request(
            params,
            output_dir=tmp_path,
            request_id="00000000-0000-4000-8000-000000000904",
        )
