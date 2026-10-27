"""Pure projection of an already-authorized native alignment artifact.

The caller must obtain and verify all bytes through the owning Job or Project
receipt. This module does not resolve paths, authorize access or select an
ensemble member.
"""
from pydantic import BaseModel


class AlignmentRow(BaseModel):
    row_id: str
    label: str
    aligned: str


class AlignmentDocument(BaseModel):
    result_id: str
    rows: list[AlignmentRow]


def alignment_document(*, result_id: str, fasta: str, roster: list[dict],
                       row_map: dict[str, str] | None = None) -> AlignmentDocument:
    """Project native row order without inferring identity from labels or sequence."""
    known = {}
    for row in roster:
        ident, label = row.get('row_id'), row.get('header')
        if not isinstance(ident, str) or not ident or ident in known or not isinstance(label, str):
            raise ValueError('invalid roster identity')
        known[ident] = label
    records: list[tuple[str, str]] = []
    header = None
    sequence: list[str] = []
    for line in fasta.splitlines():
        if line.startswith('>'):
            if header is not None:
                records.append((header, ''.join(sequence)))
            header, sequence = line[1:], []
        elif line.strip():
            if header is None:
                raise ValueError('sequence before FASTA header')
            sequence.append(''.join(line.split()))
    if header is not None:
        records.append((header, ''.join(sequence)))
    if not records:
        raise ValueError('empty alignment')
    rows, seen = [], set()
    for native_id, aligned in records:
        ident = row_map.get(native_id) if row_map is not None else native_id
        if not isinstance(ident, str) or ident not in known or ident in seen or not aligned:
            raise ValueError('alignment row identity is missing or ambiguous')
        seen.add(ident)
        rows.append(AlignmentRow(row_id=ident, label=known[ident], aligned=aligned))
    if len({len(row.aligned) for row in rows}) != 1:
        raise ValueError('alignment is not rectangular')
    return AlignmentDocument(result_id=result_id, rows=rows)
