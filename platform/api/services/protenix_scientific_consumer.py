"""Read-only Protenix native confidence through existing publication custody.

The pinned dumper writes the same AtomArray in order and applies the same sample
rank to CIF/summary/full_data. Tokenizer uses standard polymer residues as tokens
and individual atoms otherwise. Hashes authorize these bytes, not correspondence:
we reconstruct and check that native mapping before exposing any metric.
"""
from __future__ import annotations

import hashlib
from io import StringIO
import json
import math
from pathlib import Path

from sqlalchemy import select
from database import Design, Job
# Reuse the existing contained, component-by-component no-follow snapshot reader;
# none of the Boltz scientific/publication assumptions are used here.
from services.boltz_scientific_persistence import _snapshot
from services.core_protein_scientific_contract import revision_for_job
from services.scientific_viewer_contract import (
    ScientificAtomMetric, ScientificChainMetric, ScientificViewerMetric, ViewerDocument,
)

PINNED_PRODUCER = 'bd54a05d047b8925a241056f36d619700604068a'
_STANDARD = frozenset('ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL UNK A G C U N DA DG DC DT DN'.split())
_ERRORS = (ValueError, TypeError, KeyError, IndexError, OSError, RuntimeError)


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate native JSON key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('nonfinite native JSON')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _key(root, path):
    path = Path(path)
    return path.relative_to(root).as_posix() if path.is_absolute() else path.as_posix()


def _checked(root, descriptor):
    artifact, raw = _snapshot(root, _key(root, descriptor['path']))
    if artifact != descriptor:
        raise ValueError('changed native publication artifact')
    return artifact, raw


def _return_artifacts(job, root):
    """Old publications can use already-committed return custody, never new hashes."""
    from services.remote_execution.contracts import RemoteResultManifest
    from services.remote_execution.result_generation import identity
    generation = (job.provenance or {}).get('remote_result_generation')
    if not isinstance(generation, dict) or generation != identity(job, generation['manifest_sha256']):
        raise ValueError('missing native confidence custody')
    receipt = (job.provenance or {})['remote_execution_receipt']
    if (receipt['result_manifest_sha256'] != generation['manifest_sha256']
            or receipt['received_manifest_sha256'] != generation['manifest_sha256']):
        raise ValueError('foreign native return receipt')
    artifact, raw = _snapshot(root, 'result-manifest.json')
    if artifact['sha256'] != generation['manifest_sha256']:
        raise ValueError('changed native return manifest')
    manifest = RemoteResultManifest.model_validate(_json(raw))
    if (manifest.job_id != str(job.id) or manifest.attempt_id != job.remote_attempt_id
            or manifest.source_revision != job.execution_source_revision
            or manifest.source_tree != job.execution_source_tree
            or manifest.execution_envelope_sha256 != job.execution_bundle_sha256):
        raise ValueError('foreign native return manifest')
    result = {item.relative_path: item for item in manifest.artifacts}
    if len(result) != len(manifest.artifacts):
        raise ValueError('duplicate native return artifact')
    return result


def verify_publication(job, design, root, *, confidence=True):
    """No DB mutation, inference or retrospective publication of discovered files."""
    if (job.model_id != 'protenix' or revision_for_job(job) != 1
            or design.job_id != job.id or design.source_stage is not None):
        raise ValueError('foreign selected native design')
    native = (design.provenance or {})['native_producer']
    if native['producer_method'] != 'protenix' or native['source_format'] != 'mmcif':
        raise ValueError('foreign native producer')
    artifacts = (design.confidence_metrics or {})['core_protein_candidate_artifacts']
    published = job.provenance['core_protein_candidate_publication']['candidates'][design.name]
    required = ('structure', 'metrics') if confidence else ('structure',)
    if (any(artifacts.get(key) != published.get(key) for key in required)
            or design.pdb_path != artifacts['structure']['path']
            or (confidence and design.json_path != artifacts['metrics']['path'])):
        raise ValueError('foreign persisted native candidate')
    snapshots = {key: _checked(root, artifacts[key])[1] for key in required}
    if artifacts['structure']['sha256'] != native['producer_artifact_sha256']:
        raise ValueError('foreign native structure digest')
    matches = []
    seen = set()
    for entry in job.provenance['protenix_primary_publication']:
        pub = _json(_checked(root, entry['publication'])[1])
        manifest = _json(_checked(root, entry['producer_manifest'])[1])
        if (pub['schema_name'] != 'structure_producer_publication' or pub['schema_version'] != 1
                or manifest['schema_name'] not in ('structure_producer_candidates', 'sequence_structure_producer_candidates')
                or manifest['schema_version'] != 1
                or pub['producer_manifest']['sha256'] != entry['producer_manifest']['sha256']
                or pub['producer_manifest']['relative_path'] != _key(root, entry['producer_manifest']['path'])):
            raise ValueError('foreign native publication')
        candidates = {c['producer_output_key']: c for c in manifest['candidates']}
        bindings = {b['producer_output_key']: b for b in pub['bindings']}
        if (not candidates or len(candidates) != len(manifest['candidates'])
                or len(bindings) != len(pub['bindings']) or set(bindings) != set(candidates)
                or seen.intersection(candidates)):
            raise ValueError('duplicate or incomplete native publication')
        seen.update(candidates)
        key = native['producer_output_key']
        if key in candidates:
            binding = bindings[key]
            if (candidates[key] != native or binding['source_format'] != 'mmcif'
                    or binding['sha256'] != artifacts['structure']['sha256']
                    or binding['size_bytes'] != len(snapshots['structure'])
                    or binding['published_relative_path'] != _key(root, design.pdb_path)):
                raise ValueError('foreign native publication binding')
            matches.append(binding)
    if len(matches) != 1:
        raise ValueError('missing selected native publication')
    selected = dict(design_id=design.id, snapshots=snapshots, artifacts=dict(artifacts),
                    block=dict(candidate_id=native['producer_output_key'], document_id=native['producer_output_key']),
                    source_kind='mmcif')
    if not confidence:
        return selected
    # Exact pinned dumper pairing, applied only to the declared producer key and
    # publication destination. Presence or a similar basename is not authority.
    from scripts.write_structure_producer_manifest import protenix_confidence_names
    names = protenix_confidence_names(Path(native['producer_output_key']).name)
    binding = matches[0]
    descriptors = binding.get('confidence')
    returned = None if descriptors is not None else _return_artifacts(job, root)
    for kind, name in names.items():
        key = str(Path(binding['published_relative_path']).parent / name)
        artifact, raw = _snapshot(root, key)
        if descriptors is not None:
            expected = descriptors[kind]
            if (expected['published_relative_path'] != key or expected['sha256'] != artifact['sha256']
                    or expected['size_bytes'] != len(raw)):
                raise ValueError('changed native confidence publication')
        else:
            expected = returned[key]
            if expected.sha256 != artifact['sha256'] or expected.size_bytes != len(raw):
                raise ValueError('changed returned native confidence')
        if kind == 'metrics' and (artifact != artifacts['metrics'] or raw != snapshots['metrics']):
            raise ValueError('foreign native summary pairing')
        selected['artifacts'][kind] = artifact
        selected['snapshots'][kind] = raw
    selected['native'] = derive_native_identity(snapshots['structure'], selected['snapshots']['pae'],
                                               snapshots['metrics'], selected['block'])
    return selected


def derive_native_identity(source, full_raw, summary_raw, binding):
    """Reconstruct pinned full-CIF order and check native atom/token/asym maps.

    CIF B factors and full_data round at different scales; the agreement check
    admits exactly their combined rounding error, not an equality of decimals.
    The returned values remain the full_data atom fractions, not B factors.
    """
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict
    cif = MMCIF2Dict(StringIO(source.decode('utf-8')))
    full, summary = _json(full_raw), _json(summary_raw)
    fields = ('id', 'label_atom_id', 'auth_atom_id', 'type_symbol', 'label_comp_id', 'auth_comp_id',
              'label_asym_id', 'auth_asym_id', 'label_entity_id', 'label_seq_id', 'auth_seq_id',
              'pdbx_PDB_ins_code', 'label_alt_id', 'pdbx_PDB_model_num', 'B_iso_or_equiv')
    columns = {key: cif['_atom_site.' + key] for key in fields}
    count = len(columns['id'])
    if not count or any(len(v) != count for v in columns.values()):
        raise ValueError('invalid native atom-site shape')
    if columns['id'] != [str(i + 1) for i in range(count)]:
        raise ValueError('reordered native atom positions')
    values = full['atom_plddt']
    atom_map, asym = full['atom_to_token_idx'], full['token_asym_id']
    if (len(values) != count or len(atom_map) != count
            or any(type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 1 for v in values)
            or any(type(i) is not int or i < 0 for i in atom_map + asym)):
        raise ValueError('invalid native confidence vector/map')
    polymer = dict(zip(cif.get('_entity_poly.entity_id', []), cif.get('_entity_poly.type', []), strict=True))
    atom_rows, tokens, chains = [], [], {}
    expected_map, expected_asym = [], []
    seen_residues, seen_atoms = set(), set()
    last_key = None
    def clean(value):
        return '' if value in ('.', '?') else value
    for i in range(count):
        row = {k: v[i] for k, v in columns.items()}
        chain, auth, entity = row['label_asym_id'], row['auth_asym_id'], row['label_entity_id']
        if (not all(clean(v) for v in (chain, auth, entity, row['label_atom_id'], row['auth_atom_id']))
                or row['label_comp_id'] != row['auth_comp_id']
                or clean(row['label_alt_id']) or row['pdbx_PDB_model_num'] != '1'):
            raise ValueError('unsupported native atom identity')
        # Pinned input assembly and integer-chain annotation are in first-chain
        # occurrence order. Preserve CIF label/auth namespaces independently.
        if chain not in chains:
            chains[chain] = (len(chains), auth, entity)
        chain_index, expected_auth, expected_entity = chains[chain]
        if (auth, entity) != (expected_auth, expected_entity):
            raise ValueError('contradictory native chain labels')
        residue = dict(index=0, chain_id=chain, residue_name=row['label_comp_id'],
            insertion_code=clean(row['pdbx_PDB_ins_code']), selected_model=1, selected_altloc='',
            auth_asym_id=auth, auth_seq_id=int(row['auth_seq_id']), label_asym_id=chain,
            label_seq_id=int(row['label_seq_id']) if clean(row['label_seq_id']) else None,
            source_entity_id=entity, entity_instance_id=f'mmcif:{entity}:{chain}:{auth}')
        residue_key = tuple(residue.values())[1:]
        atom_key = (*residue_key, row['label_atom_id'], row['auth_atom_id'])
        if atom_key in seen_atoms:
            raise ValueError('duplicate native atom identity')
        seen_atoms.add(atom_key)
        atom = dict(residue, index=i, label_atom_id=row['label_atom_id'],
                    auth_atom_id=row['auth_atom_id'], element=row['type_symbol'])
        atom_rows.append(atom)
        is_residue_token = entity in polymer and row['label_comp_id'] in _STANDARD
        if residue_key != last_key:
            if residue_key in seen_residues:
                raise ValueError('noncontiguous native residue')
            seen_residues.add(residue_key)
            if is_residue_token:
                tokens.append(dict(residue, index=len(tokens)))
                expected_asym.append(chain_index)
            last_key = residue_key
        if not is_residue_token:
            tokens.append(dict(atom, index=len(tokens)))
            expected_asym.append(chain_index)
        expected_map.append(len(tokens) - 1)
        bfactor = float(row['B_iso_or_equiv'])
        if not math.isfinite(bfactor) or abs(bfactor / 100 - values[i]) > 0.005051:
            raise ValueError('native atom confidence/CIF disagreement')
    if atom_map != expected_map or asym != expected_asym:
        raise ValueError('native token/chain mapping disagrees with source contract')
    n = len(tokens)
    pae = full['token_pair_pae']
    if (len(pae) != n or any(len(row) != n for row in pae)
            or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for row in pae for v in row)):
        raise ValueError('invalid native token PAE')
    axis = lambda rows: dict(binding, source_sha256=hashlib.sha256(source).hexdigest(), residues=rows)
    # AddAtomArrayAnnot.unique_chain_and_add_ids (pinned parser.py3206-3226):
    # asym IDs enumerate contiguous chains; entity IDs enumerate lexically sorted
    # unique entity labels (NOT int(label)-1); sym IDs count prior entity copies.
    entity_ids = {entity: i for i, entity in enumerate(sorted({value[2] for value in chains.values()}))}
    chain_map = [dict(native_asym_id=index, source_chain_index=index, output_asym_id=None,
                      chain_id=chain, native_entity_id=entity_ids[entity],
                      native_sym_id=sum(1 for prior in list(chains)[:index] if chains[prior][2] == entity))
                 for chain, (index, auth, entity) in chains.items()]
    k = len(chains)
    if (len(summary['chain_ptm']) != k or len(summary['chain_pair_iptm']) != k
            or any(len(row) != k for row in summary['chain_pair_iptm'])):
        raise ValueError('invalid native chain matrix shape')
    return dict(atom_axis=axis(atom_rows), token_axis=axis(tokens), atom_values=values,
                pae=pae, chain_index_map=chain_map,
                chains_ptm={str(i): v for i, v in enumerate(summary['chain_ptm'])},
                pair_chains_iptm={str(i): {str(j): v for j, v in enumerate(row)}
                                  for i, row in enumerate(summary['chain_pair_iptm'])})


def native_ipsae_artifact(selected):
    """Project proven residue tokens, never collapse ligand atom tokens or PAE.

    Coordinates come from the exact selected CIF bytes. The derived submatrix's
    native positions are explicit; raw full_data and its token axis stay intact.
    """
    from dataclasses import replace
    import numpy as np
    from services.aligned_error_utils import AlignedErrorArtifact, _strict_structure_records
    records, _ = _strict_structure_records(selected['snapshots']['structure'], True, 1, '')
    fields = ('residue_name', 'insertion_code', 'selected_model', 'selected_altloc',
              'auth_asym_id', 'auth_seq_id', 'label_asym_id', 'label_seq_id', 'source_entity_id')
    lookup = {}
    for record in records:
        key = tuple(getattr(record, field) for field in fields)
        if key in lookup:
            raise ValueError('ambiguous native coordinate mapping')
        lookup[key] = record
    native = selected['native']
    residues, positions = [], []
    for token in native['token_axis']['residues']:
        if 'label_atom_id' in token:
            continue  # Atom-token ligands have no residue ipSAE interpretation.
        record = lookup.get(tuple(token[field] for field in fields))
        if record is None or not np.isfinite([record.ca_coord, record.cb_coord]).all():
            raise ValueError('missing native residue coordinates')
        residues.append(replace(record, index=len(residues), chain_id=token['chain_id'],
                                entity_instance_id=token['entity_instance_id']))
        positions.append(token['index'])
    if not residues or len(set(positions)) != len(positions):
        raise ValueError('unsupported native residue token mapping')
    evidence = dict(artifact_sha256=selected['artifacts']['pae']['sha256'],
        matrix_key='token_pair_pae', row_axis=native['token_axis'], column_axis=native['token_axis'],
        native_token_positions=positions, excluded_atom_token_count=len(native['pae']) - len(positions))
    return AlignedErrorArtifact(path=Path(selected['artifacts']['pae']['path']),
        format='protenix_full_json', matrix_key='token_pair_pae',
        matrix=np.asarray(native['pae'], dtype=float)[np.ix_(positions, positions)],
        residues=residues, row_positions=tuple(range(len(residues))),
        column_positions=tuple(range(len(residues))), identity_evidence=evidence, contract_revision=1)


def project_round_roles(selected, job):
    """Bind explicit request component roles to validated native output chains.

    Only the ordinary ordered, single-copy protein complex adapter is supported
    here. Sequence equality verifies that explicit positional binding; it never
    searches sequences to infer ancestry or chooses between equivalent chains.
    """
    from Bio.SeqUtils import seq1
    if job is None:
        raise ValueError('missing request owner')
    step = (job.provenance or {}).get('binder_round_step') or {}
    if not isinstance(step, dict):
        raise ValueError('invalid request step')
    components = (job.params or {}).get('complex_components')
    if not isinstance(components, list) or not components:
        raise ValueError('missing request component mapping')
    chains = selected['native']['chain_index_map']
    if len(chains) != len(components):
        raise ValueError('unsupported request component copies')
    mapping = {}
    for index, (component, chain) in enumerate(zip(components, chains, strict=True)):
        if not isinstance(component, dict):
            raise ValueError('invalid request component')
        identifier = component.get('id')
        if (not isinstance(identifier, str) or not identifier or identifier in mapping
                or component.get('type', 'protein') not in ('protein', 'peptide')
                or component.get('count', 1) != 1 or component.get('modifications')
                or chain['native_asym_id'] != index):
            raise ValueError('unsupported request component mapping')
        tokens = [t for t in selected['native']['token_axis']['residues'] if t['chain_id'] == chain['chain_id']]
        sequence = component.get('sequence')
        if (not isinstance(sequence, str) or len(tokens) != len(sequence)
                or any('label_atom_id' in t or t['source_entity_id'] != str(index + 1)
                       or t['label_seq_id'] != position + 1 for position, t in enumerate(tokens))
                or ''.join(seq1(t['residue_name']) for t in tokens) != sequence):
            raise ValueError('request component/native residue disagreement')
        mapping[identifier] = chain['chain_id']
    submitted = step.get('input_components')
    if not isinstance(submitted, list) or len(submitted) != len(components):
        raise ValueError('missing explicit input component roles')
    roles = {'binder_chains': [], 'target_chains': []}
    for index, (bound, component) in enumerate(zip(submitted, components, strict=True)):
        if not isinstance(bound, dict) or type(bound.get('index')) is not int:
            raise ValueError('invalid input component binding')
        role = bound.get('role')
        if (bound.get('index') != index or bound.get('id') != component['id']
                or bound.get('sequence') != component['sequence'] or role not in ('binder', 'target')
                or bound.get('source_chain') not in step.get(role + '_chains', [])):
            raise ValueError('inconsistent explicit input component roles')
        roles[role + '_chains'].append(mapping[bound['id']])
    if not all(roles.values()) or set(roles['binder_chains']) & set(roles['target_chains']):
        raise ValueError('missing or overlapping explicit input roles')
    return roles, dict(source='binder_round_step.input_components', input_to_output_chain=mapping)


async def verified_native_design(design, session, *, structure_only=False):
    from paths import get_data_root, resolve_runtime_data_path
    with session.no_autoflush:
        job = await session.scalar(select(Job).where(Job.id == design.job_id))
        row = await session.scalar(select(Design).where(Design.id == design.id, Design.job_id == design.job_id))
        if job is None or row is None or not job.output_dir:
            raise ValueError('missing native publication owner')
        root = Path(job.output_dir)
        root = resolve_runtime_data_path(root) if root.is_absolute() else get_data_root() / root
        return verify_publication(job, row, root, confidence=not structure_only)


async def scientific_document(design, session):
    try:
        selected = await verified_native_design(design, session, structure_only=True)
        return _document(design, selected)
    except _ERRORS:
        return None


def _document(design, selected):
    return ViewerDocument(documentId='primary', candidateId=design.id,
                          contentSha256=selected['artifacts']['structure']['sha256'], sourceKind='mmcif')


def _unavailable(design, metric):
    from services.analysis_registry import unavailable_scientific_identity
    return ScientificViewerMetric.model_validate(unavailable_scientific_identity(
        design, metric, 'missing_or_invalid_protenix_native_evidence'))


async def compute_persisted_native_metric(design, metric, session, *, selected=None):
    try:
        selected = selected or await verified_native_design(design, session)
        if selected['design_id'] != design.id or selected.get('source_kind') != 'mmcif':
            raise ValueError('foreign selected native snapshot')
        native = selected['native']
        axis = native['atom_axis'] if metric == 'residue_plddt' else native['token_axis']
        payload = dict(schema_name='core_protein_viewer_metric', schema_version=1, contract_revision=1,
            design_id=design.id, design_name=design.name, status='ok', reason=None,
            document=_document(design, selected), producer_binding=selected['block'],
            axis=axis, native_positions=[r['index'] for r in axis['residues']])
        if metric == 'residue_plddt':
            return ScientificAtomMetric(**payload, metric='atom_plddt', units='fraction',
                artifact_sha256=selected['artifacts']['pae']['sha256'], values=native['atom_values'])
        if metric == 'chain_metrics':
            return ScientificChainMetric(**payload, metric=metric,
                artifact_sha256=selected['artifacts']['metrics']['sha256'],
                chain_index_map=native['chain_index_map'], chains_ptm=native['chains_ptm'],
                pair_chains_iptm=native['pair_chains_iptm'], role_assignment=None, role_reason='missing_role_assignment')
        raise ValueError('unsupported native metric')
    except _ERRORS:
        return _unavailable(design, metric)


async def compute_persisted_pae(design, params, session, *, selected=None):
    try:
        selected = selected or await verified_native_design(design, session)
        if selected['design_id'] != design.id or selected.get('source_kind') != 'mmcif':
            raise ValueError('foreign selected native snapshot')
        native = selected['native']
        axis, matrix = native['token_axis'], native['pae']
        n = len(matrix)
        maximum = params.get('max_size', 300)
        if type(maximum) is not int or maximum < 1:
            raise ValueError('invalid PAE sample size')
        # Point sampling, never block averaging or ligand-token collapse.
        import numpy as np
        indexes = np.linspace(0, n - 1, min(n, maximum), dtype=int).tolist()
        result = ScientificViewerMetric(schema_name='core_protein_viewer_metric', schema_version=1,
            contract_revision=1, design_id=design.id, design_name=design.name, metric='pae', status='ok', reason=None,
            document=_document(design, selected), producer_binding=selected['block'],
            artifact_sha256=selected['artifacts']['pae']['sha256'], row_axis=axis, column_axis=axis,
            native_row_positions=list(range(n)), native_column_positions=list(range(n)), native_shape=[n, n],
            sampled_row_indices=indexes, sampled_column_indices=indexes,
            pae_matrix=[[matrix[i][j] for j in indexes] for i in indexes], size=len(indexes))
    except _ERRORS:
        result = _unavailable(design, 'pae')
    payload = result.model_dump(mode='json')
    return payload, {'status': result.status, 'reason': result.reason}, None
