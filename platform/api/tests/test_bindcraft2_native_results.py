"""Hand-authored parser fixtures, NOT evidence of a real BC2 campaign.

Shapes/columns from PacesaLab/BindCraft2 d5bae16: docs/outputs.md,
bindcraft/campaign_output.py:140-188,255-272,456-475,
bindcraft/campaign.py:169-222 and bindcraft/MPNN_stage.py:255-287.
"""

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from services.bindcraft2_candidate_projection import project_native_candidates
from services.bindcraft2_native_results import NativeResultError, native_result_page, read_native_publication


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


def producer_attempt(root, design="t"):
    payload = {"schema_version": 1, "design": design, "trajectory": 7, "recipe_hash": "recipe",
               "effective_settings": {"mpnn_temperature": 0.3, "filter_settings": {"i_pTM": 0.7}},
               "drawn": {"binder_length": 72}}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    path = root / "1_Trajectories/!_BMS_Attempts" / f"{design}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical + b"\n")
    return hashlib.sha256(canonical).hexdigest()


def stamped_campaign(root, *, sidecar=True, stamp=True, states=False):
    digest = producer_attempt(root) if sidecar else "a" * 64
    table(root / "1_Trajectories/!_Trajectories.csv", [
        {"design": "t", "hash": "recipe", "trajectory": "7", "bms_attempt_sha256": digest}])
    table(root / "2_Refolded/!_Refolded.csv", [
        {"design": f"t_candidate{i}", "hash": "recipe", "bms_trajectory_design": "t",
         "bms_attempt_sha256": digest, "outcome": "rejected" if i == 3 else "passed"}
        for i in (1, 2, 3)])
    rows = [{"design": f"t_seq{i}", "hash": "recipe", "rank": str(2 - i)} for i in (0, 1)]
    if stamp:
        for row, candidate in zip(rows, (2, 1)):
            row.update(bms_scored_candidate=str(candidate), bms_scored_design=f"t_candidate{candidate}",
                       bms_trajectory_design="t", bms_attempt_sha256=digest)
    table(root / "3_Ranked/!_Ranked.csv", rows)
    for design, candidate in (("t_seq0", 2), ("t_seq1", 1)):
        for state in ("targetA", "targetB"):
            metadata = (f"_bindcraft.design {design}\n_bindcraft.bms_scored_candidate {candidate}\n"
                        f"_bindcraft.bms_scored_design t_candidate{candidate}\n"
                        f"_bindcraft.bms_trajectory_design t\n"
                        f"_bindcraft.bms_attempt_sha256 {digest}\n") if stamp else ""
            if states and stamp:
                metadata += (f"_bindcraft.bms_target_state {state}\n"
                             "_bindcraft.bms_primary_target_state targetB\n"
                             "_bindcraft.bms_structure_variant native\n"
                             "_bindcraft.binder_chains B\n_bindcraft.target_chains A\n")
            (root / "3_Ranked" / f"{design}_{state}.cif").write_text("data_model\n" + metadata + "_atom_site.id 1\n")
    return digest


def test_explicit_sort_truncation_and_target_state_documents(tmp_path):
    digest = stamped_campaign(tmp_path)
    arm = read_native_publication(tmp_path).arms[0]
    assert [row.scored_design for row in arm.retained] == ["t_candidate2", "t_candidate1"]
    assert [row.rank for row in arm.retained] == [2, 1]
    assert arm.accounting["unresolved_retained_draw_joins"] == 0
    assert arm.attempts[0].effective_settings["filter_settings"]["i_pTM"] == 0.7
    assert arm.attempts[0].sha256 == arm.trajectories[0].attempt_sha256 == digest
    assert all(row.attempt_sha256 == digest for row in arm.retained)
    assert all(row.attempt_sha256 == digest for row in arm.draws)
    assert sorted(doc.retained_design for doc in arm.documents) == ["t_seq0"] * 2 + ["t_seq1"] * 2
    assert all(doc.attempt_sha256 == digest for doc in arm.documents)
    assert arm.accounting["retained_sequences"] == 2


@pytest.mark.parametrize("sidecar,stamp", [(False, True), (True, False), (False, False)])
def test_missing_hook_parts_never_qualify_structure(tmp_path, sidecar, stamp):
    stamped_campaign(tmp_path, sidecar=sidecar, stamp=stamp)
    arm = read_native_publication(tmp_path).arms[0]
    assert all(doc.retained_design is None and doc.attempt_sha256 is None for doc in arm.documents)
    if not sidecar:
        assert all(row.attempt_sha256 is None for row in arm.retained)
    if not stamp:
        assert all(row.scored_design is None for row in arm.retained)
        assert arm.accounting["unresolved_retained_draw_joins"] == 2


@pytest.mark.parametrize("change,expected", [
    ("digest", "attempt"), ("trajectory", "attempt"), ("hash", "attempt"),
    ("missing_draw", "scored/retained"), ("rejected_draw", "scored/retained"),
    ("duplicate_join", "scored/retained"), ("retained_digest", "attempt"),
    ("cif_candidate", "CIF"), ("cif_digest", "CIF"), ("cif_design", "CIF"),
])
def test_producer_contradictions_refused(tmp_path, change, expected):
    digest = stamped_campaign(tmp_path)
    trajectory = tmp_path / "1_Trajectories/!_Trajectories.csv"
    ranked = tmp_path / "3_Ranked/!_Ranked.csv"
    draws = tmp_path / "2_Refolded/!_Refolded.csv"
    if change in ("digest", "trajectory", "hash"):
        rows = list(csv.DictReader(trajectory.open()))
        rows[0][{"digest": "bms_attempt_sha256", "trajectory": "trajectory", "hash": "hash"}[change]] = "b" * 64 if change == "digest" else "bad"
        table(trajectory, rows)
    elif change in ("missing_draw", "duplicate_join", "retained_digest"):
        rows = list(csv.DictReader(ranked.open()))
        if change == "missing_draw": rows[0]["bms_scored_candidate"] = "4"
        elif change == "duplicate_join": rows[1]["bms_scored_candidate"] = "2"
        else: rows[0]["bms_attempt_sha256"] = "b" * 64
        table(ranked, rows)
    elif change == "rejected_draw":
        rows = list(csv.DictReader(draws.open()))
        rows[1]["outcome"] = "rejected"
        table(draws, rows)
    else:
        path = tmp_path / "3_Ranked/t_seq0_targetA.cif"
        old = {"cif_candidate": "_bindcraft.bms_scored_candidate 2",
               "cif_digest": f"_bindcraft.bms_attempt_sha256 {digest}",
               "cif_design": "_bindcraft.design t_seq0"}[change]
        path.write_text(path.read_text().replace(old, old.split()[0] + " wrong"))
    with pytest.raises(NativeResultError, match=expected):
        read_native_publication(tmp_path)


def test_interrupted_sidecar_not_claimed_or_emitted(tmp_path):
    producer_attempt(tmp_path, design="interrupted")
    arm = read_native_publication(tmp_path).arms[0]
    assert len(arm.attempts) == 1
    assert arm.accounting["claimed_attempts"] is None
    assert arm.accounting["emitted_trajectories"] == 0


def test_sidecar_canonical_bytes_and_unsafe_link_refused(tmp_path):
    producer_attempt(tmp_path)
    path = tmp_path / "1_Trajectories/!_BMS_Attempts/t.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(NativeResultError, match="noncanonical"):
        read_native_publication(tmp_path)
    path.unlink()
    path.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(NativeResultError, match="unsafe"):
        read_native_publication(tmp_path)


def test_sidecar_present_but_unstamped_trajectory_is_unqualified(tmp_path):
    stamped_campaign(tmp_path)
    path = tmp_path / "1_Trajectories/!_Trajectories.csv"
    rows = list(csv.DictReader(path.open()))
    rows[0]["bms_attempt_sha256"] = ""
    table(path, rows)
    arm = read_native_publication(tmp_path).arms[0]
    assert arm.attempts and arm.trajectories[0].attempt_sha256 is None
    assert all(row.attempt_sha256 is None for row in arm.draws + arm.retained)
    assert all(document.retained_design is None for document in arm.documents)


def test_bounded_readback_retains_native_metrics_settings_and_unknown_state(tmp_path):
    digest = stamped_campaign(tmp_path)
    publication = read_native_publication(tmp_path)
    draws = native_result_page(publication, stage="draw", offset=1, limit=1)
    assert draws["total"] == 3 and len(draws["rows"]) == 1
    assert draws["rows"][0]["design"] == "t_candidate2"
    assert draws["rows"][0]["attempt_sha256"] == digest
    retained = native_result_page(publication, stage="retained", limit=2)
    assert [row["scored_design"] for row in retained["rows"]] == ["t_candidate2", "t_candidate1"]
    assert [row["rank"] for row in retained["rows"]] == [2, 1]
    documents = native_result_page(publication, stage="document", limit=10)
    assert documents["total"] == 4
    assert all(row["target_state"] is None for row in documents["rows"])
    assert native_result_page(publication, stage="attempt")["rows"][0]["effective_settings"]["mpnn_temperature"] == 0.3
    assert native_result_page(publication, stage="trajectory")["accounting"]["claimed_attempts"] is None
    bad_pages: list[dict[str, Any]] = [{"limit": 101}, {"limit": 0}, {"offset": -1},
                                       {"limit": True}, {"stage": "fake"}, {"arm": "wrong"}]
    for kwargs in bad_pages:
        with pytest.raises(NativeResultError):
            native_result_page(publication, **kwargs)


def test_stamped_state_identity_projects_exact_primary_without_ordinal_join(tmp_path):
    digest = stamped_campaign(tmp_path, states=True)
    publication = read_native_publication(tmp_path)
    candidates = project_native_candidates(publication)
    assert [(candidate.retained_design, candidate.scored_design, candidate.native_rank) for candidate in candidates] == [
        ("t_seq0", "t_candidate2", 2), ("t_seq1", "t_candidate1", 1)]
    assert all(candidate.attempt_sha256 == digest for candidate in candidates)
    assert all(candidate.primary_structure.target_state == "targetB" for candidate in candidates)
    assert all(candidate.primary_structure.path.endswith("_targetB.cif") for candidate in candidates)
    assert all({structure.target_state for structure in candidate.structures} == {"targetA", "targetB"}
               for candidate in candidates)
    assert all(candidate.primary_structure.binder_chains == "B" for candidate in candidates)
    assert publication.arms[0].draws[2].outcome == "rejected"
    page = native_result_page(publication, stage="document")
    assert {document["target_state"] for document in page["rows"]} == {"targetA", "targetB"}
    assert {document["primary_target_state"] for document in page["rows"]} == {"targetB"}


def test_unknown_state_and_ambiguous_primary_remain_native(tmp_path):
    stamped_campaign(tmp_path)
    assert project_native_candidates(read_native_publication(tmp_path)) == ()
    stamped_campaign(tmp_path, states=True)
    path = tmp_path / "3_Ranked/t_seq0_targetA.cif"
    path.write_text(path.read_text().replace("bms_target_state targetA", "bms_target_state targetB"))
    candidates = project_native_candidates(read_native_publication(tmp_path))
    assert [candidate.retained_design for candidate in candidates] == ["t_seq1"]
    assert read_native_publication(tmp_path).arms[0].accounting["retained_sequences"] == 2


def test_missing_explicit_join_is_observational_not_stem_inference(tmp_path):
    stamped_campaign(tmp_path, states=True)
    path = tmp_path / "3_Ranked/!_Ranked.csv"
    rows = list(csv.DictReader(path.open()))
    rows[0]["bms_scored_design"] = ""
    rows[0]["bms_trajectory_design"] = ""
    table(path, rows)
    arm = read_native_publication(tmp_path).arms[0]
    assert arm.retained[0].scored_design is None
    assert arm.retained[0].rank == 2
    assert arm.draws[2].outcome == "rejected"
    assert [candidate.retained_design for candidate in project_native_candidates(read_native_publication(tmp_path))] == ["t_seq1"]

def test_unstamped_draw_keeps_failed_and_retained_rows_without_projection(tmp_path):
    stamped_campaign(tmp_path, states=True)
    path = tmp_path / "2_Refolded/!_Refolded.csv"
    rows = list(csv.DictReader(path.open()))
    rows[1]["bms_trajectory_design"] = ""
    rows[1]["bms_attempt_sha256"] = ""
    table(path, rows)
    arm = read_native_publication(tmp_path).arms[0]
    assert arm.draws[1].outcome == "passed" and arm.draws[1].attempt_sha256 is None
    assert arm.draws[2].outcome == "rejected"
    assert arm.retained[0].scored_design is None and arm.retained[0].rank == 2
    assert [candidate.retained_design for candidate in project_native_candidates(read_native_publication(tmp_path))] == ["t_seq1"]


def test_relaxed_structure_is_derivative_not_primary(tmp_path):
    stamped_campaign(tmp_path, states=True)
    source = tmp_path / "3_Ranked/t_seq0_targetB.cif"
    relaxed = tmp_path / "2_Refolded/Relaxed/t_seq0_targetB.cif"
    relaxed.parent.mkdir(parents=True, exist_ok=True)
    relaxed.write_text(source.read_text().replace("bms_structure_variant native", "bms_structure_variant relaxed"))
    arm = read_native_publication(tmp_path).arms[0]
    assert any(doc.structure_variant == "relaxed" and doc.retained_design == "t_seq0" for doc in arm.documents)
    projected = project_native_candidates(read_native_publication(tmp_path))
    assert len(projected) == 2
    assert all(structure.variant == "native" for candidate in projected for structure in candidate.structures)

def test_malformed_producer_join_is_not_accepted_as_ordinal(tmp_path):
    stamped_campaign(tmp_path)
    path = tmp_path / "3_Ranked/!_Ranked.csv"
    for candidate in ("0", "-1", "1.0", "", "002"):
        rows = list(csv.DictReader(path.open()))
        rows[0]["bms_scored_candidate"] = candidate
        table(path, rows)
        with pytest.raises(NativeResultError):
            read_native_publication(tmp_path)
