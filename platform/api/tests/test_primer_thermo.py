"""Primer thermodynamics: informational hairpin / self-dimer / pair-dimer delta-G."""
import pytest
from services.primer_thermo import (MAX_PAIRS, PrimerThermoError, PrimerThermoPair, PrimerThermoPrimer,
    PrimerThermoRequest, PrimerThermoSettings, analyze_primer_thermo, monovalent_equivalent_mM, severity_for)

Q = "GACAACCTGAAGTCTCTGACC"
GC = "GCGCGCGCGC"
HAIRPIN = "GCGCGCGCGCAAAAGCGCGCGCGC"


def run(seqs, pairs=(), **kw):
    return analyze_primer_thermo(PrimerThermoRequest(
        primers=[PrimerThermoPrimer(name=f"p{i}", sequence=s) for i, s in enumerate(seqs)],
        pairs=[PrimerThermoPair(left=a, right=b) for a, b in pairs],
        settings=PrimerThermoSettings(**kw)))


def test_thresholds_are_yellow_minus5_orange_minus10():
    assert [severity_for(x) for x in (1.9, 0.0, -4.99, -5.0, -9.99, -10.0, -18.8, None)] == \
        ["none", "none", "none", "yellow", "yellow", "orange", "orange", "none"]


def test_self_dimer_counts_only_intermolecular_pairs():
    # A strong hairpin primer must not report its own fold as a dimer.
    stem_loop = "GCGCGCAAAAGCGCGC"
    r = run([stem_loop])
    assert r.primers[0].hairpin.dg_kcal_mol < -3
    dimer = r.primers[0].self_dimer
    pairs = dimer.structure.replace("&", "")
    first = len(stem_loop)
    stack, cross_only = [], True
    for index, char in enumerate(pairs):
        if char == "(":
            stack.append(index)
        elif char == ")":
            start = stack.pop()
            cross_only &= start < first <= index
    assert cross_only


def test_pair_dimer_is_symmetric_in_the_two_primers():
    f, r = "GACAACCTGAAGTCTCTGACC", "GGTCAGAGACTTCAGGTTGTC"
    forward = run([f, r], pairs=[(0, 1)]).pairs[0].dg_kcal_mol
    assert forward < -15 and run([f, r], pairs=[(1, 0)]).pairs[0].dg_kcal_mol == forward


def test_salt_equivalent_matches_von_ahsen_form():
    assert monovalent_equivalent_mM(PrimerThermoSettings()) == pytest.approx(50 + 120 * 0.9 ** 0.5)
    assert monovalent_equivalent_mM(PrimerThermoSettings(mg_mM=0.5, dntps_mM=0.8, na_mM=100)) == 100


def test_strong_hairpin_and_self_dimer_flag_orange():
    r = run([HAIRPIN, GC], pairs=[(0, 1)])
    assert r.primers[0].hairpin.severity == "orange" and r.primers[0].hairpin.dg_kcal_mol <= -10
    assert r.primers[1].self_dimer.severity == "orange" and r.primers[1].self_dimer.three_prime_paired
    assert r.engine_version and r.citations and "University of Vienna" in r.credit


def test_ordinary_primer_stays_unflagged_and_never_positive():
    r = run([Q])
    assert r.primers[0].hairpin.severity == "none" and r.primers[0].hairpin.dg_kcal_mol <= 0


def test_pair_dimer_is_intermolecular_only():
    r = run(["AAAAAAAAAA", "TTTTTTTTTT"], pairs=[(0, 1)])
    assert r.pairs[0].dg_kcal_mol < 0 and r.pairs[0].three_prime_paired
    # a pair with nothing complementary reports no association
    r = run(["AAAAAAAAAAAA", "AAAAAAAAAAAA"], pairs=[(0, 1)])
    assert r.pairs[0].dg_kcal_mol == 0 and r.pairs[0].severity == "none"


def test_salt_changes_result_in_the_expected_direction():
    low = run([GC], na_mM=20, mg_mM=0, dntps_mM=0).primers[0].self_dimer.dg_kcal_mol
    high = run([GC], na_mM=1000, mg_mM=0, dntps_mM=0).primers[0].self_dimer.dg_kcal_mol
    assert high < low


def test_out_of_range_salt_is_reported_not_silent():
    r = run([GC], na_mM=1, mg_mM=0, dntps_mM=0)
    assert r.salt_note and r.monovalent_equivalent_mM == 10.0


def test_bad_input_is_reported_per_primer_and_does_not_block_others():
    r = run(["ACGTNNACGT", Q])
    assert r.primers[0].hairpin is None and "not analyzed" in r.primers[0].warnings[0]
    assert r.primers[1].hairpin is not None


def test_too_many_pairs_rejected():
    with pytest.raises(PrimerThermoError):
        run([Q, Q], pairs=[(0, 1)] * (MAX_PAIRS + 1))


def test_pair_index_out_of_range_rejected():
    with pytest.raises(PrimerThermoError):
        run([Q], pairs=[(0, 3)])


def test_viennarna_rna_parameters_are_not_disturbed():
    import RNA
    before = RNA.fold_compound("GGGAAACCC").mfe()[1]
    run([HAIRPIN])
    assert RNA.fold_compound("GGGAAACCC").mfe()[1] == before
