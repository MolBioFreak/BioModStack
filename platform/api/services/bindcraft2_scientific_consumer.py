"""Read-only BC2 comparison adapter; publication and selected custody stay owned.

Definitions: pinned d5bae16e filters.py (confidence metrics), loss.py
chain_pair_pae_loss (PAE / 31), protein.py quality_metric_categories.
No inference, verdict edits, B-factor confidence, or cross-document QA borrowing.
"""
import hashlib
import json
import math
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from fastapi import HTTPException

from database import Job
from services.bindcraft2_publication import (
    _regular, native_campaign_root, read_published_native_results,
    verify_selected_native_designs,
)

PRODUCER = 'bindcraft2:d5bae16e9fee95f4c97fc16bc05dcbde4ccb885f'
# Scope names deliberately retain the native function rather than claiming all
# fields are interchangeable complex confidence. i_pAE is NOT angstroms.
DESCRIPTORS = {
    'pLDDT': ('fraction', 'native_binder_confidence_in_complex', 'higher_is_better'),
    'pTM': ('fraction', 'native_ptm_metric', 'higher_is_better'),
    'i_pTM': ('fraction', 'native_iptm_metric', 'higher_is_better'),
    'i_pAE': ('dimensionless', 'native_chain_pair_pae_loss_divided_by_31', 'lower_is_better'),
    'Unbound_Binder_pLDDT': ('fraction', 'native_plddt_metric_binder_alone', 'higher_is_better'),
    'Target_pLDDT': ('fraction', 'native_plddt_metric_target', 'higher_is_better'),
    'i_pDAE': ('fraction', 'native_max_anchored_interface_tm_score', 'higher_is_better'),
    'Binder_RMSD': ('angstrom', 'native_aligned_bound_unbound_binder_rmsd', 'lower_is_better'),
}
ERRORS = (ValueError, TypeError, KeyError, IndexError, OSError, RuntimeError, HTTPException)


def _binding(receipt, design):
    matches = [b for b in receipt['candidates'] if b['design_id'] == design.id]
    if len(matches) != 1:
        raise ValueError('ambiguous_native_candidate')
    binding = matches[0]
    primary = [s for s in binding['structures'] if s['primary']]
    if len(primary) != 1:
        raise ValueError('ambiguous_native_document')
    return binding, primary[0]


async def scalar_projections(designs, job, session):
    """One custody/read pass per Job, not one full campaign pass per Design."""
    from services.scientific_analytics import projection, metric_state
    try:
        publication, receipt = await read_published_native_results(job, session)
    except ERRORS:
        return {d.id: projection(d, invalid_reason='invalid_scalar_publication',
                model_id='bindcraft2', source_kind='retained_native_artifact') for d in designs}
    rows = {(arm.name, row.design): row for arm in publication.arms for row in arm.draws}
    result = {}
    for design in designs:
        try:
            binding, primary = _binding(receipt, design)
            row = rows[(binding['arm'], binding['scored_design'])]
            # Same reader's native table dialect; never guess a row by sequence/name prefix.
            prefix = (binding['arm'] + '/') if binding['arm'] else ''
            table = prefix + ('2_Refolded/!_Refolded.csv' if prefix + '2_Refolded/!_Refolded.csv' in receipt['files'] else 'candidates.csv')
            source = dict(artifact_sha256=receipt['files'][table]['sha256'],
                candidate_id=json.dumps([binding['arm'], row.design], separators=(',', ':')),
                document_id=table)
            records = []
            for key, (unit, scope, direction) in DESCRIPTORS.items():
                raw = row.target_readings.get(key, {}).get(primary['target_state']) if key in row.target_readings else row.values.get(key)
                try:
                    state = metric_state(float(raw) if raw not in (None, '') else None)
                except (TypeError, ValueError):
                    state = metric_state(raw)
                records.append(dict(metric_key=key, unit=unit, scope=scope + ':target_state=' + primary['target_state'],
                    direction=direction, producer_version=PRODUCER, derivation_version='native_csv_identity_v1',
                    source=source, **state.model_dump()))
            result[design.id] = projection(design, records=records, source_kind='retained_native_artifact')
        except ERRORS:
            result[design.id] = projection(design, invalid_reason='invalid_scalar_publication',
                model_id='bindcraft2', source_kind='retained_native_artifact')
    return result


async def selected_document(design, session, *, artifact_id=None, target_state=None):
    job = await session.get(Job, design.job_id)
    await verify_selected_native_designs(job, session, [design])
    receipt = job.provenance['bindcraft2_native_publication']
    binding, primary = _binding(receipt, design)
    if artifact_id is not None or target_state is not None:
        from services.binder_diagnostic_selection import selected_document as resolve_document, CandidateDocument
        _, selected = await resolve_document(job, design,
            CandidateDocument(artifact_id=artifact_id, target_state=target_state), session)
        primary = dict(selected['producer_document'], viewer_document_id='artifact:' + selected['artifact_id'])
        # Resolve the selected alternate through the existing artifact/root owner.
        _, identity, raw = await native_document_snapshot(session, job.id, primary['artifact_id'], target_state=target_state)
        if identity['artifact_sha256'] != primary['sha256']:
            raise ValueError('changed_native_document')
    else:
        root = native_campaign_root(job, Path(receipt['root']))
        _, raw = _regular(root, primary['logical_path'].removeprefix('bindcraft2/native/'))
    if hashlib.sha256(raw).hexdigest() != primary['sha256']:
        raise ValueError('changed_native_document')
    return binding, primary, raw


async def native_document_snapshot(session, job_id, artifact_id, *, target_state=None):
    from services.binder_native_selection import resolve_native_source
    from services.frustrampnn.jobs import _read_owned_structure
    path, identity = await resolve_native_source(session, job_id, artifact_id)
    if target_state is not None and identity['target_state'] != target_state:
        raise ValueError('selected_native_target_state_changed')
    raw = _read_owned_structure(path, label='Selected native comparison structure')
    if hashlib.sha256(raw).hexdigest() != identity['artifact_sha256']:
        raise ValueError('changed_native_document')
    return path, identity, raw


def native_document_wire(job_id, artifact_id, identity):
    from services.scientific_viewer_contract import ViewerDocument
    return ViewerDocument(documentId='artifact:' + artifact_id,
        candidateId='native:' + job_id + ':' + artifact_id,
        contentSha256=identity['artifact_sha256'], sourceKind='mmcif')


async def native_document_metadata(session, job_id, artifact_id, *, target_state=None):
    path, identity, _ = await native_document_snapshot(session, job_id, artifact_id, target_state=target_state)
    doc = native_document_wire(job_id, artifact_id, identity)
    # PDB bodies remain readable; there is no native local CIF QA in a PDB.
    if path.suffix.lower() not in ('.cif', '.mmcif'):
        doc.sourceKind = 'pdb'
    return dict(candidate_ref=dict(kind='native_document', job_id=job_id, artifact_id=artifact_id),
        document=doc, target_state=identity['target_state'])


def document(design, primary):
    from services.scientific_viewer_contract import ViewerDocument
    return ViewerDocument(documentId=primary.get('viewer_document_id', 'primary'), candidateId=design.id,
                contentSha256=primary['sha256'], sourceKind='mmcif')


async def scientific_documents(designs, job, session):
    try:
        await verify_selected_native_designs(job, session, designs)
        receipt = job.provenance['bindcraft2_native_publication']
        return {d.id: document(d, _binding(receipt, d)[1]) for d in designs}
    except ERRORS:
        return {d.id: None for d in designs}


async def scientific_document(design, session, *, artifact_id=None, target_state=None):
    if artifact_id is not None or target_state is not None:
        try:
            _, primary, _ = await selected_document(design, session, artifact_id=artifact_id, target_state=target_state)
            return document(design, primary)
        except ERRORS:
            return None
    job = await session.get(Job, design.job_id)
    return (await scientific_documents([design], job, session))[design.id]


def local_qa(raw):
    """Join genuine CIF local pLDDT to full atom-site residue identity.

    Local QA keys omit author/insertion/altloc. Ambiguous label-to-residue joins
    are unreadable, not resolved by array order or by silently selecting a model.
    Percent-to-fraction is only the existing viewer transport unit conversion;
    the original QA source bytes and native scalar measurements are unchanged.
    """
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict
    table = MMCIF2Dict(StringIO(raw.decode('utf-8')))
    def columns(category, fields):
        values = [table.get(category + '.' + field, []) for field in fields]
        if len({len(v) for v in values}) != 1:
            raise ValueError('invalid_native_qa_columns')
        return list(zip(*values))
    definitions = columns('_ma_qa_metric', ('id', 'name', 'type', 'mode'))
    matches = [id for id, name, kind, mode in definitions if (name, kind, mode) == ('pLDDT', 'pLDDT', 'local')]
    if not matches:
        return None
    if len(matches) != 1 or len({r[0] for r in definitions}) != len(definitions):
        raise ValueError('ambiguous_native_qa_definition')
    atom_count = len(table.get('_atom_site.id', []))
    def cell(key, i, default=None):
        values = table.get('_atom_site.' + key)
        if values is None:
            return default
        if len(values) != atom_count:
            raise ValueError('invalid_native_atom_column')
        return default if values[i] in ('.', '?', '') else values[i]
    def number(v):
        return int(v) if v is not None else None
    residues = {}
    for i in range(atom_count):
        auth, label = cell('auth_asym_id', i), cell('label_asym_id', i)
        residue = dict(chain_id=auth or label, residue_name=cell('label_comp_id', i),
            insertion_code=cell('pdbx_PDB_ins_code', i, ''), selected_model=number(cell('pdbx_PDB_model_num', i)),
            selected_altloc=cell('label_alt_id', i, ''), auth_asym_id=auth,
            auth_seq_id=number(cell('auth_seq_id', i)), label_asym_id=label,
            label_seq_id=number(cell('label_seq_id', i)), source_entity_id=cell('label_entity_id', i), entity_instance_id=None)
        key = (residue['selected_model'], label, residue['label_seq_id'], residue['residue_name'])
        if key in residues and residues[key] != residue:
            raise ValueError('ambiguous_native_qa_residue_identity')
        residues[key] = residue
    axis, values, seen = [], [], set()
    for model, chain, seq, comp, metric, value in columns('_ma_qa_metric_local',
            ('model_id', 'label_asym_id', 'label_seq_id', 'label_comp_id', 'metric_id', 'metric_value')):
        if metric != matches[0]:
            continue
        key = (int(model), chain, int(seq), comp)
        if key in seen or key not in residues:
            raise ValueError('ambiguous_or_unmapped_native_qa')
        seen.add(key)
        value = float(value)
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError('invalid_native_qa_value')
        axis.append(dict(residues[key], index=len(axis)))
        values.append(value / 100.0)
    return (axis, values) if axis else None


def _profile(design, doc, producer, qa):
    from services.scientific_viewer_contract import ScientificResidueMetric
    residues, values = qa
    return ScientificResidueMetric(schema_name='core_protein_viewer_metric', schema_version=1,
        contract_revision=1, design_id=design.id, design_name=design.name, status='ok', reason=None,
        metric='residue_plddt', units='fraction', values=values, document=doc,
        producer_binding=producer, artifact_sha256=doc.contentSha256,
        axis=dict(**producer, source_sha256=doc.contentSha256, residues=residues,
            producer_version=PRODUCER, confidence_scope='collapsed_residue', stored_units='percent'),
        native_positions=list(range(len(values))))


def unavailable(design, metric, reason):
    from services.analysis_registry import unavailable_scientific_identity
    from services.scientific_viewer_contract import ScientificViewerMetric
    return ScientificViewerMetric.model_validate(unavailable_scientific_identity(design, metric, reason))


async def native_artifact_metric(session, job_id, artifact_id, metric, *, target_state=None):
    # This is a source display context, NOT an ORM Design or a fabricated Design ID.
    context = SimpleNamespace(id=None, name=artifact_id)
    reason = 'native_chain_metrics_not_published' if metric == 'chain_metrics' else 'native_local_qa_not_published'
    try:
        path, identity, raw = await native_document_snapshot(session, job_id, artifact_id, target_state=target_state)
        context.name = path.name
        qa = local_qa(raw) if metric == 'residue_plddt' and path.suffix.lower() in ('.cif', '.mmcif') else None
        if qa is not None:
            # Artifact identity needs no new candidate/metadata qualification gate.
            producer = dict(candidate_id=artifact_id, document_id=identity['producer_document'].get('path') or artifact_id)
            return _profile(context, native_document_wire(job_id, artifact_id, identity), producer, qa)
    except ERRORS:
        reason = 'missing_or_invalid_bindcraft2_native_evidence'
    return unavailable(context, metric, reason)


async def compute_persisted_native_metric(design, metric, session, *, artifact_id=None, target_state=None):
    reason = 'native_chain_metrics_not_published' if metric == 'chain_metrics' else 'native_local_qa_not_published'
    try:
        binding, primary, raw = await selected_document(design, session, artifact_id=artifact_id, target_state=target_state)
        qa = local_qa(raw) if metric == 'residue_plddt' else None
        if qa is not None:
            producer = dict(candidate_id=json.dumps([binding['arm'], binding['retained_design']], separators=(',', ':')),
                            document_id=primary['logical_path'])
            return _profile(design, document(design, primary), producer, qa)
    except ERRORS:
        reason = 'missing_or_invalid_bindcraft2_native_evidence'
    return unavailable(design, metric, reason)

