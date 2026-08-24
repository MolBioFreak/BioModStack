from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path

import pytest
from pydantic import ValidationError


API_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = API_ROOT / "config/models/frustrampnn_capability_inventory_v1.json"


def _settings_module():
    path = API_ROOT / "services" / "frustrampnn" / "settings.py"
    assert path.is_file(), "canonical typed FrustraMPNN settings authority is missing"
    from services.frustrampnn import settings

    return settings


def _entity(instance: str = "entity-1", chain: str = "A") -> dict[str, object]:
    return {
        "entity_instance_id": instance,
        "source_entity_id": "1" if instance == "entity-1" else "2",
        "label_asym_id": chain,
        "auth_asym_id": chain,
    }


def _residue(
    sequence_index: int = 10,
    *,
    instance: str = "entity-1",
    chain: str = "A",
) -> dict[str, object]:
    return {
        **_entity(instance, chain),
        "auth_seq_id": sequence_index,
        "insertion_code": "",
        "sequence_index": sequence_index,
    }


def _region(
    sequence_start: int = 10,
    sequence_end: int = 20,
    *,
    instance: str = "entity-1",
    chain: str = "A",
) -> dict[str, object]:
    return {
        **_entity(instance, chain),
        "sequence_start": sequence_start,
        "sequence_end": sequence_end,
    }


def _requested(**overrides):
    settings = _settings_module()
    payload = {
        "schema_name": "frustrampnn_settings",
        "schema_version": 1,
        "settings_value_origin": "operator_request",
        "protein_selection": {"mode": "all_protein_entities"},
        "source_structure": {
            "selected_model_number": 1,
            "preferred_altloc": "",
        },
        "classification_policy": {
            "mode": "canonical",
            "high_max": -1.0,
            "minimal_min": 0.58,
        },
    }
    payload.update(overrides)
    return settings.FrustraMPNNRequestedSettings.model_validate(payload)


def _resolved_residue(
    sequence_index: int,
    *,
    model_position: int,
    wt: str = "A",
    instance: str = "entity-1",
    chain: str = "A",
):
    settings = _settings_module()
    return settings.FrustraMPNNResolvedResidue.model_validate(
        {
            **_residue(sequence_index, instance=instance, chain=chain),
            "label_seq_id": sequence_index,
            "wt": wt,
            "pdb_chain_id": chain,
            "pdb_residue_id": sequence_index,
            "pdb_insertion_code": "",
            "model_position": model_position,
            "residue_name": "ALA" if wt == "A" else "LEU" if wt == "L" else "VAL",
        }
    )


def _resolved_chain(
    *residues,
    instance: str = "entity-1",
    chain: str = "A",
):
    settings = _settings_module()
    return settings.FrustraMPNNResolvedChainSelection.model_validate(
        {
            "entity": _entity(instance, chain),
            "pdb_chain_id": chain,
            "residues": list(residues),
        }
    )


def _resolution_identity():
    settings = _settings_module()
    return settings.FrustraMPNNResolutionIdentity.model_validate(
        {
            "source_artifact_sha256": "a" * 64,
            "structure_map_schema_name": "frustrampnn_structure_map",
            "structure_map_schema_version": 1,
            "structure_map_sha256": "b" * 64,
            "normalized_pdb_sha256": "c" * 64,
        }
    )


def _effective(requested=None, chains=None):
    settings = _settings_module()
    requested = requested or _requested()
    chains = chains or (
        _resolved_chain(
            _resolved_residue(10, model_position=9, wt="L"),
            _resolved_residue(1, model_position=0, wt="M"),
        ),
    )
    return settings._build_effective_settings(
        requested,
        resolved_chains=tuple(chains),
        resolution_identity=_resolution_identity(),
    )


def test_requested_models_are_closed_strict_and_canonical() -> None:
    settings = _settings_module()

    with pytest.raises(ValidationError, match="extra_forbidden"):
        _requested(normalized_pdb_chain="A")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _requested(
            source_structure={
                "selected_model_number": 1,
                "preferred_altloc": "",
                "model_positions": [0],
            }
        )
    with pytest.raises(ValidationError):
        _requested(
            source_structure={
                "selected_model_number": True,
                "preferred_altloc": "",
            }
        )
    with pytest.raises(ValidationError):
        settings.FrustraMPNNClassificationPolicy.model_validate(
            {"mode": "custom", "high_max": "-1.0", "minimal_min": 0.58}
        )

    first = _requested(
        protein_selection={
            "mode": "selected_residues",
            "residues": [_residue(20), _residue(10)],
        }
    )
    second = _requested(
        protein_selection={
            "mode": "selected_residues",
            "residues": [_residue(10), _residue(20)],
        }
    )
    assert [item.sequence_index for item in first.protein_selection.residues] == [10, 20]
    assert settings.requested_settings_sha256(first) == settings.requested_settings_sha256(
        second
    )


def test_requested_selection_modes_remain_fail_closed() -> None:
    with pytest.raises(ValidationError, match="all_protein_entities"):
        _requested(
            protein_selection={
                "mode": "all_protein_entities",
                "entities": [_entity()],
            }
        )
    with pytest.raises(ValidationError, match="selected_entities"):
        _requested(protein_selection={"mode": "selected_entities"})
    with pytest.raises(ValidationError, match="duplicate"):
        _requested(
            protein_selection={
                "mode": "selected_residues",
                "residues": [_residue(), _residue()],
            }
        )
    regions = _requested(
        protein_selection={
            "mode": "selected_regions",
            "regions": [_region(30, 40), _region(10, 20)],
        }
    )
    assert [
        (item.sequence_start, item.sequence_end)
        for item in regions.protein_selection.regions
    ] == [(10, 20), (30, 40)]


def test_effective_region_validation_never_expands_caller_controlled_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings_module()
    requested = _requested(
        protein_selection={
            "mode": "selected_regions",
            "regions": [_region(10, 10)],
        }
    )
    effective = _effective(
        requested=requested,
        chains=(
            _resolved_chain(
                _resolved_residue(10, model_position=9, wt="L"),
            ),
        ),
    )
    payload = effective.model_dump(mode="json", exclude_none=False)
    payload["requested_settings"]["protein_selection"]["regions"][0][
        "sequence_end"
    ] = 10**12
    real_range = range

    def bounded_range(*values: int):
        if any(abs(value) > 10_000 for value in values):
            raise RuntimeError("caller-controlled interval was expanded")
        return real_range(*values)

    monkeypatch.setattr(settings, "range", bounded_range, raising=False)
    with pytest.raises(ValidationError, match="coverage|span"):
        settings.FrustraMPNNEffectiveSettings.model_validate(payload, strict=True)


def test_requested_selection_modes_reject_overlapping_or_invalid_regions() -> None:
    settings = _settings_module()
    with pytest.raises(ValidationError, match="overlap"):
        _requested(
            protein_selection={
                "mode": "selected_regions",
                "regions": [_region(10, 20), _region(20, 30)],
            }
        )
    with pytest.raises(ValidationError, match="sequence_start"):
        _requested(
            protein_selection={
                "mode": "selected_regions",
                "regions": [_region(20, 10)],
            }
        )
    for high_max, minimal_min in (
        (0.5, 0.5),
        (1.0, 0.5),
        (math.nan, 0.58),
        (-1.0, math.inf),
    ):
        with pytest.raises(ValidationError):
            settings.FrustraMPNNClassificationPolicy.model_validate(
                {
                    "mode": "custom",
                    "high_max": high_max,
                    "minimal_min": minimal_min,
                }
            )


def test_default_settings_match_installed_behavior_and_have_explicit_value_sources() -> None:
    settings = _settings_module()
    defaults = settings.default_settings()
    assert defaults.protein_selection.mode == "all_protein_entities"
    assert defaults.source_structure.model_dump() == {
        "selected_model_number": 1,
        "preferred_altloc": "",
    }
    assert defaults.classification_policy.model_dump() == {
        "mode": "canonical",
        "high_max": -1.0,
        "minimal_min": 0.58,
    }

    effective = _effective(defaults)
    assert effective.value_sources.model_dump(mode="json") == {
        "protein_selection": {
            "mode": "bms_default",
            "entities": "bms_default",
            "regions": "bms_default",
            "residues": "bms_default",
        },
        "source_structure": {
            "selected_model_number": "bms_default",
            "preferred_altloc": "bms_default",
        },
        "classification_policy": {
            "mode": "bms_default",
            "high_max": "bms_default",
            "minimal_min": "bms_default",
        },
    }


def test_explicit_default_values_remain_operator_request_after_durable_reparse() -> None:
    settings = _settings_module()
    caller_payload = settings.default_settings().model_dump(mode="json", exclude_none=False)
    caller_payload.pop("settings_value_origin")
    explicit = settings.validate_complete_requested_settings(caller_payload)

    assert explicit.settings_value_origin == "operator_request"
    assert settings.requested_settings_sha256(explicit) != settings.requested_settings_sha256(
        settings.default_settings()
    )

    effective = _effective(explicit)
    expected_sources = effective.value_sources.model_dump(mode="json")
    assert {
        source
        for group in expected_sources.values()
        for source in group.values()
    } == {"operator_request"}

    reparsed = settings.FrustraMPNNEffectiveSettings.model_validate(
        effective.model_dump(mode="json", exclude_none=False)
    )
    assert reparsed.requested_settings.settings_value_origin == "operator_request"
    assert reparsed.value_sources == effective.value_sources


def test_caller_cannot_claim_backend_default_origin() -> None:
    settings = _settings_module()
    payload = settings.default_settings().model_dump(mode="json", exclude_none=False)
    payload["settings_value_origin"] = "bms_default"

    with pytest.raises(settings.RequestedSettingsPayloadError, match="origin"):
        settings.validate_complete_requested_settings(payload)


def test_capability_inventory_loader_validates_and_hashes_exact_file_bytes() -> None:
    settings = _settings_module()
    inventory, byte_sha256 = settings.load_capability_inventory()

    assert inventory["schema_name"] == "frustrampnn_capability_inventory"
    assert byte_sha256 == hashlib.sha256(INVENTORY_PATH.read_bytes()).hexdigest()
    assert [item["option_key"] for item in inventory["predict_options"]] == [
        "pdb",
        "checkpoint",
        "output",
        "chains",
        "positions",
        "device",
        "config",
        "quiet",
        "help",
    ]


def test_resolver_builds_complete_deterministic_effective_settings() -> None:
    settings = _settings_module()
    requested = _requested(
        protein_selection={
            "mode": "selected_residues",
            "residues": [_residue(20), _residue(10)],
        },
        source_structure={"selected_model_number": 2, "preferred_altloc": "A"},
        classification_policy={
            "mode": "custom",
            "high_max": -0.75,
            "minimal_min": 0.25,
        },
    )
    chains = (
        _resolved_chain(
            _resolved_residue(20, model_position=19, wt="V"),
            _resolved_residue(10, model_position=9, wt="L"),
        ),
    )

    effective = _effective(requested, chains)
    reversed_effective = _effective(requested, tuple(reversed(chains)))

    assert effective.requested_settings == requested
    assert [row.sequence_index for row in effective.resolved_chains[0].residues] == [
        10,
        20,
    ]
    first_residue = effective.resolved_chains[0].residues[0]
    assert first_residue.model_dump() == {
        **_residue(10),
        "label_seq_id": 10,
        "wt": "L",
        "pdb_chain_id": "A",
        "pdb_residue_id": 10,
        "pdb_insertion_code": "",
        "model_position": 9,
        "residue_name": "LEU",
    }
    assert effective.normalization_policy_id == "frustrampnn_structure_normalizer"
    assert effective.normalization_policy_version == 1
    assert effective.threshold_policy_id == "frustrampnn_class_v1"
    assert effective.threshold_policy_sha256 == settings.classification_policy_sha256(
        requested.classification_policy
    )
    assert effective.settings_sha256 == settings.requested_settings_sha256(requested)
    assert effective.capability_inventory_byte_sha256 == hashlib.sha256(
        INVENTORY_PATH.read_bytes()
    ).hexdigest()
    assert effective.resolution_identity == _resolution_identity()
    assert effective.effective_settings_sha256 == settings.effective_settings_sha256(
        effective
    )
    assert settings.effective_settings_sha256(
        effective
    ) == settings.effective_settings_sha256(reversed_effective)
    assert effective.value_sources.source_structure.selected_model_number == "operator_request"
    assert effective.value_sources.classification_policy.mode == "operator_request"
    assert "pdb_chain_id" not in requested.model_dump(mode="json")
    assert "model_position" not in requested.model_dump(mode="json")


def test_selected_regions_bind_exact_sequence_coverage_and_reject_missing_positions() -> None:
    requested = _requested(
        protein_selection={
            "mode": "selected_regions",
            "regions": [_region(10, 12)],
        }
    )
    complete = _resolved_chain(
        _resolved_residue(10, model_position=9, wt="L"),
        _resolved_residue(11, model_position=10, wt="A"),
        _resolved_residue(12, model_position=11, wt="V"),
    )

    effective = _effective(requested, (complete,))

    assert [
        residue.sequence_index for residue in effective.resolved_chains[0].residues
    ] == [10, 11, 12]
    with pytest.raises((TypeError, ValueError), match="region.*coverage|requested region"):
        _effective(
            requested,
            (
                _resolved_chain(
                    _resolved_residue(10, model_position=9, wt="L"),
                    _resolved_residue(12, model_position=11, wt="V"),
                ),
            ),
        )


def test_resolved_chain_order_is_canonical_before_effective_hashing() -> None:
    chain_a = _resolved_chain(
        _resolved_residue(1, model_position=0, wt="M", instance="entity-1", chain="A"),
        instance="entity-1",
        chain="A",
    )
    chain_b = _resolved_chain(
        _resolved_residue(1, model_position=0, wt="G", instance="entity-2", chain="B"),
        instance="entity-2",
        chain="B",
    )

    first = _effective(chains=(chain_b, chain_a))
    second = _effective(chains=(chain_a, chain_b))

    assert [chain.pdb_chain_id for chain in first.resolved_chains] == ["A", "B"]
    assert first.effective_settings_sha256 == second.effective_settings_sha256


def test_resolver_rejects_untyped_empty_missing_or_foreign_resolution_inputs() -> None:
    settings = _settings_module()
    selected = _requested(
        protein_selection={
            "mode": "selected_residues",
            "residues": [_residue(10)],
        }
    )

    with pytest.raises((TypeError, ValueError), match="typed"):
        settings._build_effective_settings(
            selected,
            resolved_chains=(_resolved_chain(_resolved_residue(10, model_position=9)).model_dump(),),
            resolution_identity=_resolution_identity(),
        )
    with pytest.raises((TypeError, ValueError), match="empty|chain"):
        settings._build_effective_settings(
            selected,
            resolved_chains=(),
            resolution_identity=_resolution_identity(),
        )
    with pytest.raises((TypeError, ValueError), match="requested residue"):
        _effective(
            selected,
            (_resolved_chain(_resolved_residue(11, model_position=10)),),
        )
    with pytest.raises((TypeError, ValueError), match="selected entity"):
        _effective(
            _requested(
                protein_selection={
                    "mode": "selected_entities",
                    "entities": [_entity("entity-1", "A")],
                }
            ),
            (
                _resolved_chain(
                    _resolved_residue(
                        1,
                        model_position=0,
                        instance="entity-2",
                        chain="B",
                    ),
                    instance="entity-2",
                    chain="B",
                ),
            ),
        )


def test_resolved_records_reject_duplicate_and_mismatched_identity() -> None:
    settings = _settings_module()
    residue = _resolved_residue(10, model_position=9)

    with pytest.raises(ValidationError, match="duplicate"):
        _resolved_chain(residue, residue)
    with pytest.raises(ValidationError, match="position"):
        _resolved_chain(
            _resolved_residue(10, model_position=9),
            _resolved_residue(11, model_position=9),
        )
    with pytest.raises(ValidationError, match="entity"):
        settings.FrustraMPNNResolvedChainSelection.model_validate(
            {
                "entity": _entity("entity-1", "A"),
                "pdb_chain_id": "A",
                "residues": [
                    _resolved_residue(
                        10,
                        model_position=9,
                        instance="entity-2",
                        chain="B",
                    ).model_dump(mode="json")
                ],
            }
        )
    mismatched_chain = residue.model_dump(mode="json")
    mismatched_chain["pdb_chain_id"] = "B"
    with pytest.raises(ValidationError, match="chain"):
        settings.FrustraMPNNResolvedChainSelection.model_validate(
            {
                "entity": _entity(),
                "pdb_chain_id": "A",
                "residues": [mismatched_chain],
            }
        )


def test_effective_settings_reject_duplicate_source_entity_or_normalized_chain() -> None:
    chain_a = _resolved_chain(
        _resolved_residue(1, model_position=0, instance="entity-1", chain="A"),
        instance="entity-1",
        chain="A",
    )
    same_entity_other_normalized_chain = _settings_module().FrustraMPNNResolvedChainSelection.model_validate(
        {
            "entity": _entity("entity-1", "A"),
            "pdb_chain_id": "B",
            "residues": [
                {
                    **_residue(2, instance="entity-1", chain="A"),
                    "label_seq_id": 2,
                    "wt": "G",
                    "pdb_chain_id": "B",
                    "pdb_residue_id": 2,
                    "pdb_insertion_code": "",
                    "model_position": 1,
                    "residue_name": "GLY",
                }
            ],
        }
    )
    with pytest.raises((ValidationError, ValueError), match="entity"):
        _effective(chains=(chain_a, same_entity_other_normalized_chain))

    other_entity_same_normalized_chain = _settings_module().FrustraMPNNResolvedChainSelection.model_validate(
        {
            "entity": _entity("entity-2", "B"),
            "pdb_chain_id": "A",
            "residues": [
                {
                    **_residue(2, instance="entity-2", chain="B"),
                    "label_seq_id": 2,
                    "wt": "G",
                    "pdb_chain_id": "A",
                    "pdb_residue_id": 2,
                    "pdb_insertion_code": "",
                    "model_position": 1,
                    "residue_name": "GLY",
                }
            ],
        }
    )
    with pytest.raises((ValidationError, ValueError), match="chain"):
        _effective(chains=(chain_a, other_entity_same_normalized_chain))


def test_effective_settings_reject_unknown_fields_and_internal_hash_tampering() -> None:
    settings = _settings_module()
    effective = _effective()
    payload = effective.model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        settings.FrustraMPNNEffectiveSettings.model_validate(payload)

    tampered = effective.model_dump(mode="json")
    tampered["resolved_chains"][0]["residues"][0]["wt"] = "W"
    with pytest.raises(ValidationError, match="effective settings SHA-256"):
        settings.FrustraMPNNEffectiveSettings.model_validate(tampered)

    with pytest.raises(ValidationError, match="frozen"):
        effective.resolved_chains[0].pdb_chain_id = "Z"
