"""Explicit synthetic writer receipts for analyzer-only unit fixtures.

Native parser/writer behavior is tested separately; this is not a production
identity resolver and must never be imported by runtime source.
"""
import hashlib
import json
from pathlib import Path

from fampnn_native_binding import VERSION, SOURCE_SHA256, receipt_path
from analyse_fampnn_seq_probs import _source_identities


def synthetic_receipt(source, candidate, sample_bytes=None):
    try:
        mapping = _source_identities(source.read_bytes())
    except ValueError:
        mapping = {}
    receipt = dict(version=VERSION, source_files=SOURCE_SHA256['fampnn'],
        input_id=source.stem, candidate_id=candidate.stem,
        source_pdb_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        candidate_pdb_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
        sample_pkl_sha256=hashlib.sha256(sample_bytes).hexdigest() if sample_bytes else None,
        records=[dict(chain_index=c, residue_index=n, source=identity,
                      candidate=f'{chr(65+c)}:{n}:') for (c,n),identity in mapping.items()])
    receipt_path(candidate).write_text(json.dumps(receipt))
    return receipt
