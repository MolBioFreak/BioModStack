"""Independent coordinate oracles for advisory primer QC (not thermodynamics)."""
from dataclasses import asdict
import itertools
import random

import pytest

from services import molbio_ops as ops
from services import primer_qc as qc

_COMPLEMENT = dict(zip("ACGTURYSWKMBDHVN", "TGCAAYRSWMKVHDBN"))
_BASES = dict(zip("ACGTURYSWKMBDHVN", map(set, [
    "A", "C", "G", "T", "T", "AG", "CT", "CG", "AT", "GT", "AC",
    "CGT", "AGT", "ACT", "ACG", "ACGT",
])))


def _rc(sequence):
    return "".join(_COMPLEMENT[base] for base in reversed(sequence))


def _paired_run(left, right, terminal=False):
    left, right = left.replace("U", "T"), right.replace("U", "T")
    best = 0
    # Physical 5'->3' indices on opposite strands sum to a constant.
    for diagonal in range(len(left) + len(right) - 1):
        pairs = [(i, diagonal - i) for i in range(len(left))
                 if 0 <= diagonal - i < len(right)]
        run = []
        for i, j in pairs + [(None, None)]:
            if i is not None and left[i] == _COMPLEMENT[right[j]]:
                run.append((i, j))
            else:
                if not terminal or (any(x == len(left) - 1 for x, y in run)
                                    and any(y == len(right) - 1 for x, y in run)):
                    best = max(best, len(run))
                run = []
    return best


def _site_oracle(template, primer, reverse, circular, minimum):
    # Brute-force every suffix at every start; collapse by physical 3' anchor.
    sites = {}
    n = len(template)
    for length in range(max(1, min(len(primer), minimum)), min(len(primer), n) + 1):
        pattern = _rc(primer[-length:]) if reverse else primer[-length:]
        for start in range(n if circular else n - length + 1):
            if all(_BASES[template[(start + i) % n]] & _BASES[base]
                   for i, base in enumerate(pattern)):
                terminal = start if reverse else (start + length - 1) % n
                sites[terminal] = (start, start + length, length, len(primer) - length)
    return sorted(sites.values())


@pytest.mark.parametrize("reverse,circular", list(itertools.product([False, True], repeat=2)))
def test_all_site_coordinate_oracle(reverse, circular):
    rng = random.Random(91)
    cases = [("AAAA", "AAAAAA", 1), ("ACGT", "ACGTAC", 2),
             ("AAAAA", "AAA", 1), ("GATCCG", "CCGGAT", 2),
             ("AUNGC", "AUGC", 2), ("NNNN", "RYN", 1), ("AC", "AAAA", 3)]
    for alphabet in ["ACGT", "ACGTNRYSWKMBDHV", "ACGU"]:
        for _ in range(250):
            n, m = rng.randint(1, 25), rng.randint(1, 30)
            cases.append(("".join(rng.choices(alphabet, k=n)),
                          "".join(rng.choices(alphabet, k=m)), rng.randint(0, m + 2)))
    for template, primer, minimum in cases:
        sites = qc._primer_binding_sites(template, primer, reverse=reverse,
            circular=circular, sequence_type="rna" if "U" in primer else "dna",
            min_anneal_length=minimum)
        actual = sorted((s.start, s.end, s.anneal_length, s.overhang_length) for s in sites)
        assert actual == _site_oracle(template, primer, reverse, circular, minimum), (template, primer, minimum)


@pytest.mark.parametrize("alphabet", ["ACGT", "ACGU"])
def test_antiparallel_coordinate_oracle(alphabet):
    rng = random.Random(803)
    cases = [("", "A"), ("AAAA", "AAAA"), ("GGATCC", "GGATCC"),
             ("CCAGTA", "TACTGG"), ("CGCGCGAAAAGG", "GCGCGCTTTTCC")]
    for _ in range(1000):
        left = "".join(rng.choices(alphabet, k=rng.randint(1, 24)))
        right = "".join(rng.choices(alphabet, k=rng.randint(1, 24)))
        cases.extend([(left, right), (left, left)])
    for left, right in cases:
        assert qc._longest_contiguous_complement(left, right) == _paired_run(left, right)
        assert qc._three_prime_dimer_length(left, right) == _paired_run(left, right, True)


def test_candidate_antiparallel_regressions_and_warnings():
    assert qc.evaluate_primer_qc("ATGCATGCAAGAATTC").three_prime_self_complement == 6
    assert qc.evaluate_primer_qc("TTTTTTTTGGATCC").three_prime_self_complement == 6
    for left, right, expected in [("TTTTTTTTGGATCC", "AAAAAAAAGGATCC", 6),
                                  ("CCAGTA", "TACTGG", 6),
                                  ("CGCGCGAAAAGG", "GCGCGCTTTTCC", 2),
                                  ("AAAAAAAAAAAACCCC", "GGGGTTTTTTTTTTTT", 16)]:
        metrics = qc.evaluate_primer_pair_qc(left, right)
        assert metrics.three_prime_heterodimer == expected
        assert any("3' heterodimer" in warning for warning in metrics.warnings) == (expected >= 4)


@pytest.mark.parametrize("primer", ["AUGCAUGCAAGAAUUC", "UUUUUUUUGGAUCC", "ACGUACGU"])
def test_rna_equivalent_dna_preserves_input(primer):
    rna = asdict(qc.evaluate_primer_qc(primer, sequence_type="rna"))
    dna = asdict(qc.evaluate_primer_qc(primer.replace("U", "T")))
    assert rna["sequence"] == primer and rna["sequence_type"] == "rna"
    for key in rna.keys() - {"sequence", "sequence_type"}:
        assert rna[key] == dna[key]


@pytest.mark.parametrize("reverse,circular", list(itertools.product([False, True], repeat=2)))
def test_full_and_partial_sites_terminal_dedup(reverse, circular):
    primer = "ACGTTAGCTTAGGCTATGCG"
    template = "CCCCCC" + primer + "CCCCCCCCCC" + primer[-16:] + "CCCCCC"
    if reverse:
        template = _rc(template)
    if circular:
        # Rotate through the full site, forcing it to straddle the origin.
        template = template[15:] + template[:15]
    metrics = qc.evaluate_primer_qc(primer, template_sequence=template,
        circular_template=circular, min_binding_anneal_length=12)
    assert metrics.binding_site_count == 2
    assert metrics.off_target_site_count == 1
    assert sorted(s["anneal_length"] for s in metrics.binding_positions) == [16, 20]
    assert {s["strand"] for s in metrics.binding_positions} == {-1 if reverse else 1}
    assert len(ops.resolve_primer_binding_sites(template, primer, reverse, circular)) == 1
    if circular:
        assert any(s["end"] > len(template) for s in metrics.binding_positions)


def test_distinct_strands_and_linear_ends():
    metrics = qc.evaluate_primer_qc("GGATCC", template_sequence="GGATCC")
    assert metrics.binding_site_count == 2
    assert {s["strand"] for s in metrics.binding_positions} == {-1, 1}
    assert qc.evaluate_primer_qc("CCGG", template_sequence="GGCC").binding_site_count == 0
    assert qc.evaluate_primer_qc("CCGG", template_sequence="GGCC", circular_template=True).binding_site_count == 2


def test_dense_reverse_hits_complement_once(monkeypatch):
    calls = []
    original = qc.reverse_complement
    def tracked(sequence, *args, **kwargs):
        calls.append(sequence)
        return original(sequence, *args, **kwargs)
    monkeypatch.setattr(qc, "reverse_complement", tracked)
    sites = qc._primer_binding_sites("T" * 1000, "A" * 30, reverse=True,
        circular=False, sequence_type="dna", min_anneal_length=12)
    assert len(sites) == 989
    assert calls == ["A" * 30]


def test_design_receives_correct_metrics_same_weights_and_shortlist(monkeypatch):
    from routers import molbio_ops as api
    rng = random.Random(114)
    template = "".join(rng.choice("ACGT") for _ in range(420))
    request = api.PrimerDesignRequest(sequence=template, sequence_type="dna",
        target_start=130, target_end=270, flank_search_span=100,
        primer_min_length=12, primer_max_length=13, gc_min_percent=0,
        gc_max_percent=100, gc_clamp_min=0, max_poly_x=20, tm_max_delta_c=100,
        product_min_length=40, product_max_length=1000)
    before = request.model_dump()
    calls, cleans = [], []
    original, clean = api._evaluate_primer_qc_canonical, ops.clean_sequence
    def tracked(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    def tracked_clean(sequence):
        if sequence == template:
            cleans.append(sequence)
        return clean(sequence)
    monkeypatch.setattr(api, "_evaluate_primer_qc_canonical", tracked)
    monkeypatch.setattr(ops, "clean_sequence", tracked_clean)
    result = api.design_primer_pairs_for_request(request, "fixture")
    assert request.model_dump() == before
    assert result.pair_count > 0 and len(calls) == 96
    assert len(cleans) <= 1  # Never revalidate a whole template per shortlisted primer.
    for pair in result.pairs:
        f, r = pair.forward, pair.reverse
        assert f.sequence in calls and r.sequence in calls
        general, terminal = _paired_run(f.sequence, r.sequence), _paired_run(f.sequence, r.sequence, True)
        assert (pair.heterodimer_complement, pair.three_prime_heterodimer) == (general, terminal)
        expected = round(abs(f.tm - request.tm_target_c) + abs(r.tm - request.tm_target_c)
            + abs(f.tm - r.tm) * 2.5 + general * 1.8 + terminal * 2.8
            + max(f.off_target_site_count or 0, 0) * 1.2
            + max(r.off_target_site_count or 0, 0) * 1.2
            + abs(pair.product_length - 140) / 100, 3)
        assert pair.penalty == expected
    assert [p.penalty for p in result.pairs] == sorted(p.penalty for p in result.pairs)
