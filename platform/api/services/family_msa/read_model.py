"""Pure projection of an already-authorized native alignment artifact.

The caller must obtain and verify all bytes through the owning Job or Project
receipt. This module does not resolve paths, authorize access or select an
ensemble member.
"""
from pydantic import BaseModel
from bms_alignment.formats import coordinates
from bms_alignment.models import Row


class AlignmentRow(BaseModel):
    row_id: str
    label: str
    aligned: str
    source_member_id: str | None = None
    occurrence_id: str | None = None
    source_positions: list[int | None] | None = None


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
        known[ident] = row
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
        source = known[ident]
        original = source.get('sequence')
        positions = source.get('source_positions')
        mapped = None
        provenance = source.get('transformation_provenance')
        if (isinstance(original, str) and isinstance(positions, list)
                and len(positions) == len(original) and isinstance(provenance, str) and provenance.strip()
                and ''.join(c for c in original if c not in '-.').upper() == ''.join(c for c in aligned if c not in '-.').upper()):
            # Do not allow malformed JSON or model coercion to invent positions.
            safe_positions = [p if type(p) is int and p >= 0 else None for p in positions]
            raw = Row.model_construct(row_id=ident, header=source['header'], sequence=original,
                                      source_positions=safe_positions, transformation_provenance=provenance)
            residues = iter(p for c, p in zip(original, coordinates(raw)) if c not in '-.')
            mapped = [None if c in '-.' else next(residues) for c in aligned]
        rows.append(AlignmentRow(row_id=ident, label=source['header'], aligned=aligned,
                                 source_member_id=source.get('source_member_id'), occurrence_id=source.get('occurrence_id'), source_positions=mapped))
    if len({len(row.aligned) for row in rows}) != 1:
        raise ValueError('alignment is not rectangular')
    return AlignmentDocument(result_id=result_id, rows=rows)
