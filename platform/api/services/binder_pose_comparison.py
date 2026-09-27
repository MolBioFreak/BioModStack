"""Evidence-only exact CA comparison, persisted by the shared analysis owner.

No sequence alignment, role guessing, score classification or source mutation.
Transforms are prediction -> reference, using row-vector coordinates in angstrom.
"""
from __future__ import annotations

import hashlib
import numpy as np
from sqlalchemy import select

from database import Design, Job
from paths import resolve_allowed_path
from services.aligned_error_utils import _strict_structure_records, residue_identity_axis
from services.binder_diagnostic_selection import CandidateDocument, selected_document

ERRORS = (ValueError, TypeError, KeyError, IndexError, OSError, RuntimeError)


def normalize_params(raw):
    params = dict(raw or {})
    if set(params) - {'reference_design_id', 'reference_document', 'residue_mapping'}:
        raise ValueError('unknown binder_pose_comparison parameter')
    if 'reference_design_id' in params and (not isinstance(params['reference_design_id'], str) or not params['reference_design_id']):
        raise ValueError('invalid reference_design_id')
    if 'reference_document' in params:
        params['reference_document'] = CandidateDocument.model_validate(params['reference_document']).model_dump(mode='json')
    if 'residue_mapping' in params and not isinstance(params['residue_mapping'], list):
        raise ValueError('invalid residue_mapping')
    return params


def _selector_key(value):
    if (not isinstance(value, dict) or set(value) != {'chain_id', 'auth_seq_id', 'insertion_code'}
            or not isinstance(value['chain_id'], str) or not value['chain_id']
            or type(value['auth_seq_id']) is not int or not isinstance(value['insertion_code'], str)):
        raise ValueError('invalid exact author residue selector')
    return value['chain_id'], value['auth_seq_id'], value['insertion_code']


def _lookup(records):
    result = {}
    for residue in records:
        if residue.chain_type != 'protein' or residue.residue_name not in {
            'ALA', 'ARG', 'ASN', 'ASP', 'CYS', 'GLN', 'GLU', 'GLY', 'HIS', 'ILE',
            'LEU', 'LYS', 'MET', 'PHE', 'PRO', 'SER', 'THR', 'TRP', 'TYR', 'VAL', 'UNK',
        }:
            continue
        key = (residue.auth_asym_id, residue.auth_seq_id, residue.insertion_code)
        if key in result:
            raise ValueError('ambiguous author residue identity')
        if not np.isfinite(residue.ca_coord).all():
            raise ValueError('nonfinite CA coordinates')
        result[key] = residue
    return result


def _fit(moving, fixed):
    x, y = np.asarray(moving, dtype=float), np.asarray(fixed, dtype=float)
    if len(x) < 3 or min(np.linalg.matrix_rank(x - x.mean(0)), np.linalg.matrix_rank(y - y.mean(0))) < 2:
        raise ValueError('underdetermined_CA_fit')
    u, _, vt = np.linalg.svd((x - x.mean(0)).T @ (y - y.mean(0)))
    correction = np.eye(3)
    correction[-1, -1] = np.linalg.det(u @ vt)
    rotation = u @ correction @ vt
    translation = y.mean(0) - x.mean(0) @ rotation
    return rotation, translation


def _rmsd(moving, fixed, transform):
    rotation, translation = transform
    return float(np.sqrt(np.mean(np.sum((np.asarray(moving) @ rotation + translation - fixed) ** 2, axis=1))))


def compare_exact(reference_records, prediction_records, mapping):
    """Compare explicitly joined residues; report missing/unmatched rows, not zero."""
    reference, prediction = _lookup(reference_records), _lookup(prediction_records)
    if not isinstance(mapping, list) or not mapping:
        raise ValueError('missing_exact_residue_mapping')
    used_reference, used_prediction = set(), set()
    coordinates = {role: ([], []) for role in ('binder', 'target')}
    rows, counts = [], {}
    role_keys = {role: [set(), set()] for role in coordinates}
    for row in mapping:
        if not isinstance(row, dict) or set(row) != {'role', 'reference', 'prediction'} or row['role'] not in coordinates:
            raise ValueError('invalid_exact_residue_mapping')
        rk, pk = _selector_key(row['reference']), _selector_key(row['prediction'])
        if rk in used_reference or pk in used_prediction:
            raise ValueError('nonbijective_exact_residue_mapping')
        used_reference.add(rk); used_prediction.add(pk)
        role_keys[row['role']][0].add(rk); role_keys[row['role']][1].add(pk)
        r, p = reference.get(rk), prediction.get(pk)
        rows.append(dict(row, reference_index=r.index if r else None, prediction_index=p.index if p else None,
                         matched=r is not None and p is not None))
        if r is not None and p is not None:
            coordinates[row['role']][0].append(r.ca_coord)
            coordinates[row['role']][1].append(p.ca_coord)
    for role, keys in role_keys.items():
        rchains = {k[0] for k in keys[0]}; pchains = {k[0] for k in keys[1]}
        matched = len(coordinates[role][0])
        counts[role] = dict(requested=len(keys[0]), matched=matched,
            missing_reference=len(keys[0] - reference.keys()), missing_prediction=len(keys[1] - prediction.keys()),
            unmatched_reference=sum(k[0] in rchains for k in reference) - matched,
            unmatched_prediction=sum(k[0] in pchains for k in prediction) - matched)
    values = dict(binder_fitted_ca_rmsd=None, target_fitted_binder_ca_rmsd=None, target_fit_ca_rmsd=None)
    reasons, transforms = {}, {}
    for role, (fixed, moving) in coordinates.items():
        try:
            transform = _fit(moving, fixed)
            transforms[role] = dict(rotation=transform[0].tolist(), translation=transform[1].tolist())
            key = 'binder_fitted_ca_rmsd' if role == 'binder' else 'target_fit_ca_rmsd'
            values[key] = _rmsd(moving, fixed, transform)
            if role == 'target' and coordinates['binder'][0]:
                values['target_fitted_binder_ca_rmsd'] = _rmsd(coordinates['binder'][1], coordinates['binder'][0], transform)
        except ValueError as exc:
            reasons[role] = str(exc)
    measured = sum(value is not None for value in values.values())
    return dict(values, status='ok' if measured == 3 else 'partial' if measured else 'unavailable',
        reason=None if measured == 3 else 'insufficient_exact_CA_geometry', metric_reasons=reasons,
        units='angstrom', mapping=rows, counts=counts, transforms=transforms,
        total_reference_CA=len(reference), total_prediction_CA=len(prediction),
        unmatched_reference_CA=len(reference) - sum(c['matched'] for c in counts.values()),
        unmatched_prediction_CA=len(prediction) - sum(c['matched'] for c in counts.values()))


async def _snapshot(design, session, selector=None):
    owner = await session.scalar(select(Job).where(Job.id == design.job_id))
    if owner is None:
        raise ValueError('missing document owner')
    path, identity = await selected_document(owner, design, selector, session)
    from paths import resolve_runtime_data_path
    path = resolve_runtime_data_path(path) if path.is_absolute() else resolve_allowed_path(str(path))
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if identity.get('artifact_sha256') not in (None, digest):
        raise ValueError('changed selected document')
    # Reuse the native publication authority where it exists. Legacy/generator
    # selections bind exact bytes in the shared analysis signature and artifact.
    from services.core_protein_scientific_contract import native_spatial_consumer, revision_for_job
    consumer = await native_spatial_consumer(design, session) if revision_for_job(owner) == 1 else None
    if consumer is not None and selector is None:
        selected = await consumer.verified_native_design(design, session, structure_only=True)
        if selected['snapshots']['structure'] != raw:
            raise ValueError('changed native selected document')
    document = dict(candidateId=design.id, documentId=(identity.get('artifact_id') or 'primary') if selector else 'primary',
        contentSha256=digest, sourceKind='mmcif' if path.suffix.lower() in ('.cif', '.mmcif') else 'pdb')
    records, _ = _strict_structure_records(raw, document['sourceKind'] == 'mmcif', 1, '')
    return dict(document=document, selection=identity, records=records,
        axis=residue_identity_axis(records, candidate_id=design.id, document_id=document['documentId']))


def mapping_from_native_components(selected, owner, reference_document):
    """Join submitted positions to native tokens and bound reference residues."""
    from services.protenix_scientific_consumer import project_round_roles
    _, role_evidence = project_round_roles(selected, owner)
    step = (owner.provenance or {})['binder_round_step']
    spec = step.get('pose_comparison') or {}
    if spec.get('reference_sha256') != reference_document['contentSha256']:
        raise ValueError('missing_or_changed_reference_mapping_binding')
    mapping, unmatched = [], {'binder': 0, 'target': 0}
    for component in step['input_components']:
        reference_residues = component.get('reference_residues')
        if not isinstance(reference_residues, list) or len(reference_residues) != len(component['sequence']):
            raise ValueError('missing_exact_input_residue_mapping')
        chain = role_evidence['input_to_output_chain'][component['id']]
        tokens = [token for token in selected['native']['token_axis']['residues'] if token['chain_id'] == chain]
        for reference, token in zip(reference_residues, tokens, strict=True):
            if reference is None:
                unmatched[component['role']] += 1
                continue
            _selector_key(reference)
            mapping.append(dict(role=component['role'], reference=reference,
                prediction=dict(chain_id=token['auth_asym_id'], auth_seq_id=token['auth_seq_id'],
                                insertion_code=token['insertion_code'])))
    return mapping, unmatched


async def comparison_inputs(design, session, params=None):
    owner = await session.scalar(select(Job).where(Job.id == design.job_id))
    step = (owner.provenance or {}).get('binder_round_step') if owner else None
    params = normalize_params(params)
    step = step if isinstance(step, dict) else {}
    if not params.get('reference_design_id') and step.get('stage') != 'prediction':
        raise ValueError('missing_prediction_step')
    reference_id = params.get('reference_design_id') or step.get('backbone_design_id')
    reference = await session.scalar(select(Design).where(Design.id == reference_id)) if reference_id else None
    if reference is None:
        raise ValueError('missing_reference_design')
    spec = dict(step.get('pose_comparison') or {})
    spec.update({k: params[k] for k in ('reference_document', 'residue_mapping') if k in params})
    step = dict(step, pose_comparison=spec)
    selector = CandidateDocument.model_validate(spec['reference_document']) if spec.get('reference_document') else None
    return step, reference, await _snapshot(reference, session, selector), await _snapshot(design, session)


async def build_signature(design, params, session):
    from services.analysis_registry import _json_hash
    owner = await session.scalar(select(Job).where(Job.id == design.job_id))
    identity = dict(analysis_type='binder_pose_comparison', adapter=1, design_id=design.id, params=params,
        step=(owner.provenance or {}).get('binder_round_step') if owner else None,
        complex_components=(owner.params or {}).get('complex_components') if owner else None)
    try:
        _, _, reference, prediction = await comparison_inputs(design, session, params)
        identity.update(reference={k: v for k, v in reference.items() if k != 'records'},
                        prediction={k: v for k, v in prediction.items() if k != 'records'})
    except ERRORS as exc:
        identity.update(status='unavailable', reason=str(exc))
    return _json_hash(identity)


async def compute_comparison(design, params, session):
    params = normalize_params(params)
    result = dict(schema_version=1, analysis_type='binder_pose_comparison', design_id=design.id,
                  status='unavailable', reason=None, reference_design_id=None,
                  binder_fitted_ca_rmsd=None, target_fitted_binder_ca_rmsd=None, target_fit_ca_rmsd=None)
    try:
        step, reference, ref, pred = await comparison_inputs(design, session, params)
        result.update(reference_design_id=reference.id, reference_document=ref['document'],
            prediction_document=pred['document'], reference_selection=ref['selection'],
            prediction_selection=pred['selection'], reference_axis=ref['axis'], prediction_axis=pred['axis'])
        spec = step.get('pose_comparison') or {}
        mapping = spec.get('residue_mapping')
        if mapping is None and not params.get('reference_design_id'):
            from services.core_protein_scientific_contract import verified_native_spatial_design
            owner = await session.scalar(select(Job).where(Job.id == design.job_id))
            selected = await verified_native_spatial_design(design, session)
            if selected['artifacts']['structure']['sha256'] != pred['document']['contentSha256']:
                raise ValueError('changed prediction mapping binding')
            mapping, unmatched = mapping_from_native_components(selected, owner, ref['document'])
            result['unmapped_input_residue_counts'] = unmatched
            result['mapping_authority'] = 'submitted_component_reference_residues_to_native_tokens'
        result.update(compare_exact(ref['records'], pred['records'], mapping))
    except ERRORS as exc:
        result['reason'] = str(exc)
    summary = {k: result[k] for k in ('status', 'reason', 'binder_fitted_ca_rmsd', 'target_fitted_binder_ca_rmsd', 'target_fit_ca_rmsd')}
    return result, summary, result
