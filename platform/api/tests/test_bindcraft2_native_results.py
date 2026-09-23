"""Hand-authored parser fixtures, NOT evidence of a real BC2 campaign.

Shapes/columns from PacesaLab/BindCraft2 d5bae16: docs/outputs.md,
bindcraft/campaign_output.py:140-188,255-272,456-475,
bindcraft/campaign.py:169-222 and bindcraft/MPNN_stage.py:255-287.
"""

import csv
import json
from pathlib import Path

import pytest

from services.bindcraft2_native_results import NativeResultError, read_native_publication


def table(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=tuple(dict.fromkeys(k for row in rows for k in row)))
        writer.writeheader()
        writer.writerows(rows)


def test_sorted_truncated_native_rows_do_not_imply_candidate_to_seq_membership(tmp_path):
    root = tmp_path
    (root / ".campaign_state.json").write_text(json.dumps({"trajectories": 3, "attempted": ["h1", "h2", "interrupted"]}))
    table(root / "1_Trajectories/!_Trajectories.csv", [
        {"design": "t1", "hash": "h1", "trajectory": "1", "terminated": ""},
        {"design": "t2", "hash": "h2", "trajectory": "2", "terminated": "design"},
    ])
    table(root / "2_Refolded/!_Refolded.csv", [
        {"design": "t1_candidate1", "hash": "h1", "outcome": "rejected", "failed_filters": "i_pTM", "Binder_Sequence": "AAA/CCC"},
        {"design": "t1_candidate2", "hash": "h1", "outcome": "passed", "failed_filters": "", "Binder_Sequence": "DDD/EEE"},
        {"design": "t1_candidate3", "hash": "h1", "outcome": "passed", "failed_filters": "", "Binder_Sequence": "FFF/GGG"},
    ])
    table(root / "3_Ranked/!_Ranked.csv", [
        {"design": "t1_seq0", "hash": "h1", "rank": "1", "Binder_Sequence": "FFF/GGG"},
    ])
    # Even an apparently matching sequence or ordinal is insufficient evidence.
    (root / "3_Ranked/t1_seq0_targetA.cif").write_bytes(b"data_test\n")
    (root / "3_Ranked/t1_seq0_targetB.cif").write_bytes(b"data_test2\n")
    result = read_native_publication(root).arms[0]
    assert result.accounting == {
        "claimed_attempts": 3, "emitted_trajectories": 2, "scored_draws": 3,
        "passing_draws": 2, "rejected_draws": 1, "retained_sequences": 1,
        "unresolved_retained_draw_joins": 1,
    }
    assert result.draws[1].trajectory_design == "t1"
    assert result.retained[0].trajectory_design == "t1"
    assert result.draws[0].stage == "draw"
    assert result.draws[0].outcome == "rejected"
    assert result.draws[0].failed_filters == ("i_pTM",)
    assert result.retained[0].rank == 1
    assert result.retained[0].sequence == "FFF/GGG"
    assert not hasattr(result.retained[0], "candidate_number")
    assert result.draws[0].values["Binder_Sequence"] == "AAA/CCC"
    assert len(result.documents) == 2  # documents never inflate sequence count
    assert all(document.sha256 and document.format == "cif" for document in result.documents)
    assert read_native_publication(root) == read_native_publication(root)  # sealed bytes stable


def test_multitarget_order_and_missingness_are_native_not_alphabetical(tmp_path):
    table(tmp_path / "2_Refolded/!_Refolded.csv", [{
        "design": "draw", "outcome": "rejected", "targets": "zeta;alpha;off",
        "target_weights": "2;1;-1", "i_pTM": "0.9;;0.1", "Timing": "start=1;worker=0",
        "failed_filters": "i_pTM_alpha,Interface_Residues_off", "Binder_Sequence": "AC/DE",
    }])
    row = read_native_publication(tmp_path).arms[0].draws[0]
    assert row.targets == (("zeta", 2.0), ("alpha", 1.0), ("off", -1.0))
    assert row.target_readings["i_pTM"] == {"zeta": "0.9", "alpha": None, "off": "0.1"}
    assert "Timing" not in row.target_readings
    assert row.values["failed_filters"] == "i_pTM_alpha,Interface_Residues_off"
    assert row.failed_filters == ("i_pTM_alpha", "Interface_Residues_off")


def test_zero_yield_trajectory_only_and_missing_claim_state_are_not_errors(tmp_path):
    (tmp_path / ".campaign_state.json").write_text('{"trajectories":2}')
    table(tmp_path / "1_Trajectories/!_Trajectories.csv", [{"design": "terminated", "terminated": "design", "hash": "h"}])
    arm = read_native_publication(tmp_path).arms[0]
    assert arm.accounting["claimed_attempts"] == 2
    assert arm.accounting["retained_sequences"] == 0
    assert arm.draws == arm.retained == arm.documents == ()
    (tmp_path / ".campaign_state.json").unlink()
    assert read_native_publication(tmp_path).arms[0].accounting["claimed_attempts"] is None


def test_sweep_arms_are_isolated_and_legacy_tables_supported(tmp_path):
    table(tmp_path / "sweep.csv", [{"arm": "arm_a", "rank": "2"}, {"arm": "arm_b", "rank": "1"}])
    table(tmp_path / "arm_a/trajectories.csv", [{"design": "same", "hash": "h"}])
    table(tmp_path / "arm_a/candidates.csv", [{"design": "same_candidate1", "hash": "h", "outcome": "passed"}])
    table(tmp_path / "arm_b/1_Trajectories/!_Trajectories.csv", [{"design": "same", "hash": "h"}])
    arms = read_native_publication(tmp_path).arms
    assert [arm.name for arm in arms] == ["arm_a", "arm_b"]
    assert arms[0].draws[0].trajectory_design == "same"
    assert arms[1].draws == ()
    assert all(arm.claimed_attempts is None for arm in arms)


@pytest.mark.parametrize("bad", ["../outside", "arm/subdir", "", ".."])
def test_untrusted_sweep_names_rejected(tmp_path, bad):
    table(tmp_path / "sweep.csv", [{"arm": bad}])
    with pytest.raises(NativeResultError):
        read_native_publication(tmp_path)


def test_ambiguous_recipe_hash_not_used_as_attempt_identity(tmp_path):
    table(tmp_path / "1_Trajectories/!_Trajectories.csv", [{"design": "one", "hash": "h"}, {"design": "two", "hash": "h"}])
    table(tmp_path / "2_Refolded/!_Refolded.csv", [{"design": "one_candidate1", "hash": "h", "outcome": "passed"}])
    assert read_native_publication(tmp_path).arms[0].draws[0].trajectory_design is None


def test_symlinked_structure_and_invalid_state_refused(tmp_path):
    (tmp_path / "3_Ranked").mkdir()
    external = tmp_path / "external.cif"
    external.write_text("data_external")
    (tmp_path / "3_Ranked/link.cif").symlink_to(external)
    with pytest.raises(NativeResultError, match="unsafe"):
        read_native_publication(tmp_path)
    (tmp_path / "3_Ranked/link.cif").unlink()
    (tmp_path / ".campaign_state.json").write_text('{"trajectories":-1}')
    with pytest.raises(NativeResultError, match="claimed"):
        read_native_publication(tmp_path)
