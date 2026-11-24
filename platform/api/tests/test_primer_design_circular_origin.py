"""A9c: circular primer design considers origin-spanning candidates (lane l6)."""
import random

from routers.molbio_ops import PrimerDesignRequest, design_primer_pairs_for_request


def _template():
    rng = random.Random(11)
    return "".join(rng.choice("ACGT") for _ in range(300))


def test_circular_design_returns_origin_spanning_pair_when_it_is_best():
    template = _template()
    # Target sits across the origin: bases 290..300 and 0..10 of the circle.
    rotated = template[150:] + template[:150]          # same circle, origin moved by 150
    target_start, target_end = 140, 160                 # in the rotated frame this is the old origin region
    request = dict(sequence=rotated, sequence_type="dna", is_circular=True,
                   primer_min_length=18, primer_max_length=24, product_min_length=60, product_max_length=200,
                   flank_search_span=40, gc_min_percent=0, gc_max_percent=100, tm_target_c=60, tm_max_delta_c=15,
                   gc_clamp_min=0, max_poly_x=6, max_pairs=8)
    # Control: the same circle but with the target at the origin of the linear frame.
    spanning = PrimerDesignRequest(**{**request, "sequence": template, "target_start": 0, "target_end": 10})
    result = design_primer_pairs_for_request(spanning, "circle")
    assert result.pairs, result.warnings
    assert not any("excludes origin" in w for w in result.warnings)
    best = result.pairs[0]
    n = len(template)
    # The best pair's amplicon crosses the origin: it ends before it starts.
    assert best.product_end < best.product_start
    assert best.product_length == n - best.product_start + best.product_end + best.forward.overhang_length + best.reverse.overhang_length
    doubled = template * 2
    assert doubled[best.forward.start:best.forward.end] == best.forward.anneal_sequence
    from services.molbio_ops import reverse_complement
    reverse_start = best.reverse.start
    assert reverse_complement(doubled[reverse_start:reverse_start + best.reverse.anneal_length]) == best.reverse.anneal_sequence


def test_forward_primer_itself_spans_the_origin():
    template = _template()
    n = len(template)
    # Target starts 3 bases after the origin and the search reaches only 20 bases
    # upstream: the one admissible 24-mer footprint (-17..7) crosses the origin.
    request = PrimerDesignRequest(sequence=template, sequence_type="dna", is_circular=True, target_start=3,
        target_end=40, primer_min_length=24, primer_max_length=24, product_min_length=60, product_max_length=200,
        flank_search_span=20, gc_min_percent=0, gc_max_percent=100, tm_target_c=60, tm_max_delta_c=40,
        gc_clamp_min=0, max_poly_x=6, max_pairs=4)
    pairs = design_primer_pairs_for_request(request, "circle").pairs
    assert pairs
    for pair in pairs:
        f = pair.forward
        assert f.end > n and f.start < n          # wrapped site: end folded past the length
        assert (template * 2)[f.start:f.end] == f.anneal_sequence
        assert pair.product_start == f.start


def test_linear_template_still_excludes_wrapping():
    template = _template()
    request = PrimerDesignRequest(sequence=template, sequence_type="dna", is_circular=False, target_start=0,
        target_end=10, primer_min_length=18, primer_max_length=24, product_min_length=60, product_max_length=200,
        flank_search_span=40, gc_min_percent=0, gc_max_percent=100, tm_target_c=60, tm_max_delta_c=15,
        gc_clamp_min=0, max_poly_x=6)
    for pair in design_primer_pairs_for_request(request, "linear").pairs:
        assert pair.forward.start >= 0 and pair.reverse.end <= len(template)
