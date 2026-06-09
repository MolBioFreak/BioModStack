from __future__ import annotations

import sys
import types
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parent

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

sys.modules.setdefault("pyrosetta", types.SimpleNamespace())

from score_maturation import build_rosetta_interface_payload, reconcile_chain_groups_with_selected_positions


def test_reconcile_chain_groups_prefers_selected_position_chain_when_split_is_wrong() -> None:
    antibody, antigen = reconcile_chain_groups_with_selected_positions(
        chains=["A", "B"],
        antibody=["B"],
        antigen=["A"],
        selected_positions={("A", 27), ("A", 56), ("A", 105)},
        fallback_ab_count=1,
    )

    assert antibody == ["A"]
    assert antigen == ["B"]


def test_reconcile_chain_groups_preserves_existing_antibody_assignment_when_selected_chain_already_matches() -> None:
    antibody, antigen = reconcile_chain_groups_with_selected_positions(
        chains=["A", "B"],
        antibody=["A", "B"],
        antigen=[],
        selected_positions={("A", 27), ("A", 56), ("A", 105)},
        fallback_ab_count=2,
    )

    assert antibody == ["A", "B"]
    assert antigen == []


def test_rosetta_interface_payload_records_raw_reu_sign_convention() -> None:
    data = types.SimpleNamespace(
        dG={1: -42.5},
        dSASA={1: 1337.0},
        packstat=0.61,
        sc_value=0.73,
        interface_hbonds=9,
    )

    payload = build_rosetta_interface_payload("HL_A", data)

    assert payload["rosetta_interface_score"] == -42.5
    assert payload["rosetta_interface_dg"] == -42.5
    assert payload["rosetta_interface_dsasa"] == 1337.0
    assert payload["rosetta_interface_score_unit"] == "REU"
    assert payload["rosetta_interface_score_direction"] == "more_negative_is_better"
    assert payload["rosetta_interface_analyzer_used"] is True
    assert payload["rosetta_interface_id"] == "HL_A"
