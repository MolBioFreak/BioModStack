"""Admission declarations: biological membership, never future artifact evidence.

Resolution against transformed inputs belongs to the preparation adapter. This
module intentionally does not predict producer candidate IDs or hash future PDBs.
"""
from copy import deepcopy
from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field, StrictInt

DECLARATION_KEY = 'fampnn_analysis_declaration'
POLICY_KEY = 'fampnn_analysis_policy'

ANTIBODY_SETTINGS = {
    'framework_type': 'standard-fv', 'framework_pdb': None,
    'antibody_design_mode': 'cdr_only', 'antibody_design_loops': 'H1,H2,H3,L1,L2,L3',
    'protect_vhh_tetrad': True, 'lock_antibody_framework': True, 'lock_target_chains': True,
}


def declaration_dependencies(declaration):
    """Inputs consumed by the declaration and its physical materialization owner.

    Resource placement is deliberately not biological authority. Both direct
    selectors and deferred source/normalization controls must survive cached reuse.
    """
    keys = {'input_pdb', 'pdb_paths', 'design_chain', 'target_chain',
            'fixed_positions', 'fampnn_analysis_overrides'}
    if declaration.get('owner') == 'antibody_denovo':
        keys.update(ANTIBODY_SETTINGS)
        keys.update({'target_pdb', 'antigen_chains', 'target_model_number',
            'antibody_chains', 'cdr_positions', 'rfantibody_design_loops',
            'rfantibody_design_loops_custom', 'rfantibody_loop_length_ranges',
            'epitope_residues', 'backbone_method', 'seq_method', 'seq_design_fampnn',
            'selected_input_artifact_class', 'selected_input_schema_version',
            'selected_input_dir', 'iteration_selection_dir', 'rfantibody_input_pdbs',
            'fampnn_collected_pdbs', 'selected_input_manifest', 'source_selection_manifest_path',
            'selected_input_source_job_id', 'source_stage_job_id', 'selection_source_job_id',
            'iteration_source_job_id', 'selected_input_stage_family', 'source_stage_family',
            'selected_input_stage_mode', 'source_stage_mode', 'iteration_source_design_ids',
            'source_selection_count', 'selected_loop_scope', 'skip_rfantibody',
            'input_dir', 'pdb_dir', 'source_dir', 'fampnn_constraint_mode',
            'manual_mutation_fixed_positions_json'})
    if declaration.get('owner') == 'protein_local_redesign':
        local = {'input_pdb', 'design_chains', 'model_number', 'region_mode',
                 'redesign_ranges', 'context_chains', 'interface_cutoff',
                 'region_padding', 'sequence_redesign_ranges', 'seq_method'}
        keys.update(local)
        keys.update('plr_' + key for key in local)
        keys.add('rfd3_request')
    return keys


def guard_cached_declaration(declaration, original, overrides):
    for key in declaration_dependencies(declaration):
        default = ANTIBODY_SETTINGS.get(key) if declaration.get('owner') == 'antibody_denovo' else None
        if key in overrides and overrides[key] != original.get(key, default):
            raise ValueError('FA-MPNN biological changes require fresh admission, not cached resume')


class FampnnResidueSelector(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    chain_id: str = Field(min_length=1, max_length=1, pattern=r'^[^:\s]$')
    author_number: StrictInt
    insertion_code: str = Field(default='', max_length=1, pattern=r'^[^:\s]?$')

    def identity(self):
        return f'{self.chain_id}:{self.author_number}:{self.insertion_code}'


class FampnnAnalysisOverrides(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    summary: list[FampnnResidueSelector] | None = None
    mutation: list[FampnnResidueSelector] | None = None

    def identities(self, field):
        selectors = getattr(self, field)
        if selectors is None:
            return None
        result = [item.identity() for item in selectors]
        if len(set(result)) != len(result):
            raise ValueError('duplicate FA-MPNN override residue')
        return result


def _domain(path):
    # Admission reads only author identities. It makes no correspondence claim
    # about the eventual PrepFAMPNN output; that requires transform provenance.
    identities = []
    for line in Path(path).read_text().splitlines():
        if line.startswith('ATOM  '):
            if len(line) < 54:
                raise ValueError('malformed input PDB')
            identity = f'{line[21]}:{int(line[22:26])}:{line[26].strip()}'
            if identity not in identities:
                identities.append(identity)
    if not identities:
        raise ValueError('FA-MPNN input has no protein residue identities')
    return identities


def _fixed_positions(spec, domain):
    fixed = set()
    if not spec:
        return fixed
    if not isinstance(spec, str):
        raise ValueError('fixed_positions requires a residue specification')
    for token in spec.split(','):
        match = re.fullmatch(r'([^:\s]):(-?\d+)(?:-(-?\d+))?', token.strip())
        if not match:
            raise ValueError('unresolved FA-MPNN fixed_positions specification')
        chain, first, last = match.groups()
        first, last = int(first), int(last or first)
        selected = {identity for identity in domain if identity.split(':')[0] == chain
                    and first <= int(identity.split(':')[1]) <= last}
        if first > last or not selected:
            raise ValueError('fixed_positions outside input domain')
        fixed.update(selected)
    return fixed


def overrides_from_declaration(declaration):
    """Recover typed operator values from trusted persisted authority for retry."""
    if declaration is None:
        return None
    result = {}
    for field in ('summary', 'mutation'):
        values = declaration[field + '_override']
        result[field] = None if values is None else [dict(chain_id=v.split(':')[0],
            author_number=int(v.split(':')[1]), insertion_code=v.split(':')[2]) for v in values]
    return result


def _local_region(params):
    # Use the same source-owned selector as ResolveProteinLocalRegions. No
    # manifest, generated PDB, correspondence or hash is predicted here.
    from services import aligned_error_utils  # shared scripts import owner
    from resolve_redesign_regions import (select_structure_lines, parse_pdb_residues_from_lines,
        parse_chain_list, parse_manual_ranges, pick_interface_shell, residue_numbers_from_indices)
    source = params.get('input_pdb') or params.get('plr_input_pdb')
    def setting(name, default=None):
        ordinary, native = params.get(name), params.get('plr_' + name)
        if ordinary is not None and native is not None and ordinary != native:
            raise ValueError(f'conflicting local redesign {name}')
        return ordinary if ordinary is not None else native if native is not None else default
    chains = parse_chain_list(setting('design_chains', ''))
    if len(chains) != 1:
        raise ValueError('local redesign requires one declared design chain')
    lines, _ = select_structure_lines(Path(source), setting('model_number'))
    records = parse_pdb_residues_from_lines([line for line in lines if line.startswith('ATOM  ')])
    chain = chains[0]
    if chain not in records:
        raise ValueError('local redesign chain outside input domain')
    residues = records[chain]
    available = {r.key.resnum for r in residues}
    region_mode = setting('region_mode', 'manual_ranges')
    if region_mode == 'manual_ranges':
        movable = parse_manual_ranges(setting('redesign_ranges', ''), chain, available)
    elif region_mode == 'interface_shell':
        context = parse_chain_list(setting('context_chains', ''))
        if not set(context) <= set(records):
            raise ValueError('local redesign context outside input domain')
        indices = pick_interface_shell(residues, [r for c in context for r in records[c]],
            cutoff=float(setting('interface_cutoff', 6.0)), padding=max(0, int(setting('region_padding', 2))))
        movable = residue_numbers_from_indices(residues, indices)
    else:
        raise ValueError('unsupported local redesign region mode')
    sequence = setting('sequence_redesign_ranges', '')
    selected = parse_manual_ranges(sequence, chain, available) if sequence else movable
    if not selected <= movable:
        raise ValueError('sequence redesign outside coordinate-edit region')
    domain = [f'{r.key.chain}:{r.key.resnum}:{r.key.icode}' for group in records.values() for r in group]
    authorized = [f'{r.key.chain}:{r.key.resnum}:{r.key.icode}' for r in residues if r.key.resnum in selected]
    return domain, authorized


def compile_declaration(model_id, mode, params, overrides=None, *, parent=None):
    overrides = FampnnAnalysisOverrides.model_validate(overrides or {})
    if model_id == 'rfdiffusion':
        raise ValueError('legacy generated FA-MPNN caller held/unadmitted')
    if model_id == 'antibody_denovo' and mode == 'antibody_denovo_pipeline' and params.get('seq_design_fampnn', True):
        if overrides.summary is not None:
            raise ValueError('summary override forbidden by antibody declaration')
        from services import aligned_error_utils
        from antibody_fampnn_provenance import admission_inputs
        return dict(schema_version=1, owner='antibody_denovo', version=1,
            declaration='authorized_sequence_design_region', input_domain=None,
            sequence_design=None, summary=None, fixed=None, summary_override=None,
            mutation_override=overrides.identities('mutation'), allow_summary_override=False,
            require_full_coverage=False, materialization=dict(
                source='rfantibody_native_export_v1', summary='authorized_antibody_domain',
                mutation='resolved_cdrs', inputs=admission_inputs(params), settings={key: deepcopy(params.get(key, default))
                    for key, default in ANTIBODY_SETTINGS.items()}))
    if model_id == 'fampnn_child':
        declaration = (getattr(parent, 'provenance', None) or {}).get(DECLARATION_KEY)
        if not isinstance(declaration, dict):
            raise ValueError('FA-MPNN child requires trusted parent declaration')
        supported = {'protein_design': {'binder_role_residues', 'declared_protein_inputs'},
                     'protein_local_redesign': {'sequence_redesign_positions_spec'},
                     'antibody_denovo': {'authorized_sequence_design_region'}}
        if declaration.get('declaration') not in supported.get(declaration.get('owner'), set()):
            raise ValueError('unsupported parent FA-MPNN declaration')
        declaration = deepcopy(declaration)
        # Child overrides cannot replace parent biological authority.
        for field in ('summary', 'mutation'):
            value = overrides.identities(field)
            if value is not None:
                if field == 'summary' and not declaration['allow_summary_override']:
                    raise ValueError('summary override forbidden by parent declaration')
                if field == 'mutation' and 'materialization' in declaration:
                    inherited = declaration['mutation_override']
                    if inherited is not None and not set(value) <= set(inherited):
                        raise ValueError('mutation override outside inherited domain')
                    declaration['mutation_override'] = value
                    continue
                domain = declaration['input_domain'] if field == 'summary' else (
                    declaration['mutation_override'] if declaration['mutation_override'] is not None
                    else declaration['sequence_design'])
                permitted = set(domain) - (set(declaration['fixed']) if field == 'mutation' else set())
                if not set(value) <= permitted:
                    raise ValueError(f'{field} override outside inherited domain')
                declaration[field + '_override'] = value
        if 'materialization' in declaration and params.get('pdb_paths'):
            import json
            from services import aligned_error_utils  # shared scripts import owner
            from fampnn_policy_resolution import resolve_declaration
            from antibody_fampnn_provenance import verify_parent_preparation, read_export
            origins = []
            for value in params['pdb_paths'].split(','):
                source = Path(value.strip())
                receipt = json.loads(source.with_suffix('.fampnn_prep.json').read_text())
                verify_parent_preparation(parent, source, receipt)
                resolve_declaration(declaration, {source.stem: receipt}, source.parent)
                proof = read_export(source)
                origins.append((proof.get('native_origin') or {}).get('sha256', receipt['source_pdb_sha256']))
            declaration['materialization']['origins'] = sorted(set(origins))
        return declaration
    local = (model_id, mode) == ('protein_modification_experimental', 'region_redesign') and not params.get('rfd3_request') and params.get('seq_method', params.get('plr_seq_method', 'fampnn')) == 'fampnn'
    if model_id != 'fampnn' and not local:
        if overrides.summary is not None or overrides.mutation is not None:
            raise ValueError('FA-MPNN declaration owner unresolved for this caller')
        return None
    if local:
        domain, authorized = _local_region(params)
        fixed = set(domain) - set(authorized)
        summary = authorized
    else:
        domain = _domain(params['input_pdb'])
        chains = {s.strip() for s in str(params.get('design_chain') or 'A').split(',')}
        if not chains <= {s.split(':')[0] for s in domain}:
            raise ValueError('design_chain outside input domain')
        authorized = [s for s in domain if s.split(':')[0] in chains]
        fixed = _fixed_positions(params.get('fixed_positions'), domain)
        summary = authorized if mode == 'binder_design' else domain
    summary_override = overrides.identities('summary')
    mutation_override = overrides.identities('mutation')
    if summary_override is not None and not set(summary_override) <= set(domain):
        raise ValueError('summary override outside input domain')
    if mutation_override is not None and not set(mutation_override) <= set(authorized) - fixed:
        raise ValueError('mutation override outside authorized nonfixed domain')
    return dict(schema_version=1, owner='protein_local_redesign' if local else 'protein_design', version=1,
                declaration='sequence_redesign_positions_spec' if local else ('binder_role_residues' if mode == 'binder_design' else 'declared_protein_inputs'),
                input_domain=domain, sequence_design=authorized, summary=summary,
                fixed=sorted(fixed), summary_override=summary_override,
                mutation_override=mutation_override, allow_summary_override=True,
                require_full_coverage=False)
