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
