from __future__ import annotations

import copy

import pytest

from services.frustrampnn.configuration import global_configuration
from services.frustrampnn.guidance import build_guidance_plan


def test_guidance_accepts_sequence_span_and_mapped_structural_regions():
    landscape = {
        "configuration_id": "frustrampnn_global_v1",
        "configuration_sha256": global_configuration()["configuration_sha256"],
        "residues": [
            {
                "entity_instance_id": "pdb:A",
                "auth_asym_id": "A",
                "auth_seq_id": 1,
                "insertion_code": "",
                "sequence_index": 1,
                "wild_type": "A",
                "slots": [{"mutation_aa": "C", "score": -1.0, "class": "neutral", "scoreable": True}],
            },
            {
                "entity_instance_id": "pdb:A",
                "auth_asym_id": "A",
                "auth_seq_id": 2,
                "insertion_code": "",
                "sequence_index": 2,
                "wild_type": "G",
                "slots": [{"mutation_aa": "C", "score": -0.5, "class": "neutral", "scoreable": True}],
            },
        ],
    }
    objective = {"objective_type": "score_aggregate", "direction": "lower_is_better", "aggregation": "mean"}
    sequence_plan = build_guidance_plan(
        landscape=landscape,
        region={"region_type": "sequence_span", "start": 1, "end": 1, "auth_asym_id": "A"},
        objective=objective,
        constraints={},
        ranking={"mode": "lexicographic"},
        rationale="Sequence-defined target span",
        guidance_id="gdp-sequence-span",
    )
    assert sequence_plan["region"]["region_type"] == "sequence_span"
    assert sequence_plan["region"]["resolved_residues"] == [{"auth_asym_id": "A", "auth_seq_id": 1, "insertion_code": ""}]

    pocket_plan = build_guidance_plan(
        landscape=landscape,
        region={
            "region_type": "pocket",
            "mapping_method": "source_contact_set_v1",
            "source_artifact_sha256": "a" * 64,
            "residues": [{"entity_instance_id": "pdb:A", "auth_asym_id": "A", "auth_seq_id": 2}],
        },
        objective=objective,
        constraints={},
        ranking={"mode": "lexicographic"},
        rationale="Pocket contact-set target",
        guidance_id="gdp-pocket",
    )
    assert pocket_plan["region"]["region_type"] == "pocket"
    assert pocket_plan["region"]["mapping_method"] == "source_contact_set_v1"


def test_guidance_rejects_unmapped_structural_region_without_provenance():
    landscape = {
        "configuration_id": "frustrampnn_global_v1",
        "configuration_sha256": global_configuration()["configuration_sha256"],
        "residues": [],
    }
    with pytest.raises(Exception, match="mapping|provenance|residue"):
        build_guidance_plan(
            landscape=landscape,
            region={"region_type": "interface", "residues": []},
            objective={"objective_type": "score_aggregate", "direction": "lower_is_better"},
            constraints={},
            ranking={"mode": "lexicographic"},
            rationale="Missing source mapping",
            guidance_id="gdp-invalid-interface",
        )
