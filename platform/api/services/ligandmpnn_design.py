"""Ordinary Foundry LigandMPNN requests and native publication (not diagnostics).

All four product modes use the same ligand_mpnn model with the supplied complex
coordinates. Labels/SMILES are retained annotations, never geometry generators.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MODES = frozenset({'ligand_aware', 'ntp_aware', 'metal_aware', 'dna_aware'})
CONTRACT = 'ligandmpnn_design.v1'
CHECKPOINT = '/foundry/checkpoints/ligandmpnn_v_32_010_25.pt'
# Exact native author-ID grammar: preserve case/insertion codes, never renumber.
ResidueId = Annotated[str, Field(pattern=r'^[A-Za-z]+[0-9]+[A-Za-z]*$')]
ChainId = Annotated[str, Field(pattern=r'^[A-Za-z]+$')]
Token = Literal['ALA', 'CYS', 'ASP', 'GLU', 'PHE', 'GLY', 'HIS', 'ILE',
                'LYS', 'LEU', 'MET', 'ASN', 'PRO', 'GLN', 'ARG', 'SER',
                'THR', 'VAL', 'TRP', 'TYR', 'UNK']


class NativeOptions(BaseModel):
    """rc-foundry 0.1.9 MPNN per-input controls, with native defaults.

    features_to_return is engine-internal tensor selection, not a published
    output control; repeat_sample_num is native-derived from batch_size.
    undesired_res_names is training-only in this pinned inference pipeline.
    """
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    seed: int | None = None
    batch_size: int = Field(default=1, gt=0)
    number_of_batches: int = Field(default=1, gt=0)
    remove_ccds: list[str] | None = Field(default_factory=list)
    remove_waters: bool | None = None
    occupancy_threshold_sidechain: float | None = 0.0
    occupancy_threshold_backbone: float | None = 0.0
    structure_noise: float | None = 0.0
    decode_type: Literal['auto_regressive', 'teacher_forcing'] | None = 'auto_regressive'
    causality_pattern: Literal['auto_regressive', 'unconditional', 'conditional', 'conditional_minus_self'] | None = 'auto_regressive'
    initialize_sequence_embedding_with_ground_truth: bool | None = False
    atomize_side_chains: bool | None = False
    fixed_residues: list[ResidueId] | None = None
    designed_residues: list[ResidueId] | None = None
    fixed_chains: list[ChainId] | None = None
    designed_chains: list[ChainId] | None = None
    bias: dict[Token, float] | None = None
    bias_per_residue: dict[ResidueId, dict[Token, float]] | None = None
    omit: list[Token] | None = Field(default_factory=lambda: ['UNK'])
    omit_per_residue: dict[ResidueId, list[Token]] | None = None
    pair_bias: dict[Token, dict[Token, float]] | None = None
    pair_bias_per_residue_pair: dict[ResidueId, dict[ResidueId, dict[Token, dict[Token, float]]]] | None = None
    temperature: float | None = 0.1
    temperature_per_residue: dict[ResidueId, float] | None = None
    symmetry_residues: list[list[ResidueId]] | None = None
    symmetry_residues_weights: list[list[float]] | None = None
    homo_oligomer_chains: list[list[ChainId]] | None = None

    @model_validator(mode='after')
    def native_combinations(self):
        for fixed, designed in ((self.fixed_residues, self.designed_residues),
                                (self.fixed_chains, self.designed_chains)):
            if fixed is not None and designed is not None:
                raise ValueError('Native fixed and designed scopes are mutually exclusive')
        if (self.fixed_residues or self.designed_residues) and (self.fixed_chains or self.designed_chains):
            raise ValueError('Native residue and chain scopes cannot be mixed')
        if self.symmetry_residues is not None and self.homo_oligomer_chains is not None:
            raise ValueError('Native residue and homo-oligomer symmetry are mutually exclusive')
        if self.symmetry_residues_weights is not None:
            if self.symmetry_residues is None or list(map(len, self.symmetry_residues)) != list(map(len, self.symmetry_residues_weights)):
                raise ValueError('Native symmetry weights must match residue groups')
        if self.homo_oligomer_chains is not None and any(len(g) < 2 for g in self.homo_oligomer_chains):
            raise ValueError('Native homo-oligomer groups require at least two chains')
        return self


ANNOTATIONS = ('ligand_smiles', 'ntp_type', 'metal_type', 'dna_sequence')


def normalize_design_params(mode: str, params: dict) -> dict:
    """Call from the existing Jobs normalizer, before generic design defaults.

    Old source/label keys remain intact. A separate ligand document is not an
    assembly instruction; a complete coordinate-bearing complex is required.
    """
    if mode not in MODES:
        raise ValueError('Not an ordinary LigandMPNN design mode')
    native_keys = set(NativeOptions.model_fields) - {'seed'}
    allowed = native_keys | {'design_seed', 'target_pdb', 'ligand_pdb', *ANNOTATIONS,
                             'write_fasta', 'write_structures'}
    unknown = set(params) - allowed
    if unknown:
        raise ValueError(f'Unknown LigandMPNN design settings: {sorted(unknown)}')
    result = copy.deepcopy(params)
    target, ligand = result.get('target_pdb'), result.get('ligand_pdb')
    if target and ligand and target != ligand:
        raise ValueError('Supply one assembled protein-context structure; separate ligand_pdb and target_pdb cannot be positioned automatically')
    source = target or ligand
    if not isinstance(source, str) or not source.strip():
        raise ValueError('A protein-context PDB/mmCIF is required; labels/SMILES are not coordinates')
    result['target_pdb'] = source
    for key in ANNOTATIONS:
        if key in result and result[key] is not None and not isinstance(result[key], str):
            raise ValueError(f'{key} must be a descriptive string')
    native = NativeOptions.model_validate({**{k: result[k] for k in native_keys if k in result},
                                           'seed': result.get('design_seed')}).model_dump()
    result.update({k: v for k, v in native.items() if k != 'seed'})
    result['design_seed'] = native['seed']
    for key in ('write_fasta', 'write_structures'):
        result.setdefault(key, True)
        if type(result[key]) is not bool:
            raise ValueError(f'{key} must be boolean')
    return result


def prepare_design_request(mode: str, params: dict) -> dict:
    """Compiler payload; source is transported separately, not inside this JSON."""
    values = normalize_design_params(mode, params)
    options = {k: values[k] for k in NativeOptions.model_fields if k != 'seed'}
    options['seed'] = values['design_seed']
    return {'contract': CONTRACT, 'mode': mode, 'model_type': 'ligand_mpnn',
            'options': options,
            'annotations': {k: values[k] for k in ANNOTATIONS if k in values},
            'write_fasta': values['write_fasta'], 'write_structures': values['write_structures']}


def parameter_schema(mode: str) -> dict:
    """Expose the native nested schema using the public scientific key names."""
    if mode not in MODES:
        raise ValueError('Not an ordinary LigandMPNN design mode')
    schema = NativeOptions.model_json_schema()
    props = schema['properties']
    props['design_seed'] = props.pop('seed')
    # Source/annotation/output names are owned beside normalization, not in a
    # second registry schema. Cross-field native rules remain with that owner.
    source = {'anyOf': [{'type': 'string', 'pattern': r'\S'}, {'type': 'null'}]}
    props.update({key: copy.deepcopy(source) for key in ('target_pdb', 'ligand_pdb')})
    props.update({key: {'anyOf': [{'type': 'string'}, {'type': 'null'}]} for key in ANNOTATIONS})
    props.update({key: {'type': 'boolean', 'default': True} for key in ('write_fasta', 'write_structures')})
    schema['anyOf'] = [{'required': [key], 'properties': {key: {'type': 'string', 'pattern': r'\S'}}}
                       for key in ('target_pdb', 'ligand_pdb')]
    schema['title'] = 'Ordinary LigandMPNN scientific parameters'
    schema['x-native-contract'] = CONTRACT
    schema['x-native-mode'] = mode
    schema['x-semantic-validation'] = 'services.ligandmpnn_design:normalize_design_params'
    return schema


def science_params(mode: str, params: dict) -> dict:
    """Project scientific settings after the caller admits framework fields."""
    names = (set(NativeOptions.model_fields) - {'seed'}) | {
        'design_seed', 'target_pdb', 'ligand_pdb', *ANNOTATIONS, 'write_fasta', 'write_structures'}
    return normalize_design_params(mode, {key: value for key, value in params.items() if key in names})


def read_prepared_request(mode: str, params: dict, *, allowed_roots=None) -> dict:
    """Compare retained science/source bytes without reopening provenance paths."""
    from scripts.lib.portable_inputs import _contained
    request = Path(params['ligandmpnn_design_request'])
    source = Path(params['ligandmpnn_design_input'])
    roots = ([Path(p).resolve() for p in allowed_roots] if allowed_roots is not None
             else [request.parent.resolve()])
    request, source = _contained(request, roots), _contained(source, roots)
    document = json.loads(request.read_bytes())
    science = science_params(mode, params)
    expected = prepare_design_request(mode, science)
    expected['source'] = {'requested_path': science['target_pdb'],
                          'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    if document != expected:
        raise ValueError('LigandMPNN retained request or source differs from selected settings')
    return document


def prepare_for_job(mode: str, params: dict, directory: str | Path, *, allowed_roots,
                    retain_prepared: bool = False) -> dict:
    """Use existing input custody and retain the reviewed native files unchanged."""
    import shutil
    from scripts.lib.portable_inputs import _contained
    roots = [Path(p).resolve() for p in allowed_roots]
    fields = ('ligandmpnn_design_request', 'ligandmpnn_design_input')
    retained = any(params.get(key) for key in fields)
    if retained:
        if not all(params.get(key) for key in fields):
            raise ValueError('LigandMPNN prepared request and source must travel together')
        document = read_prepared_request(mode, params, allowed_roots=roots)
        source = Path(params[fields[1]])
        if retain_prepared or Path(params[fields[0]]).parent.resolve() == Path(directory).resolve():
            return {key: str(Path(params[key]).resolve()) for key in fields}
    else:
        science = science_params(mode, params)
        source = _contained(Path(science['target_pdb']), roots)
        document = prepare_design_request(mode, science)
    destination = Path(directory)
    ancestor = destination
    while not ancestor.exists() and not ancestor.is_symlink():
        ancestor = ancestor.parent
    _contained(ancestor, roots)
    if source.suffix.lower() not in {'.pdb', '.cif', '.mmcif'}:
        raise ValueError('LigandMPNN requires a PDB/mmCIF structure')
    destination.mkdir(parents=True, exist_ok=False)
    owned = destination / ('source' + source.suffix.lower())
    if retained:
        shutil.copyfile(source, owned)
        shutil.copyfile(params[fields[0]], destination / 'request.json')
    else:
        data = source.read_bytes()
        owned.write_bytes(data)
        document['source'] = {'requested_path': science['target_pdb'],
                              'sha256': hashlib.sha256(data).hexdigest()}
        (destination / 'request.json').write_text(json.dumps(document, sort_keys=True, allow_nan=False) + '\n')
    return {fields[0]: str((destination / 'request.json').resolve()), fields[1]: str(owned.resolve())}


def result_contract(mode: str) -> dict:
    if mode not in MODES:
        raise ValueError('Not an ordinary LigandMPNN design mode')
    return {'native_contract_authority': 'platform/api/services/ligandmpnn_design.py:read_design_result',
            'contract': CONTRACT, 'mode': mode, 'relative_output_dir': 'ligandmpnn_design',
            'primary_document': 'manifest.json'}


def selected_assets() -> tuple[dict, ...]:
    """Installed checkpoint is embedded in this existing managed image."""
    return ({'kind': 'image', 'relative_path': 'foundry.sif'},)


def read_design_result(output_root: str | Path) -> dict:
    """Read returned model bytes; caller registers artifacts with existing Jobs.

    No Design/PDB placeholder, universal score or ordinal-to-source join.
    A native sequence result can legitimately have no structure output.
    """
    root = Path(output_root).resolve()
    document = json.loads((root / 'manifest.json').read_text())
    if document.get('contract') != CONTRACT or document.get('model_type') != 'ligand_mpnn' or document.get('mode') not in MODES:
        raise ValueError('Not an ordinary LigandMPNN publication')
    seen = set()
    for row in document['records']:
        key = row['producer']
        identity = (key['name'], key['batch_idx'], key['design_idx'])
        if identity in seen:
            raise ValueError('Duplicate native producer identity')
        seen.add(identity)
        native = row['native_output']
        if native['model_type'] != 'ligand_mpnn' or any(native[k] != key[k] for k in ('batch_idx', 'design_idx')) or row['native_input']['name'] != key['name']:
            raise ValueError('Native producer identity mismatch')
        for artifact in row['artifacts']:
            relative = Path(artifact['path'])
            path = (root / relative).resolve()
            if relative.is_absolute() or not path.is_relative_to(root):
                raise ValueError('Artifact escapes LigandMPNN publication')
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != artifact['sha256']:
                raise ValueError('LigandMPNN artifact digest mismatch')
    return document
