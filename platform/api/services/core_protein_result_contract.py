"""Future-only candidate publication validation and producer adapters."""
from collections.abc import Mapping
from typing import Any


class CandidateIntegrityError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.reason = {'code': code, 'message': message}


def _ids(values: Any, label: str) -> set[str]:
    if not isinstance(values, (list, tuple)) or any(not isinstance(v, str) or not v.strip() for v in values):
        raise CandidateIntegrityError('invalid_candidate_ids', f'invalid {label}')
    if len(set(values)) != len(values):
        raise CandidateIntegrityError('duplicate_candidate_id', f'duplicate {label}')
    return set(values)


def validate_candidate_accounting(*, stage_id: str, requested_count: int | None,
                                  generated_ids: list[str], dispositions: list[dict],
                                  expected_publication_ids: list[str], persisted_ids: list[str]) -> dict:
    """Adapter interface, not a producer file schema. No row-count inference.

    Producer adapters supply exact IDs and one disposition per generated candidate.
    Callers must separately validate required artifacts before crediting persisted IDs.
    """
    if not isinstance(stage_id, str) or not stage_id.strip():
        raise CandidateIntegrityError('invalid_stage_id', 'stage identity is required')
    if requested_count is not None and (type(requested_count) is not int or requested_count < 0):
        raise CandidateIntegrityError('invalid_requested_count', 'requested count must be a nonnegative integer or unknown')
    generated = _ids(generated_ids, 'generated candidates')
    expected = _ids(expected_publication_ids, 'expected publication')
    persisted = _ids(persisted_ids, 'persisted candidates')
    if not isinstance(dispositions, list) or any(not isinstance(d, Mapping) for d in dispositions):
        raise CandidateIntegrityError('invalid_dispositions', 'dispositions must be records')
    accounted = _ids([d.get('candidate_id') for d in dispositions], 'dispositions')
    if accounted != generated:
        raise CandidateIntegrityError('candidate_disposition_mismatch', 'each generated candidate requires exactly one disposition')
    selected = set()
    for disposition in dispositions:
        state = disposition.get('disposition')
        if state == 'selected':
            selected.add(disposition['candidate_id'])
        elif state in {'rejected', 'failed', 'unevaluable'}:
            if not isinstance(disposition.get('reason_code'), str) or not disposition['reason_code'].strip():
                raise CandidateIntegrityError('missing_disposition_reason', 'nonselected candidate requires a typed reason')
            if state == 'rejected' and (not isinstance(disposition.get('criterion'), str) or not disposition['criterion'].strip()):
                raise CandidateIntegrityError('missing_rejection_criterion', 'rejection requires an explicit criterion')
        else:
            raise CandidateIntegrityError('invalid_disposition', 'unknown candidate disposition')
    if selected != expected or persisted != expected:
        raise CandidateIntegrityError('candidate_publication_mismatch', 'selected, expected and validated persisted identities must match exactly')
    return {'stage_id': stage_id, 'requested_count': requested_count, 'generated_count': len(generated),
            'selected_count': len(selected), 'published_count': len(persisted),
            'expected_publication_count': len(expected),
            'rejected_count': sum(d['disposition'] == 'rejected' for d in dispositions),
            'failed_count': sum(d['disposition'] == 'failed' for d in dispositions),
            'unevaluable_count': sum(d['disposition'] == 'unevaluable' for d in dispositions)}


def _artifact(root, raw):
    import hashlib
    from pathlib import Path
    if not isinstance(raw, str) or not raw:
        raise CandidateIntegrityError('candidate_artifact_missing', 'declared artifact path is required')
    path = Path(raw)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(root.resolve()):
        raise CandidateIntegrityError('foreign_candidate_artifact', 'artifact escapes producer root')
    if not path.is_file() or path.stat().st_size == 0:
        raise CandidateIntegrityError('candidate_artifact_missing', f'declared artifact is missing: {raw}')
    content = path.read_bytes()
    return {'path': str(path), 'sha256': hashlib.sha256(content).hexdigest()}, content


def _structure_confidence(content: bytes, path: str):
    """Parse the hashed bytes, not a mutable path; require finite atom coordinates.

    Registered PDB/mmCIF readers validate structure syntax without imposing a
    model-specific sequence, chain, or completeness policy. Confidence is taken
    from the same parsed snapshot (first-model CA B factors, as in the viewer).
    """
    from io import StringIO
    import math
    from pathlib import Path
    from Bio.PDB import MMCIFParser, PDBParser

    try:
        suffix = Path(path).suffix.lower()
        if suffix == '.pdb':
            parser = PDBParser(PERMISSIVE=False, QUIET=True)
        elif suffix in {'.cif', '.mmcif'}:
            parser = MMCIFParser(QUIET=True)
        else:
            raise ValueError('unsupported structure format')
        structure = parser.get_structure('candidate', StringIO(content.decode('utf-8')))
        atoms = [atom for model in structure for chain in model
                 for residue in chain.get_unpacked_list() for atom in residue.get_unpacked_list()]
        if not atoms:
            raise ValueError('structure contains no atoms')
        if any(not all(math.isfinite(float(v)) for v in atom.coord) for atom in atoms):
            raise ValueError('structure contains nonfinite coordinates')
        first_model = next(structure.get_models())
        scores = [float(atom.bfactor) for atom in first_model.get_atoms() if atom.name == 'CA']
        if not scores or not all(math.isfinite(v) for v in scores):
            return None, None
        return sum(scores) / len(scores), [round(v, 2) for v in scores]
    except Exception as exc:
        raise CandidateIntegrityError('candidate_structure_invalid', f'unusable declared structure: {path}: {exc}') from exc


def revalidate_prepared_publication(root, receipt):
    """Controlled path revalidation only: never reinterpret replacement payloads."""
    evidence = [receipt['manifest']]
    evidence.extend(r['artifact'] for r in receipt.get('execution_settings', []))
    evidence.extend(artifact for candidate in receipt['candidates'].values() for artifact in candidate.values())
    for expected in evidence:
        current, _ = _artifact(root, expected['path'])
        if current != expected:
            raise CandidateIntegrityError('candidate_replay_changed', 'prepared publication bytes changed')


def prepare_esmfold2_publication(job, root, existing):
    """Bind the existing ESMFold2 producer samples manifest, before any row mutation."""
    import json
    manifest_artifact, content = _artifact(root, 'manifest.json')
    try:
        manifest = json.loads(content)
    except (ValueError, UnicodeError) as exc:
        raise CandidateIntegrityError('missing_candidate_declaration', 'invalid producer manifest JSON') from exc
    if not isinstance(manifest, dict):
        raise CandidateIntegrityError('missing_candidate_declaration', 'producer manifest must be an object')
    entries = manifest.get('samples')
    if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
        raise CandidateIntegrityError('missing_candidate_declaration', 'ESMFold2 requires producer samples declaration')
    ids = [e.get('sample_id') for e in entries]
    _ids(ids, 'ESMFold2 samples')
    if type(manifest.get('sample_count')) is not int or manifest['sample_count'] != len(ids):
        raise CandidateIntegrityError('candidate_count_mismatch', 'producer sample_count disagrees with sample identities')
    prepared = {}
    for entry in entries:
        candidate = entry['sample_id']
        structure, structure_bytes = _artifact(root, entry.get('cif'))
        confidence = _structure_confidence(structure_bytes, structure['path'])
        metrics, metrics_bytes = _artifact(root, entry.get('metrics'))
        try:
            payload = json.loads(metrics_bytes)
        except (ValueError, UnicodeError) as exc:
            raise CandidateIntegrityError('candidate_metrics_invalid', 'invalid candidate metrics JSON') from exc
        if not isinstance(payload, dict) or payload.get('sample_id') != candidate:
            raise CandidateIntegrityError('foreign_candidate_id', 'metrics candidate differs from declared candidate')
        if payload.get('cif') != entry.get('cif'):
            raise CandidateIntegrityError('foreign_candidate_artifact', 'metrics structure differs from declared structure')
        from services.esmfold2_scientific_consumer import scalar_block
        block = scalar_block(payload, candidate_id=candidate, document_id=entry['metrics'],
                             artifact_sha256=metrics['sha256'])
        prepared[candidate] = {'block': block, 'payload': {**manifest, **entry, **payload},
                               'structure_confidence': confidence,
                               'artifacts': {'structure': structure, 'metrics': metrics}}
    requested = (job.params or {}).get('esmf_num_diffusion_samples')
    summary = validate_candidate_accounting(stage_id='esmfold2', requested_count=requested,
        generated_ids=ids, dispositions=[{'candidate_id': i, 'disposition': 'selected'} for i in ids],
        expected_publication_ids=ids, persisted_ids=ids)
    receipt = {'summary': summary, 'manifest': manifest_artifact,
               'candidates': {i: prepared[i]['artifacts'] for i in ids}}
    if 'core_protein_requested_params' in (job.provenance or {}):
        from services.core_protein_execution_settings import prepare_receipt
        receipt['execution_settings'] = [prepare_receipt(job, root, root / 'effective_settings.json')]
    prior = (job.provenance or {}).get('core_protein_candidate_publication')
    if prior is not None and prior != receipt:
        raise CandidateIntegrityError('candidate_replay_changed', 'candidate replay evidence changed')
    if existing or prior is not None:
        if prior != receipt:
            raise CandidateIntegrityError('candidate_replay_changed', 'existing rows have no matching candidate replay receipt')
        validate_persisted_publication(job, existing, root)
        from services.esmfold2_scientific_consumer import canonical_bytes
        for row in existing:
            if receipt.get('execution_settings') and canonical_bytes((row.confidence_metrics or {}).get('core_protein_execution_settings')) != canonical_bytes(receipt['execution_settings']):
                raise CandidateIntegrityError('candidate_replay_changed', 'execution receipt replay changed')
            if canonical_bytes((row.confidence_metrics or {}).get('core_protein_scientific')) != canonical_bytes(prepared[row.name]['block']):
                raise CandidateIntegrityError('candidate_replay_changed', 'canonical scalar replay changed')
    return prepared, receipt


def retained_usable_candidate_count(rows, root) -> int:
    """Failure partialness is retained usable evidence, not mere row existence."""
    from pathlib import Path
    count = 0
    for row in rows:
        evidence = (row.confidence_metrics or {}).get('core_protein_candidate_artifacts')
        try:
            if evidence:
                for artifact in evidence.values():
                    current, _ = _artifact(Path(root), artifact['path'])
                    if current != artifact:
                        raise CandidateIntegrityError('candidate_replay_changed', 'retained bytes changed')
            else:
                _artifact(Path(root), row.pdb_path)
            count += 1
        except (CandidateIntegrityError, OSError, KeyError, TypeError):
            continue
    return count


def validate_persisted_publication(job, rows, root):
    """Exact row identity plus required artifact/hash validation; no basename join."""
    from pathlib import Path
    receipt = (job.provenance or {}).get('core_protein_candidate_publication')
    if not isinstance(receipt, dict) or not isinstance(receipt.get('candidates'), dict):
        raise CandidateIntegrityError('missing_candidate_declaration', 'marked generic result lacks candidate publication authority')
    expected = receipt['candidates']
    actual = _ids([row.name for row in rows], 'persisted candidates')
    if actual != set(expected):
        raise CandidateIntegrityError('candidate_publication_mismatch', 'persisted identities differ from expected publication')
    for row in rows:
        artifacts = expected[row.name]
        confidence = row.confidence_metrics or {}
        if confidence.get('core_protein_candidate_artifacts') != artifacts:
            raise CandidateIntegrityError('candidate_replay_changed', 'persisted candidate artifact evidence changed')
        for role, evidence in artifacts.items():
            # Boltz retains extra hash-bound native artifacts, without pretending
            # that every native ledger/vector/manifest is Design.json_path.
            native_extra = (receipt['summary'].get('stage_id') == 'boltz'
                            and role in {'manifest', 'ledger', 'pae', 'plddt'})
            native_extra = native_extra or (receipt['summary'].get('stage_id') == 'boltzgen' and role == 'native')
            field = row.pdb_path if role == 'structure' else row.json_path
            if not native_extra and field != evidence['path']:
                raise CandidateIntegrityError('candidate_publication_mismatch', 'persisted artifact identity differs from declaration')
            current, _ = _artifact(Path(root), evidence['path'])
            if current != evidence:
                raise CandidateIntegrityError('candidate_replay_changed', 'candidate artifact bytes changed')
    current, _ = _artifact(Path(root), receipt['manifest']['path'])
    if current != receipt['manifest']:
        raise CandidateIntegrityError('candidate_replay_changed', 'producer manifest changed')
    return receipt['summary']
