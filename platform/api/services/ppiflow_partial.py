"""Dependency-light task-specific defaults and typed native contract discovery."""
from copy import deepcopy
from pathlib import Path
import yaml

MODEL_YAML = Path(__file__).resolve().parents[1] / 'config/models/binder_refinement.yaml'


def partial_parameter_contract(task):
    fields = yaml.safe_load(MODEL_YAML.read_text())['params']
    protein = task == 'protein_binder_partial'
    result = []
    for field in fields:
        if protein and field.get('protein_native_cli') or protein and field.get('protein_native_path'):
            field = deepcopy(field)
            field['default'] = field.get('protein_default', field.get('default'))
            result.append(field)
    return result


def apply_partial_defaults(params):
    """Only absent values default. Saved values and independent stage flags survive."""
    task = params.get('ppiflow_task') or ('nanobody_partial' if params.get('framework_type') == 'nanobody' else 'antibody_partial')
    for field in yaml.safe_load(MODEL_YAML.read_text())['params']:
        defaults = field.get('defaults_by_task', {})
        if task in defaults:
            params.setdefault(field['name'], deepcopy(defaults[task]))
    if task == 'protein_binder_partial':
        for field in partial_parameter_contract(task):
            if field.get('default') is not None:
                params.setdefault(field['name'], deepcopy(field['default']))
    return params
