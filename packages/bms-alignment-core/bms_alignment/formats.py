from collections import Counter
from .models import Row, SequenceSet


def parse(text: str, fmt: str, molecule: str) -> SequenceSet:
    records = []
    if fmt in ('fasta', 'a3m'):
        for line in text.splitlines():
            if line.startswith('>'):
                records.append([line[1:], ''])
            elif line.strip():
                if not records:
                    raise ValueError('sequence before FASTA header')
                records[-1][1] += ''.join(line.split())
    elif fmt in ('stockholm', 'clustal'):
        # Block formats use identifier keys, not display headers.
        by_id = {}
        for line in text.splitlines():
            if not line.strip() or line.startswith(('#', '//', 'CLUSTAL', 'MUSCLE')) or line[0].isspace():
                continue
            parts = line.split()
            if len(parts) >= 2:
                if parts[0] not in by_id:
                    by_id[parts[0]] = len(records)
                    records.append([parts[0], ''])
                records[by_id[parts[0]]][1] += parts[1]
    else:
        raise ValueError('unsupported format')
    return SequenceSet(molecule=molecule, source_format=fmt, source_text=text,
                       rows=[Row(row_id=f'row_{i:08d}', header=h, sequence=s) for i, (h, s) in enumerate(records, 1)])


def export(roster: SequenceSet, fmt: str, original: bool = False) -> tuple[str, list[str]]:
    roster = SequenceSet.model_validate(roster.model_dump())
    if original:
        if fmt != roster.source_format or roster.source_text is None:
            raise ValueError('original bytes unavailable for requested format')
        return roster.source_text, []
    if fmt == 'a3m':
        raise ValueError('A3M conversion unavailable; original may be retained')
    if roster.source_format == 'a3m':
        raise ValueError('A3M insertion projection is not implemented; retain original')
    warnings = []
    if roster.source_format == 'stockholm':
        warnings.append('Stockholm annotations are not represented in this conversion; retain original')
    if fmt == 'fasta':
        return ''.join(f'>{r.header}\n{r.sequence}\n' for r in roster.rows), warnings
    if fmt in ('stockholm', 'clustal'):
        warnings.append('Row IDs replace display headers in block-format output')
        lines = ['# STOCKHOLM 1.0' if fmt == 'stockholm' else 'CLUSTAL W', '']
        lines.extend(f'{r.row_id} {r.sequence}' for r in roster.rows)
        lines.append('//' if fmt == 'stockholm' else '')
        return '\n'.join(lines) + '\n', warnings
    raise ValueError('unsupported format')


def coordinates(row: Row) -> list[int | None]:
    """Map gapped columns to zero-based source positions; unknown stays unknown."""
    i = 0
    result = []
    for c in row.sequence:
        if c in '-.':
            result.append(None)
        else:
            result.append(None if row.source_offset is None else row.source_offset + i)
            i += 1
    return result


def summary(roster: SequenceSet) -> list[dict]:
    if roster.source_format == 'a3m':
        raise ValueError('A3M insertion-aware summary unavailable; use project_a3m')
    if not roster.rows:
        return []
    lengths = {len(r.sequence) for r in roster.rows}
    if len(lengths) != 1:
        raise ValueError('summary requires rectangular alignment')
    result = []
    for column in zip(*(r.sequence for r in roster.rows)):
        counts = Counter(c.upper() for c in column if c not in '-.')
        non_gap = sum(counts.values())
        consensus = min(counts, key=lambda x: (-counts[x], x)) if counts else None
        result.append({'counts': dict(counts), 'rows': len(column), 'non_gap': non_gap,
                       'occupancy': non_gap / len(column), 'consensus': consensus,
                       'conservation': counts[consensus] / non_gap if consensus else None,
                       'policy': 'each occurrence weighted once; gaps excluded from consensus; ambiguity is literal'})
    return result


def project_a3m(roster: SequenceSet) -> list[dict]:
    """Separate match columns from lowercase/dot insertion slots, without realignment.

    Slot zero precedes the first match; slot N follows match N. Original text
    remains authoritative. Insertion strings need not have equal lengths.
    """
    if roster.source_format != 'a3m':
        raise ValueError('projection requires A3M input')
    result = []
    for row in roster.rows:
        matches = []
        insertions = ['']
        for symbol in row.sequence:
            if symbol.islower() or symbol == '.':
                insertions[-1] += symbol
            else:
                matches.append(symbol)
                insertions.append('')
        result.append({'row_id': row.row_id, 'match_columns': ''.join(matches),
                       'insertions_after_match_count': insertions})
    return result


from .comparison import compare
