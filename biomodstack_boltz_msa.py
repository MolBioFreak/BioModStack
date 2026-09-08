"""File-only Boltz native MSA contract (CSV keys are opaque row groups).

Boltz2 runtime: boltz-community 7ebf1be087d4d61a02234c878402838bf3712d8b,
Boltz-CP parser: 15f9775bc2280cd8a9338feb4d29511c27beaf21,
src/boltz/data/parse/csv.py and data/types.py (signed int32 keys).
No taxonomy annotations are manufactured. CSV key N denotes paired row N
in the provider's ordered per-chain paired alignments; -1 denotes unpaired.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from biomodstack_msa_handoff import validate_a3m


def a3m_rows(data: bytes) -> list[str]:
    rows = []
    for line in data.decode().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('>'):
            rows.append('')
        else:
            rows[-1] += line
    return rows


def validate_boltz_msa(path: Path, sequence: str) -> bytes:
    """Accept native A3M or CSV, requiring an exact query and aligned width."""
    if path.suffix.lower() != '.csv':
        return validate_a3m(path, sequence)
    data = path.read_bytes()
    reader = csv.DictReader(io.StringIO(data.decode()))
    if sorted(reader.fieldnames or []) != ['key', 'sequence']:
        raise ValueError('Boltz CSV requires exactly key,sequence columns')
    rows = list(reader)
    if not rows or rows[0]['sequence'] != sequence:
        raise ValueError('Boltz CSV query identity mismatch')
    for row in rows:
        if None in row or row['key'] is None or row['sequence'] is None:
            raise ValueError('Invalid Boltz CSV row column count')
        key = row['key']
        if key and (not key.lstrip('-').isdigit() or not -1 <= int(key) <= 2147483647):
            raise ValueError('Boltz CSV pairing key must be -1 or a nonnegative int32')
        aligned = ''.join(c for c in row['sequence'] if not c.islower())
        alphabet = 'ARNDCQEGHILKMFPSTWYVXBZUO-'
        if (len(aligned) != len(sequence) or any(c not in alphabet for c in aligned)
                or any(c.upper() not in alphabet or not c.isascii() for c in row['sequence'])):
            raise ValueError('Invalid Boltz CSV alignment row')
    return data


def write_paired_csv(path: Path, unpaired: bytes, paired: bytes, *, deduplicates_paired: bool) -> None:
    """Emit query once, ordered paired hits, then unpaired hits.

    CP unconditionally deduplicates line.replace('-', '').upper(), including
    paired rows. Refuse that precise loss, rather than altering residues or
    silently assigning a different row to a pairing group.
    """
    urows, prows = a3m_rows(unpaired), a3m_rows(paired)
    visited = {prows[0].replace('-', '').upper()}
    for row in prows[1:]:
        identity = row.replace('-', '').upper()
        if deduplicates_paired and identity in visited:
            raise ValueError('Boltz-CP parse_csv deduplicates paired sequences (including query matches); cannot preserve these pairing groups')
        visited.add(identity)
    with path.open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['key', 'sequence'])
        writer.writerow([-1, urows[0]])
        writer.writerows((index, row) for index, row in enumerate(prows[1:], 1))
        writer.writerows((-1, row) for row in urows[1:])
    validate_boltz_msa(path, urows[0])
