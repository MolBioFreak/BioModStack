"""Conservative MAFFT tree mapping; unresolved leaves remain unchanged and reported."""
import re

_TOKEN = re.compile(r"\s+|'(?:[^']|'')*'|[^\s(),:;]+|[(),:;]")

def map_mafft_tree(tree: str, row_ids: list[str]) -> tuple[str, dict]:
    tokens = _TOKEN.findall(tree)
    expected = {f'{i}_{row_id}': row_id for i, row_id in enumerate(row_ids, 1)}
    used, unknown = [], []
    expect_leaf = True
    previous = ''
    for i, token in enumerate(tokens):
        if token.isspace(): continue
        if token in ('(', ','):
            expect_leaf = True
        elif token in (')', ':', ';'):
            expect_leaf = False
        elif expect_leaf:
            label = token[1:-1].replace("''", "'") if token.startswith("'") else token
            if label in expected:
                replacement = expected[label]
                tokens[i] = "'" + replacement.replace("'", "''") + "'"
                used.append(replacement)
            else:
                unknown.append(label)
            expect_leaf = False
        previous = token
    return ''.join(tokens), {'mapped': used, 'unmapped': unknown,
                             'missing': [r for r in row_ids if r not in used],
                             'duplicate_mapped': sorted({r for r in used if used.count(r) > 1})}
