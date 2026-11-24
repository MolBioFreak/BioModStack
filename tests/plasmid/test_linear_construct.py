"""A linear construct: no origin wrap, no rotation, read ends measured by how the library was made."""
import random
from pathlib import Path

import pytest

import verify_construct as verify
from build_construct_topology_evidence import derive_topology_evidence
from plasmid_evidence import library_prep_from_kits

ROOT = Path(__file__).resolve().parents[2]
SCREEN = dict(
    breakpoint_rows=[{"call_status": "no_split", "call_confidence": "low", "primary_position_mod_ref": "64", "boundary_window_bp": "10"}],
    secondary_rows=[{"aligned_dimer_reads": "20", "non_boundary_split_reads": "0"}],
)
READS = [
    "full\t0\tref\t1\t60\t1000M\t*\t0\t0\t*\t*",
    "partial\t0\tref\t301\t60\t400M\t*\t0\t0\t*\t*",
    "reverse\t16\tref\t3\t60\t995M\t*\t0\t0\t*\t*",
    "joined\t0\tref\t501\t60\t500M500S\t*\t0\t0\t*\t*",
    "joined\t2048\tref\t1\t60\t500S500M\t*\t0\t0\t*\t*",
]


@pytest.mark.parametrize("kits, prep", [
    (["sqk-rbk114-96"], "rapid"), (["SQK-RAD114"], "rapid"), (["SQK-NBD114-24"], "ligation"), (["SQK-LSK114"], "ligation"),
    ([], "unknown"), ([""], "unknown"), (["SQK-PCB114-24"], "unknown"), (["SQK-RBK114-96", "SQK-LSK114"], "unknown"),
])
def test_library_prep_comes_from_the_kit_named_in_the_raw_data(kits, prep):
    assert library_prep_from_kits(kits) == prep


def test_linear_reference_reports_read_ends_and_keeps_junction_reads():
    evidence = derive_topology_evidence(reference_length=1000, sam_rows=READS, edge_window_bp=20,
                                        expected_topology="linear", sequencing_kits=["sqk-nbd114-24"], **SCREEN)
    assert evidence["state"] == "present" and evidence["expected_topology"] == "linear"
    assert evidence["origin_spanning_reads"] == 1
    ends = evidence["linear_ends"]
    assert ends["library_prep"] == "ligation" and ends["sequencing_kits"] == ["sqk-nbd114-24"]
    assert (ends["aligned_reads"], ends["reads_spanning_both_ends"], ends["spanning_fraction"]) == (4, 2, 0.5)
    assert (ends["end_window_bp"], ends["mean_depth_first_window"], ends["mean_depth_last_window"]) == (50, 2.96, 2.94)
    bins = ends["read_end_histogram"]["bins"]
    assert ends["read_end_histogram"]["bin_width_bp"] == 10 and len(bins) == 100
    assert sum(item["read_starts"] for item in bins) == 4 and bins[0]["read_starts"] == 2 and bins[-1]["read_ends"] == 3


def test_circular_reference_evidence_is_unchanged():
    evidence = derive_topology_evidence(reference_length=1000, sam_rows=READS, edge_window_bp=20, **SCREEN)
    assert evidence["expected_topology"] == "circular" and "linear_ends" not in evidence
    assert evidence["evidence_basis"] == "primary_and_supplementary_alignment_edges_plus_dimer_screen"


def test_linear_sequences_are_not_rotated_to_fit():
    reference = "".join(random.Random(7).choices("ACGT", k=200))
    rotated = reference[50:] + reference[:50]
    assert verify.exact_equivalence(reference, rotated) is not None
    assert verify.exact_equivalence(reference, rotated, True) is None
    assert verify.exact_equivalence(reference, verify.reverse_complement(reference), True)["orientation"] == "reverse_complement"
    assert verify.best_alignment(reference, rotated)["edit_cost"] == 0
    # A consensus that lost bases at both ends is reported as two end deletions, not shifted round.
    trimmed = verify.best_alignment(reference, reference[12:-9], {"expected_topology": "linear"})
    assert trimmed["rotation_offset"] == 0 and trimmed["edit_cost"] == 21
    edits = [code for code in trimmed["opcodes"] if code[0] != "equal"]
    assert {code[0] for code in edits} == {"delete"} and sum(code[2] - code[1] for code in edits) == 21
    assert all(code[2] <= 24 or code[1] >= 180 for code in edits)
    short = verify.best_alignment("ACGTACGTAC", "GTACGTACGT"[::-1].translate(str.maketrans("ACGT", "TGCA")), {"expected_topology": "linear"})
    assert short["rotation_offset"] == 0 and short["canonicalization"] == "linear_global_minimum_edit_v1"


def test_the_run_hands_the_saved_topology_and_the_kits_to_both_steps():
    text = (ROOT / "modules/ngs/construct_verify.nf").read_text(encoding="utf-8")
    assert "val sequencing_kits" in text and "--sequencing-kits ${kitsArg}" in text
    assert text.count("${topologyArgs}") == 2
    assert "DoradoPreflight.out.manifest.map { sequencingKits(it) }" in (ROOT / "workflows/ngs/ont_plasmid_qc.nf").read_text(encoding="utf-8")
