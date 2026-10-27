"""Best-effort output observations; never determine native success."""
from collections import Counter
from .formats import parse


def observe_alignment(path, roster):
    try:
        output = parse(path.read_text(encoding='utf8'), 'fasta', roster.molecule)
        headers = [row.header for row in output.rows]
        counts = Counter(headers)
        inputs = {row.row_id: row for row in roster.rows}
        def residues(sequence):
            return ''.join(c for c in sequence if c not in '-.')
        changed = sorted({row.header for row in output.rows if row.header in inputs
                          and residues(row.sequence).upper() != residues(inputs[row.header].sequence).upper()})
        case_changed = sorted({row.header for row in output.rows if row.header in inputs
                               and residues(row.sequence) != residues(inputs[row.header].sequence)
                               and residues(row.sequence).upper() == residues(inputs[row.header].sequence).upper()})
        return {'output_headers': headers, 'missing_ids': sorted(inputs.keys() - counts.keys()),
                'unexpected_ids': sorted(counts.keys() - inputs.keys()),
                'duplicate_ids': sorted(k for k, v in counts.items() if v > 1),
                'changed_residue_ids': changed,
                'case_changed_ids': case_changed,
                'aligned_lengths': sorted({len(row.sequence) for row in output.rows})}
    except Exception as exc:
        return {'observation_error': f'{type(exc).__name__}: {exc}'}
