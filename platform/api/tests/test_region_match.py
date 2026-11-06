"""Region matcher: segments must reproduce the target exactly (independent oracle)."""
import random
import time

from services.assembly.region_match import Source, cover_target, reverse_complement


def dna(seed, n):
    rng = random.Random(seed)
    return "".join(rng.choice("ACGT") for _ in range(n))


def rebuild(cover, sources, target):
    by_id = {s.id: s for s in sources}
    rotated = target[cover.rotation:] + target[:cover.rotation]
    out = ""
    for p in cover.pieces:
        if not p.is_match:
            out += p.sequence
            continue
        a, b, wraps = p.region()
        seq = by_id[p.source].sequence
        run = seq[a:] + seq[:b] if wraps else seq[a:b]
        out += run if p.strand == 1 else reverse_complement(run)
    assert out == rotated
    return out


def test_forward_match_with_new_dna_either_side():
    src = Source("s", dna(1, 400))
    target = dna(2, 50) + src.sequence[100:250] + dna(3, 40)
    c = cover_target(target, [src])
    rebuild(c, [src], target)
    match = [p for p in c.pieces if p.is_match]
    assert len(match) == 1 and match[0].region() == (100, 250, False) and match[0].strand == 1


def test_reverse_match():
    src = Source("s", dna(4, 400))
    target = dna(5, 30) + reverse_complement(src.sequence[50:200]) + dna(6, 30)
    c = cover_target(target, [src])
    rebuild(c, [src], target)
    match = [p for p in c.pieces if p.is_match][0]
    assert match.strand == -1 and match.region() == (50, 200, False)


def test_origin_spanning_source():
    src = Source("s", dna(7, 300), circular=True)
    run = src.sequence[250:] + src.sequence[:60]
    target = dna(8, 20) + run + dna(9, 20)
    c = cover_target(target, [src])
    rebuild(c, [src], target)
    match = [p for p in c.pieces if p.is_match][0]
    assert match.region() == (250, 60, True)


def test_origin_spanning_reverse_source():
    src = Source("s", dna(10, 300), circular=True)
    run = src.sequence[270:] + src.sequence[:50]
    target = reverse_complement(run) + dna(11, 25)
    c = cover_target(target, [src])
    rebuild(c, [src], target)
    match = [p for p in c.pieces if p.is_match][0]
    assert match.strand == -1 and match.region() == (270, 50, True)


def test_repeat_in_source_still_reproduces_target():
    unit = dna(12, 60)
    src = Source("s", unit + dna(13, 40) + unit + dna(14, 40))
    target = dna(15, 10) + unit + dna(16, 10)
    rebuild(cover_target(target, [src]), [src], target)


def test_no_match_is_all_new_dna():
    src = Source("s", dna(17, 300))
    target = dna(18, 200)
    c = cover_target(target, [src])
    assert [p.is_match for p in c.pieces] == [False]
    rebuild(c, [src], target)


def test_match_below_minimum_is_new_dna():
    src = Source("s", dna(19, 300))
    flank = "A" if src.sequence[39] != "A" else "C"
    before = "A" if src.sequence[9] != "A" else "C"
    target = dna(20, 40) + before + src.sequence[10:39] + flank + dna(21, 40)  # exactly 29 nt shared
    c = cover_target(target, [src], min_match=30)
    assert not any(p.is_match for p in c.pieces)
    rebuild(c, [src], target)


def test_circular_target_origin_does_not_split_a_match():
    src = Source("s", dna(22, 500))
    target = src.sequence[300:400] + dna(23, 80) + src.sequence[100:300] + src.sequence[400:450]
    # rotate so the 100-bp match straddles the target origin
    rotated = target[50:] + target[:50]
    c = cover_target(rotated, [src], circular=True)
    rebuild(c, [src], rotated)
    assert sum(p.is_match for p in c.pieces) == 3  # three source runs; the one cut by the origin is not split


def test_twenty_kb_target_two_ten_kb_sources_is_fast():
    a, b = Source("a", dna(24, 10000), True), Source("b", dna(25, 10000))
    target = a.sequence[2000:9000] + dna(26, 300) + reverse_complement(b.sequence[500:8000]) + a.sequence[:5700]
    start = time.time()
    c = cover_target(target, [a, b], circular=True)
    assert time.time() - start < 10
    rebuild(c, [a, b], target)
