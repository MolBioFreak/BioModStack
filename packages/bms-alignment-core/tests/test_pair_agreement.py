from itertools import combinations

from bms_alignment import compare, parse


def roster(sequences):
    doc = parse(''.join(f'>r{i}\n{s}\n' for i, s in enumerate(sequences)), 'fasta', 'dna')
    for i, row in enumerate(doc.rows):
        row.source_member_id = f'm{i}'
        row.occurrence_id = '1'
        row.transformation_provenance = 'test mapping'
        n = iter(range(sum(c not in '-.' for c in row.sequence)))
        row.source_positions = [None if c in '-.' else next(n) for c in row.sequence]
    return doc


def oracle(sequences):
    # Independently enumerate row pairs and source-residue pairs.
    result = set()
    for i, j in combinations(range(len(sequences)), 2):
        def columns(s):
            k = 0
            out = {}
            for col, c in enumerate(s):
                if c not in '-.':
                    out[col] = k
                    k += 1
            return out
        a, b = columns(sequences[i]), columns(sequences[j])
        result.update((i, a[col], j, b[col]) for col in a.keys() & b.keys())
    return result


def test_matches_independent_pair_oracle():
    a = ['A-CG', 'AC-G', 'A--G']
    b = ['AC-G', 'A-CG', 'A-G-']
    expected_a, expected_b = oracle(a), oracle(b)
    got = compare(roster(a), roster(b))['agreement']
    assert got['left_aligned_pairs'] == len(expected_a)
    assert got['right_aligned_pairs'] == len(expected_b)
    assert got['shared_aligned_pairs'] == len(expected_a & expected_b)
    assert got['union_aligned_pairs'] == len(expected_a | expected_b)
    assert got['jaccard'] == len(expected_a & expected_b) / len(expected_a | expected_b)


def test_unknown_mapping_and_empty_denominator():
    a = roster(['A-', '-A'])
    b = roster(['A-', '-A'])
    b.rows[0].source_positions = None
    result = compare(a, b)['agreement']
    assert result['rows_with_unknown_mapping'] == 1
    assert result['right_unmapped_residues'] == 1
    assert result['union_aligned_pairs'] == 0 and result['jaccard'] is None


def test_ensemble_support_has_explicit_sample_denominators_and_unknowns():
    from bms_alignment import ensemble_support
    a = ['A-CG', 'AC-G', 'A--G']
    b = ['AC-G', 'A-CG', 'A-G-']
    reference, member = roster(a), roster(b)
    missing = roster(b)
    for row in missing.rows:
        row.source_positions = None
    got = ensemble_support(reference, [reference, member, missing])
    expected_a, expected_b = oracle(a), oracle(b)
    assert got['method'] == 'reference-pair-retention-pooled-over-members.v1'
    assert got['sample_count'] == 3 and got['samples_with_nonzero_denominator'] == 2
    assert [(s['numerator'], s['denominator']) for s in got['samples']] == [
        (len(expected_a), len(expected_a)), (len(expected_a & expected_b), len(expected_a)), (0, 0)]
    assert got['fraction'] == (len(expected_a) + len(expected_a & expected_b)) / (2 * len(expected_a))
    assert got['samples'][2]['fraction'] is None
    assert ensemble_support(reference, [])['fraction'] is None
