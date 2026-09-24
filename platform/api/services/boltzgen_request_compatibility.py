"""Known BoltzGen CSV/NPZ compatibility, before queue admission.

scripts/lib/filtering/evidence.py owns the native dialect mapping. Native pLDDT
is not supplied on this path; design_ptm is a distinct fraction, never its alias.
"""
import math
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import yaml

from pydantic import BaseModel, ConfigDict, Field


class BoltzGenRankWeights(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    design_ptm: float | None = Field(default=1., gt=0, allow_inf_nan=False)
    affinity_probability: float | None = Field(default=1., gt=0, allow_inf_nan=False)
    filter_rmsd: float | None = Field(default=1., gt=0, allow_inf_nan=False)


def compile_boltzgen_settings(params):
    result = dict(params)
    for key in ('min_plddt', 'boltzgen_min_plddt'):
        if result.get(key) is not None:
            raise ValueError('BoltzGen native pLDDT is unavailable on the known CSV/NPZ producer path; disable this threshold (null)')
    if re.search(r'\bplddt\s*[<>]', str(result.get('boltzgen_additional_filters') or '')):
        raise ValueError('BoltzGen native pLDDT is unavailable; additional pLDDT filters are incompatible')
    values = {}
    legacy = result.get('boltzgen_metrics_override')
    if legacy:
        if not isinstance(legacy, str):
            raise ValueError('metric override must be a string')
        for token in re.split(r'[\s,]+', legacy.strip()):
            try:
                name, raw = token.split('=')
                name = {'conf_score': 'affinity_probability', 'rmsd': 'filter_rmsd'}.get(name, name)
                if name not in BoltzGenRankWeights.model_fields or name in values:
                    raise ValueError('unknown or duplicate metric')
                values[name] = None if raw.lower() == 'none' else float(raw)
            except ValueError as exc:
                raise ValueError('unsupported BoltzGen rank override') from exc
    for name in BoltzGenRankWeights.model_fields:
        key = f'boltzgen_rank_{name}_weight'
        if key in result:
            value = result[key]
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
                raise ValueError('rank weights require finite positive numbers or null (disabled)')
            if name in values and value != values[name]:
                raise ValueError('conflicting typed and legacy rank weights')
            values[name] = value
    weights = BoltzGenRankWeights.model_validate(values).model_dump()
    result.update({f'boltzgen_rank_{name}_weight': weight for name, weight in weights.items()})
    result['boltzgen_metrics_override'] = ' '.join(f'{name}={"none" if weight is None else weight}' for name, weight in weights.items())
    result['boltzgen_effective_rank'] = [
        {'name': name, 'higher_is_better': name != 'filter_rmsd', 'weight': weight,
         'unit': 'angstrom' if name == 'filter_rmsd' else 'fraction'}
        for name, weight in weights.items() if weight is not None]
    return result


# Native protocol/entity mapping from boltzgen 617e549e, parse/schema.py and
# the vanilla-protein / linear-peptide examples. Peptides are protein entities.
BOLTZGEN_GENERATION_PROTOCOLS = {
    'protein_binder': 'protein-anything',
    'peptide_binder': 'peptide-anything',
    'nanobody_binder': 'nanobody-anything',
}

# The shared compiler imports this table instead of maintaining a second alias
# map. Legacy ligand/nucleotide aliases retain their existing interpretation.
BOLTZGEN_PARAMETER_ALIASES = {
    **{name: 'boltzgen_' + name for name in (
        'input_pdb', 'ligand_pdb', 'ligand_smiles', 'protein_sequence',
        'dna_template_seq', 'dna_primer_seq', 'dna_structure', 'scaffold_length',
        'num_designs', 'batch_size', 'ntp_type', 'binding_site_residues',
        'catalytic_site', 'budget', 'alpha', 'max_rmsd', 'min_plddt',
        'secondary_structure', 'protocol', 'target_chains', 'target_binding_positions', 'binder_sequence',
        'scaffold_path', 'scaffold_chain', 'scaffold_design_ranges',
        'nanobody_framework', 'cdr_h1_length', 'cdr_h2_length', 'cdr_h3_length',
        'diffusion_batch_size', 'step_scale', 'noise_scale', 'checkpoint_mode',
        'inverse_fold_avoid', 'inverse_fold_num_sequences', 'skip_inverse_folding',
        'reuse', 'min_conf_score', 'refolding_rmsd_threshold', 'filter_biased',
        'metrics_override', 'additional_filters', 'size_buckets', 'covalent_bonds',
        'yaml_config', 'use_framework_template', 'scaffold_source',
        'nanobody_scaffold_specs', 'rank_design_ptm_weight',
        'rank_affinity_probability_weight', 'rank_filter_rmsd_weight')},
    'target_pdb': 'boltzgen_target_pdb_path',
    'ligand_description': 'boltzgen_ligand_smiles',
}


class BoltzGenGenerationInputs(BaseModel):
    """Typed model-owned source inputs; the registry owns native run settings.

    Structural scaffold ranges use native 1-indexed chain-local positions, not
    antibody numbering. Generic generation never infers CDRs or a seed complex.
    """
    model_config = ConfigDict(extra='forbid', strict=True)
    target_pdb: str | None = None
    target_chains: str | None = None
    target_binding_positions: str | None = None
    binder_sequence: str | None = None
    scaffold_path: str | None = None
    scaffold_chain: str | None = None
    scaffold_design_ranges: str | None = None
    nanobody_framework: str | None = None
    cdr_h1_length: str | None = None
    cdr_h2_length: str | None = None
    cdr_h3_length: str | None = None


@lru_cache(maxsize=1)
def _generation_contract():
    path = Path(__file__).resolve().parents[1] / 'config/models/boltzgen.yaml'
    return yaml.safe_load(path.read_text())['native_generation']


def parameter_contract(mode: str) -> list[dict]:
    """Mode-owned typed discovery; never expand the legacy ligand defaults."""
    if mode not in BOLTZGEN_GENERATION_PROTOCOLS:
        raise ValueError('Unknown public BoltzGen generation mode: ' + str(mode))
    fields = []
    for definition in _generation_contract()['parameters']:
        if mode not in definition['modes']:
            continue
        field = deepcopy(definition)
        field['default'] = field.get('default_by_mode', {}).get(mode, field['default'])
        field['aliases'] = [key for key, value in BOLTZGEN_PARAMETER_ALIASES.items()
                            if value == field['name']]
        field['native_mapping'] = ('target_pdb' if field['name'] == 'boltzgen_target_pdb_path'
                                   else field['name'].removeprefix('boltzgen_'))
        field['authority'] = 'operator'
        field['control'] = {'boolean': 'checkbox', 'integer': 'number',
                            'number': 'number', 'file': 'structural-source',
                            'object': 'source-selector', 'string': 'text'}[field['type']]
        if 'enum' in field:
            field['control'] = 'select'
        fields.append(field)
    return fields


def boltzgen_generation_inventory(mode: str) -> dict:
    contract = _generation_contract()
    return {'schema_version': contract['schema_version'], 'model_id': 'boltzgen',
            'source_revision': contract['source_revision'], 'mode': mode,
            'parameters': parameter_contract(mode),
            'native_behavior': {
                'defaults': 'Null optional run overrides defer to the native protocol, not antibody-form recommendations.',
                'plddt': 'Unavailable in the native CSV/NPZ producer; explicit non-null thresholds are incompatible.',
                'aliases': 'Equal aliases accepted; conflicting names rejected. Explicit values precede defaults.',
                'batch_size': 'Non-null diffusion_batch_size precedes batch_size.',
                'rmsd': 'Non-null refolding_rmsd_threshold precedes max_rmsd.'}}


# These are controller/compiler receipts, not scientific controls or permission
# to trust user-supplied identities. Their existing owning boundaries verify them.
BOLTZGEN_GENERATION_METADATA_FIELDS = frozenset({
    'boltzgen_mode', 'boltzgen_generation_mode', 'boltzgen_prepared_sha256',
    'boltzgen_prepared_identity', 'boltzgen_scaffold_source_resolved',
    'boltzgen_effective_rank', 'core_protein_scientific_contract',
})


def _validate_generation_field(field, value):
    name, kind = field['name'], field['type']
    if value is None:
        if not field['nullable']:
            raise ValueError(name + ' cannot be null')
        return
    valid = {'boolean': type(value) is bool, 'integer': type(value) is int,
             'number': type(value) in (int, float), 'string': isinstance(value, str),
             'file': isinstance(value, str), 'object': isinstance(value, dict)}[kind]
    if 'array' in field.get('accepted_types', ()) and isinstance(value, list):
        valid = True
    if not valid:
        raise ValueError(name + ' must be ' + kind)
    if kind == 'number' and not math.isfinite(value):
        raise ValueError(name + ' must be finite')
    if 'enum' in field and value not in field['enum']:
        raise ValueError(name + ' must be one of ' + str(field['enum']))
    for bound, comparison in [('minimum', lambda x, y: x < y),
                              ('maximum', lambda x, y: x > y),
                              ('exclusiveMinimum', lambda x, y: x <= y)]:
        if bound in field and comparison(value, field[bound]):
            raise ValueError(name + ' violates ' + bound)


def normalize_boltzgen_generation_request(mode, params):
    """Call before registry defaults, for the three public generation modes only.

    Preserve explicit native protocol choices (including auto), numeric zeros,
    false and null. This is source/alias normalization, not a readiness gate.
    Legacy ligand/nucleotide and internal child requests do not use this adapter.
    """
    if mode not in BOLTZGEN_GENERATION_PROTOCOLS:
        raise ValueError('Unknown public BoltzGen generation mode: ' + str(mode))
    fields = parameter_contract(mode)
    by_name = {field['name']: field for field in fields}
    result = deepcopy(params)
    # Validate both spellings, even where Python considers False equal to zero.
    for key, value in result.items():
        canonical = BOLTZGEN_PARAMETER_ALIASES.get(key, key)
        if canonical in by_name:
            _validate_generation_field(by_name[canonical], value)
    for source, destination in BOLTZGEN_PARAMETER_ALIASES.items():
        if source in result:
            value = result.pop(source)
            if destination in result and result[destination] != value:
                raise ValueError('Conflicting BoltzGen aliases: ' + source)
            result[destination] = value
    unknown = set(result) - {field['name'] for field in fields} - BOLTZGEN_GENERATION_METADATA_FIELDS
    if unknown:
        raise ValueError('Unknown or inapplicable BoltzGen settings for ' + mode + ': ' + ', '.join(sorted(unknown)))
    for key in ('boltzgen_mode', 'boltzgen_generation_mode'):
        if key in result and result[key] != mode:
            raise ValueError('Conflicting BoltzGen mode: ' + key)
    # Resolve explicitly supplied legacy weights BEFORE injecting typed defaults.
    # Otherwise a saved override would conflict with a newly injected weight=1.
    rank = compile_boltzgen_settings(result)
    for name in BoltzGenRankWeights.model_fields:
        key = 'boltzgen_rank_' + name + '_weight'
        result[key] = rank[key]
    for field in fields:
        result.setdefault(field['name'], deepcopy(field['default']))
        _validate_generation_field(field, result[field['name']])
    typed = {name: result[BOLTZGEN_PARAMETER_ALIASES[name]]
             for name in BoltzGenGenerationInputs.model_fields
             if BOLTZGEN_PARAMETER_ALIASES[name] in result}
    inputs = BoltzGenGenerationInputs.model_validate(typed)
    if inputs.target_binding_positions and result.get('boltzgen_binding_site_residues'):
        raise ValueError('Choose native target positions or legacy author-PDB binding residues, not both')
    if mode != 'nanobody_binder':
        if inputs.nanobody_framework or result.get('boltzgen_nanobody_scaffold_specs') or result.get('boltzgen_use_framework_template'):
            raise ValueError('VHH framework inputs belong to nanobody_binder, not generic binder generation')
        if inputs.scaffold_path and inputs.binder_sequence:
            raise ValueError('Choose a structural scaffold or a binder sequence template')
        if inputs.scaffold_path and not (inputs.scaffold_chain and inputs.scaffold_design_ranges):
            raise ValueError('A structural scaffold requires its native chain and design ranges')
    elif any((inputs.binder_sequence, inputs.scaffold_path, inputs.scaffold_chain, inputs.scaffold_design_ranges)):
        raise ValueError('nanobody_binder uses the existing VHH framework/scaffold inputs')
    result.setdefault('boltzgen_protocol', BOLTZGEN_GENERATION_PROTOCOLS[mode])
    result['boltzgen_mode'] = mode
    result['boltzgen_generation_mode'] = mode
    return result
