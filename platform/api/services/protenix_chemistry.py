"""Bind ordinary Protenix file selections to compiler-owned input artifacts."""
from copy import deepcopy
from pathlib import Path


def resolve_ligand_file(value):
    from paths import resolve_allowed_path
    if Path(value).is_absolute():
        raise ValueError('ligand_file requires a managed file selection, not a runtime path')
    source = resolve_allowed_path(value)
    if not source.is_file() or source.suffix.lower() not in ('.mol', '.mol2', '.sdf', '.pdb'):
        raise ValueError('ligand_file must select an existing managed mol/mol2/sdf/pdb file')
    return source


def validate_component_files(params):
    for component in params.get('complex_components') or []:
        if component.get('ligand_file'):
            resolve_ligand_file(component['ligand_file'])


def stage_component_files(params, *, output_dir, write_input):
    params = deepcopy(params)
    for index, component in enumerate(params.get('complex_components') or []):
        if component.get('ligand_file'):
            source = resolve_ligand_file(component['ligand_file'])
            destination = Path(output_dir) / 'protenix_ligands' / f'{index + 1}{source.suffix.lower()}'
            write_input(destination, source.read_bytes())
            component['ligand_file'] = str(destination)
    return params
