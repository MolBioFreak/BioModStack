"""Output observations and integrity checks, separate from native exit status."""
from collections import Counter
from .formats import parse


def reverse_complement_residues(sequence, molecule):
    """Return an exact IUPAC reverse complement, or None for an unsupported alphabet."""
    sequence = ''.join(c for c in sequence if c not in '-.').upper()
    alphabet = 'ACGTRYMKBDHVSWN' if molecule == 'dna' else 'ACGURYMKBDHVSWN' if molecule == 'rna' else ''
    if not alphabet or not set(sequence) <= set(alphabet):
        return None
    complement = str.maketrans('ACGTRYMKBDHVSWNU', 'TGCAYRKMVHDBSWNA')
    result = sequence.translate(complement)[::-1]
    return result.replace('T', 'U') if molecule == 'rna' else result


def observe_alignment(path, roster, *, allow_reverse_complement=False):
    try:
        return observe_alignment_text(path.read_text(encoding='utf8'), roster,
                                      allow_reverse_complement=allow_reverse_complement)
    except Exception as exc:
        return {'observation_error': f'{type(exc).__name__}: {exc}'}


def observe_alignment_text(text, roster, *, allow_reverse_complement=False):
    try:
        output = parse(text, 'fasta', roster.molecule)
        headers = [row.header for row in output.rows]
        inputs = {row.row_id: row for row in roster.rows}
        def residues(sequence):
            return ''.join(c for c in sequence if c not in '-.')
        reversed_ids = set()
        mapped_rows = []
        for row in output.rows:
            ident = row.header
            expected = residues(inputs[ident].sequence) if ident in inputs else None
            if (allow_reverse_complement and roster.molecule in ('dna', 'rna')
                    and ident not in inputs and ident.startswith('_R_') and ident[3:] in inputs):
                candidate = ident[3:]
                candidate_expected = reverse_complement_residues(inputs[candidate].sequence, roster.molecule)
                # The marker alone is not evidence of a valid source mapping.
                if candidate_expected is not None and residues(row.sequence).upper() == candidate_expected:
                    ident, expected = candidate, candidate_expected
                    reversed_ids.add(candidate)
            mapped_rows.append((ident, row, expected))
        counts = Counter(ident for ident, _, _ in mapped_rows)
        changed = sorted({ident for ident, row, expected in mapped_rows if expected is not None
                          and residues(row.sequence).upper() != expected.upper()})
        deleted = {}
        residue_counts = []
        for ident, row, expected in mapped_rows:
            if expected is None:
                continue
            actual = residues(row.sequence).upper()
            original = expected.upper()
            residue_counts.append({'row_id': ident, 'input_residues': len(original),
                'output_residues': len(actual),
                'output_fraction_of_input': len(actual) / len(original) if original else None})
            if len(actual) < len(original):
                source = iter(original)
                if all(symbol in source for symbol in actual):
                    deleted[ident] = len(original) - len(actual)
        case_changed = sorted({ident for ident, row, expected in mapped_rows if expected is not None
                               and residues(row.sequence) != expected
                               and residues(row.sequence).upper() == expected.upper()})
        return {'output_headers': headers, 'missing_ids': sorted(inputs.keys() - counts.keys()),
                'unexpected_ids': sorted(counts.keys() - inputs.keys()),
                'duplicate_ids': sorted(k for k, v in counts.items() if v > 1),
                'changed_residue_ids': changed, 'subsequence_deletion_ids': sorted(deleted),
                'removed_residue_counts': deleted, 'row_residue_counts': residue_counts,
                'reverse_complemented_ids': sorted(reversed_ids),
                'case_changed_ids': case_changed,
                'aligned_lengths': sorted({len(row.sequence) for row in output.rows})}
    except Exception as exc:
        return {'observation_error': f'{type(exc).__name__}: {exc}'}


def validate_alignment_output(request, observations):
    """Validate alignment integrity separately from the native process exit code.

    Native filtering/subsampling can omit rows only when explicitly requested.
    Explicit FAMSA column trimming may delete residues only as a verified
    subsequence. No setting authorizes substitutions, inserted residues, duplicate
    rows, unknown rows, or a ragged/empty alignment.
    """
    settings = request.settings
    members = observations if isinstance(observations, list) else [observations]
    if (settings.engine == 'muscle' and settings.guidetreeout
            and not settings.randomchaintree and not any(item.get('output_headers') for item in members if item)):
        return {'status': 'not_alignment', 'errors': [], 'reason': 'Guide tree only was requested'}
    allow_missing = ((settings.engine == 'mafft' and settings.sparsepickup is not None)
        or (settings.engine == 'twilight' and (settings.filter
            or any(getattr(settings, key) is not None for key in ('min_len', 'max_len', 'max_ambig', 'length_deviation')))))
    allow_deletion = settings.engine == 'famsa' and settings.trim_columns is not None and settings.trim_columns > 0
    trimmed = set()
    errors = []
    if not members:
        errors.append('No alignment artifact was produced')
    for index, item in enumerate(members):
        prefix = f'Alignment {index + 1}: '
        if not item or item.get('observation_error'):
            errors.append(prefix + 'output could not be parsed')
            continue
        if not item.get('output_headers'):
            errors.append(prefix + 'no sequence rows were produced')
        if len(item.get('aligned_lengths', [])) != 1:
            errors.append(prefix + 'rows do not share one nonempty alignment width')
        elif item['aligned_lengths'][0] <= 0:
            errors.append(prefix + 'alignment has zero columns')
        for key, label in (('unexpected_ids', 'unknown sequence rows'), ('duplicate_ids', 'duplicate sequence rows'),
                           ('changed_residue_ids', 'changed sequence residues')):
            affected = set(item.get(key, []))
            if key == 'changed_residue_ids' and allow_deletion:
                permitted = affected.intersection(item.get('subsequence_deletion_ids', []))
                trimmed.update(permitted)
                affected -= permitted
            if affected:
                errors.append(prefix + label + ': ' + ', '.join(sorted(affected)))
        if item.get('missing_ids') and not allow_missing:
            errors.append(prefix + 'unrequested missing sequence rows: ' + ', '.join(item['missing_ids']))
    return {'status': 'invalid' if errors else 'valid', 'errors': errors,
            'row_subsetting_requested': bool(allow_missing),
            'residue_deletion_requested': bool(allow_deletion), 'trimmed_row_ids': sorted(trimmed)}
