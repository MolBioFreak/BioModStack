from collections import Counter
from .models import Row, SequenceSet


def parse(text: str | bytes, fmt: str, molecule: str) -> SequenceSet:
    # UTF-8 is reversible, including CRLF and BOM; no universal-newline decoding.
    if isinstance(text, bytes):
        text = text.decode('utf-8')
    records = []
    annotations = []
    observations = []
    if fmt in ('fasta', 'a3m'):
        for line in text.removeprefix('\ufeff').splitlines():
            if line.startswith('>'):
                records.append([line[1:], ''])
            elif line.strip():
                if not records:
                    raise ValueError('sequence before FASTA header')
                records[-1][1] += ''.join(line.split())
        if fmt == 'a3m':
            if any(not (c.isascii() and (c.isalpha() or c in '-.')) for _, seq in records for c in seq):
                observations.append('Nonstandard A3M symbols retained; insertion semantics may be unknown')
            widths = {sum(not (c.islower() or c == '.') for c in seq) for _, seq in records}
            if len(widths) > 1:
                observations.append('A3M rows have unequal match column counts')
    elif fmt in ('stockholm', 'clustal'):
        by_id = {}
        for line in text.removeprefix('\ufeff').splitlines():
            stripped = line.strip()
            if fmt == 'stockholm' and stripped.startswith('#='):
                parts = stripped.split(None, 3)
                kind = parts[0][2:]
                expected = 4 if kind in ('GS', 'GR') else 3
                if kind not in ('GF', 'GS', 'GC', 'GR') or len(parts) < expected:
                    annotations.append({'kind': 'unknown', 'raw': line})
                    observations.append('Unknown or malformed Stockholm annotation retained verbatim')
                elif expected == 4:
                    annotations.append({'kind': kind, 'sequence_id': parts[1], 'feature': parts[2], 'value': parts[3]})
                else:
                    # GF values can contain spaces; GC values are normally one token.
                    short = stripped.split(None, 2)
                    annotations.append({'kind': kind, 'feature': short[1], 'value': short[2]})
                continue
            if not stripped or stripped.startswith(('#', '//', 'CLUSTAL', 'MUSCLE')) or line[0].isspace():
                continue
            parts = line.split()
            if len(parts) >= 2:
                if parts[0] not in by_id:
                    by_id[parts[0]] = len(records)
                    records.append([parts[0], ''])
                records[by_id[parts[0]]][1] += parts[1]
            else:
                observations.append('Malformed block line retained in original: ' + line)
        if fmt == 'stockholm':
            if not text.removeprefix('\ufeff').lstrip().startswith('# STOCKHOLM 1.0'):
                observations.append('Missing Stockholm header')
            if not any(line.strip() == '//' for line in text.splitlines()):
                observations.append('Missing Stockholm terminator')
            for item in annotations:
                if item.get('sequence_id') and item['sequence_id'] not in by_id:
                    observations.append('Annotation references unknown sequence: ' + item['sequence_id'])
            widths = {len(seq) for _, seq in records}
            if len(widths) > 1:
                observations.append('Stockholm rows have unequal aligned lengths')
            if len(widths) == 1:
                width = next(iter(widths))
                tracks = {}
                for item in annotations:
                    if item['kind'] in ('GR', 'GC'):
                        key = (item['kind'], item.get('sequence_id'), item['feature'])
                        tracks[key] = tracks.get(key, '') + item['value']
                for key, value in tracks.items():
                    if len(value) != width:
                        observations.append('Stockholm annotation length differs from alignment: ' + str(key))
    else:
        raise ValueError('unsupported format')
    return SequenceSet(molecule=molecule, source_format=fmt, source_text=text,
                       stockholm_annotations=annotations, format_observations=observations,
                       rows=[Row(row_id=f'row_{i:08d}', header=h, sequence=s) for i, (h, s) in enumerate(records, 1)])


def export(roster: SequenceSet, fmt: str, original: bool = False) -> tuple[str, list[str]]:
    roster = SequenceSet.model_validate(roster.model_dump())
    if original:
        if fmt != roster.source_format or roster.source_text is None:
            raise ValueError('original bytes unavailable for requested format')
        return roster.source_text, []
    if roster.source_format == 'stockholm' and fmt == 'stockholm' and roster.source_text is not None:
        parsed = parse(roster.source_text, 'stockholm', roster.molecule)
        if ([(r.row_id, r.header, r.sequence) for r in parsed.rows] == [(r.row_id, r.header, r.sequence) for r in roster.rows]
                and parsed.stockholm_annotations == roster.stockholm_annotations):
            return roster.source_text, []
        raise ValueError('Stockholm annotated export after edits unavailable; original remains available')
    if fmt == 'a3m':
        if roster.source_format != 'a3m':
            raise ValueError('A3M conversion unavailable: insertion states are unknown')
        return ''.join(f'>{r.header}\n{r.sequence}\n' for r in roster.rows), []
    if roster.source_format == 'a3m':
        if any(not (c.isascii() and (c.isalpha() or c in '-.')) for r in roster.rows for c in r.sequence):
            raise ValueError('A3M conversion unavailable: nonstandard symbol semantics unknown')
        projected = project_a3m(roster)
        if any(any(slot for slot in p['insertions_after_match_count']) for p in projected):
            raise ValueError('A3M conversion unavailable: insertion alignment is unknown')
        if len({len(p['match_columns']) for p in projected}) > 1:
            raise ValueError('A3M conversion unavailable: unequal match column counts')
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


def export_bytes(roster: SequenceSet, fmt: str, original: bool = False) -> tuple[bytes, list[str]]:
    text, warnings = export(roster, fmt, original)
    return text.encode('utf-8'), warnings


def coordinates(row: Row) -> list[int | None]:
    """Return only explicitly mapped, unambiguous source positions.

    Offset alone does not establish orientation or contiguity. Malformed maps
    and duplicate source positions are observations, not admission failures.
    """
    positions = row.source_positions
    if not row.transformation_provenance or not row.transformation_provenance.strip() or positions is None or len(positions) != len(row.sequence):
        return [None] * len(row.sequence)
    counts = Counter(p for c, p in zip(row.sequence, positions) if c not in '-.' and p is not None)
    return [p if c not in '-.' and type(p) is int and p >= 0 and counts[p] == 1 else None
            for c, p in zip(row.sequence, positions)]


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
        mapping = []
        for raw_index, symbol in enumerate(row.sequence):
            if symbol.islower() or symbol == '.':
                mapping.append({'raw_index': raw_index, 'match_column': None, 'insertion_slot': len(matches), 'slot_offset': len(insertions[-1])})
                insertions[-1] += symbol
            else:
                mapping.append({'raw_index': raw_index, 'match_column': len(matches), 'insertion_slot': None, 'slot_offset': None})
                matches.append(symbol)
                insertions.append('')
        result.append({'row_id': row.row_id, 'match_columns': ''.join(matches),
                       'insertions_after_match_count': insertions, 'raw_mapping': mapping})
    return result


from .comparison import compare
