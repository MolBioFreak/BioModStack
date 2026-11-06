"""Native discovery is informational, never a new MDP allowlist."""
import asyncio
import pytest
from scripts.bms_md.native_parameters import native_parameter_catalog
from scripts.bms_md.native_config import parse_mdp, render_mdp

CATALOG = native_parameter_catalog()


@pytest.mark.parametrize('row', CATALOG['parameters'], ids=lambda row: row['key'])
def test_catalog_row_emits_without_inserting_native_defaults(row):
    assert row['type'] in {'number', 'string', 'enum', 'number-list', 'enum-list'}
    assert row['authority'] == 'operator'
    assert row['native_mapping'] == 'stages[].mdp.' + row['key']
    assert row['documentation'].startswith('https://manual.gromacs.org/2025.3/')
    value = (row['choices'][-1] if row['choices'] else '0' if row['type'] == 'number'
             else row['list_separator'].join(['0', '1', '0']) if row['type'] == 'number-list' else '')
    requested = {row['key']: value, 'Unknown_Mixed-Case': 'unchanged', 'gen-seed': '-1'}
    assert parse_mdp(render_mdp(requested)) == requested
    assert render_mdp({}) == ''


def test_discovery_route_returns_same_inventory_without_shared_mutability(tmp_path, monkeypatch):
    # This standalone router test does not exercise unrelated model discovery.
    # Keep its import-time Project dependencies in a task-private catalog.
    import shutil
    from pathlib import Path
    import model_registry
    models = tmp_path / 'models'; models.mkdir()
    for model in ('fampnn', 'proteinmpnn'):
        shutil.copyfile(Path(model_registry.__file__).parent / 'config/models' / f'{model}.yaml', models / f'{model}.yaml')
    registry = model_registry.ModelRegistry(config_dir=models)
    monkeypatch.setattr(model_registry, 'get_registry', lambda: registry)
    from routers.molecular_dynamics import get_native_parameters
    assert asyncio.run(get_native_parameters()) == CATALOG
    first = native_parameter_catalog(); first['parameters'].clear()
    assert native_parameter_catalog() == CATALOG
    assert len({row['key'] for row in CATALOG['parameters']}) == len(CATALOG['parameters'])
