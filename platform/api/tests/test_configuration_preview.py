"""Strict input and non-mutating clean-install candidate contract."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import biomodstack_install_document as install
import biomodstack_runtime_profile as profiles


def document(profile=None, ingress=None):
    return {"schema_version": "bms.install.v1", "profile": profile or {},
            "ingress": ingress or {"mode": "local-only"}}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(('BMS_', 'XDG_')) or key in {'DATABASE_URL', 'PYTHONPATH'}:
            monkeypatch.delenv(key)
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    return tmp_path


def snapshot(path):
    result = {}
    for base, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in {'.venv', '.git'}]
        for name in dirs + files:
            p = Path(base) / name
            result[str(p.relative_to(path))] = (p.lstat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
    return result


@pytest.mark.parametrize('profile', [
    {'unknown': 1}, {'features': {'unknown': True}}, {'features': {'BioXP': True}},
    {'features': {'bioxp': 'false'}}, {'features': []}, {'features': None},
    {'data_root': 12}, {'data_root': None}, {'data_root': []}, {'data_root': ''},
    {'data_root': 'relative'}, {'data_root': '~/data'}, {'data_root': '/a/../b'},
    {'data_root': '/a\nnew'}, {'api_host_port': '18000'}, {'dev_api_host_port': True},
    {'dev_api_host_port': 18002.0}, {'api_host_port': 8000}, {'dev_web_host_port': 5173},
    {'dev_web_host_port': 18001}, {'dev_web_host_port': 18002}, {'web_host_port': 19000},
    {'core_runtime_mode': 1}, {'local_cpu_threads': 0}, {'local_cpu_threads': '1'},
    {'local_memory_gib': True}, {'local_memory_gib': float('nan')}, {'local_memory_gib': -1},
    {'workflow_adapter_url': 'http://127.0.0.1:8001'},
])
def test_rejects_before_normalization(profile, monkeypatch):
    monkeypatch.setattr(profiles, 'normalize_install_profile', lambda *_: pytest.fail('normalized invalid input'))
    with pytest.raises(ValueError):
        install.validate_install_document(document(profile))


@pytest.mark.parametrize('raw', [[], {}, {'schema_version': 'bms.install.v2', 'profile': {}, 'ingress': {'mode': 'local-only'}},
                               {**document(), 'extra': False}, {**document(), 'profile': []}])
def test_closed_versioned_document(raw):
    with pytest.raises(ValueError):
        install.validate_install_document(raw)


@pytest.mark.parametrize('ingress', [{'mode': 'local-only', 'target': 'production'}, {'mode': 'tailnet'},
                                    {'mode': 'tailnet', 'target': 'both'}, {'mode': True},
                                    {'mode': 'tailnet', 'target': 'production', 'public': True}])
def test_invalid_ingress(ingress):
    with pytest.raises(ValueError):
        install.validate_install_document(document(ingress=ingress))


@pytest.mark.parametrize('ingress', [{'mode': 'local-only'}, {'mode': 'tailnet', 'target': 'production'},
                                    {'mode': 'tailnet', 'target': 'development'}])
def test_empty_home_stable_defaults_and_intent(isolated, monkeypatch, ingress):
    monkeypatch.setenv('BMS_DATA', str(ROOT))
    before = snapshot(isolated)
    report = install.configuration_preview(document(ingress=ingress), project_root=ROOT)
    assert report['profile']['data_root'] == str(isolated / 'home/.local/state/biomodstack')
    assert report['resolved'] == {**profiles.resolve_runtime_paths(ROOT, report['profile'], environ={}),
                                 'local_cpu_threads': report['resolved']['local_cpu_threads'],
                                 'local_memory_bytes': report['resolved']['local_memory_bytes']}
    assert report['ingress'] == {**ingress, 'applied': False, 'qualified': False}
    assert report['ready'] is report['apply_available'] is False
    assert snapshot(isolated) == before


@pytest.mark.parametrize('kind', ['source', 'source-symlink', 'file-parent', 'directory-db', 'dangling', 'overlap'])
def test_invalid_storage(isolated, kind):
    path = isolated / 'state'
    key = 'data_root'
    extra = {}
    if kind == 'source':
        path = ROOT / 'state'
    elif kind == 'source-symlink':
        path.symlink_to(ROOT, target_is_directory=True)
    elif kind == 'file-parent':
        path.write_text('file')
        path = path / 'child'
    elif kind == 'directory-db':
        path.mkdir()
        key = 'db_path'
    elif kind == 'dangling':
        path.symlink_to(isolated / 'missing')
    else:
        extra['dev_data_root'] = str(path / 'dev')
    with pytest.raises(ValueError):
        install.configuration_preview(document({key: str(path), **extra}), project_root=ROOT)


@pytest.mark.parametrize('key,value', [('HOME', ''), ('HOME', 'relative'), ('XDG_STATE_HOME', 'relative'),
                                      ('XDG_CONFIG_HOME', 'relative')])
def test_invalid_environment(isolated, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        install.configuration_preview(document(), project_root=ROOT)


def test_existing_legacy_profile_not_read_or_migrated(isolated):
    path = profiles.get_install_profile_path()
    path.parent.mkdir(parents=True)
    path.write_text('{malformed legacy profile')
    before = snapshot(isolated)
    report = install.configuration_preview(document(), project_root=ROOT)
    assert report['compatibility']['existing_profile_present'] is True
    assert snapshot(isolated) == before
    assert profiles.normalize_install_profile({'dev_web_host_port': '5173', 'features': {'unknown': True}}) == {'dev_web_host_port': 18082}


@pytest.mark.parametrize('shell', [False, True])
@pytest.mark.parametrize('raw,exit_code', [(json.dumps(document()), 0), ('{"schema_version":1,"schema_version":2}', 2),
                                         ('[]', 2), ('{"profile": NaN}', 2), ('{broken', 2)])
def test_real_cli_no_writes(isolated, monkeypatch, shell, raw, exit_code):
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(isolated / "bytecode"))
    path = isolated / 'install.json'
    path.write_text(raw)
    before, source = snapshot(isolated), snapshot(ROOT)
    command = [str(ROOT / 'start_ui.sh')] if shell else [sys.executable, '-B', str(ROOT / 'scripts/manage_desktop_services.py')]
    result = subprocess.run(command + (['configure-preview', '--json'] if shell else ['--json', 'configure-preview']) + ['--document', str(path)],
                            capture_output=True, text=True)
    assert result.returncode == exit_code, result.stderr
    report = json.loads(result.stdout)
    assert report['ready'] is False
    assert report['valid'] is (exit_code == 0)
    assert snapshot(isolated) == before
    assert snapshot(ROOT) == source


def test_missing_document_is_structured_configuration_blocker(isolated):
    assert install.preview_report(isolated / 'missing', project_root=ROOT)['valid'] is False
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/manage_desktop_services.py'), 'configure'],
                            capture_output=True, text=True)
    assert result.returncode == 3
    assert json.loads(result.stdout)["configured"] is False


@pytest.mark.parametrize('failure', [OverflowError('overflow'), RuntimeError('symlink loop')])
def test_resolution_errors_are_structured(isolated, monkeypatch, failure):
    path = isolated / 'install.json'
    path.write_text(json.dumps(document()))
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(profiles, 'resolve_runtime_paths', fail)
    report = install.preview_report(path, project_root=ROOT)
    assert report['valid'] is False
    assert report['blockers'][0]['code'] == 'install_document_invalid'


def test_symlink_loop_and_exponent_overflow(isolated):
    loop = isolated / 'loop'
    loop.symlink_to(loop)
    path = isolated / 'install.json'
    for raw in [json.dumps(document({'data_root': str(loop)})),
                json.dumps(document({'local_memory_gib': 1})).replace('"local_memory_gib": 1', '"local_memory_gib": 1e999')]:
        path.write_text(raw)
        report = install.preview_report(path, project_root=ROOT)
        assert report['valid'] is False
        assert report['blockers'][0]['code'] == 'install_document_invalid'


def run_storage_preview(isolated, profile, *, valid, shell=False):
    path = isolated / 'install.json'
    path.write_text(json.dumps(document(profile)))
    before = snapshot(isolated)
    command = ([str(ROOT / 'start_ui.sh')] if shell else
               [sys.executable, '-B', str(ROOT / 'scripts/manage_desktop_services.py')])
    result = subprocess.run(command + ['configure-preview', '--json', '--document', str(path)],
                            capture_output=True, text=True)
    assert result.returncode == (0 if valid else 2), result.stderr
    report = json.loads(result.stdout)
    assert report['valid'] is valid
    assert report['read_only'] is True
    assert report['ready'] is report['apply_available'] is False
    if not valid:
        assert report['blockers'][0]['code'] == 'install_document_invalid'
        assert 'mutable paths must not overlap' in report['blockers'][0]['message']
    assert snapshot(isolated) == before
    return report


# Independent of the validator's list: protect every mutable runtime destination.
MUTABLE_LEAVES = [
    ('inputs_dir', 'inputs'), ('results_dir', 'bms_results'),
    ('db_path', 'biomodstack.db'), ('work_dir', 'work'),
    ('analysis_cache_dir', 'analysis_cache'), ('msa_cache_dir', 'msa_cache'),
    ('sabdab_cache_dir', 'sabdab_cache'),
]


@pytest.mark.parametrize('shell', [False, True])
@pytest.mark.parametrize('key,leaf', MUTABLE_LEAVES)
def test_cli_explicit_path_overlaps_derived_development(isolated, key, leaf, shell):
    run_storage_preview(isolated, {
        'data_root': str(isolated / 'prod'), 'dev_data_root': str(isolated / 'dev'),
        key: str(isolated / 'dev' / leaf),
    }, valid=False, shell=shell)


@pytest.mark.parametrize('shell', [False, True])
@pytest.mark.parametrize('relation', ['equal', 'prod-ancestor', 'dev-ancestor'])
@pytest.mark.parametrize('key', ['results_dir', 'work_dir', 'analysis_cache_dir',
                                 'inputs_dir', 'msa_cache_dir', 'sabdab_cache_dir', 'db_path'])
def test_cli_external_mutable_paths_overlap(isolated, key, relation, shell):
    prod = dev = isolated / 'shared'
    if relation == 'prod-ancestor':
        dev = dev / 'child'
    elif relation == 'dev-ancestor':
        prod = prod / 'child'
    # Absent destinations must be checked too, including file-path containment.
    run_storage_preview(isolated, {
        'data_root': str(isolated / 'prod'), 'dev_data_root': str(isolated / 'dev'),
        key: str(prod), 'dev_results_dir': str(dev),
    }, valid=False, shell=shell)


@pytest.mark.parametrize('lane', ['prod', 'dev'])
@pytest.mark.parametrize('key,leaf', MUTABLE_LEAVES)
def test_cli_canonical_derived_child_symlink_overlap(isolated, lane, key, leaf):
    prod, dev = isolated / 'prod', isolated / 'dev'
    prod.mkdir()
    dev.mkdir()
    shared = isolated / 'external'
    if key == 'db_path':
        shared.mkdir()
        shared = shared / 'biomodstack.db'
        shared.write_text('existing database fixture')
    else:
        shared.mkdir()
    profile = {'data_root': str(prod), 'dev_data_root': str(dev)}
    if lane == 'dev':
        (dev / leaf).symlink_to(shared, target_is_directory=key != 'db_path')
        profile[key] = str(shared)
    else:
        (prod / leaf).symlink_to(shared, target_is_directory=key != 'db_path')
        # Development results contain the canonical production destination,
        # which lives outside both lexical lane roots.
        profile['dev_results_dir'] = str(shared.parent if key == 'db_path' else shared)
    run_storage_preview(isolated, profile, valid=False)


@pytest.mark.parametrize('relation', ['equal', 'prod-ancestor', 'dev-ancestor'])
def test_cli_symlink_aliases_external_overrides(isolated, relation):
    shared, alias = isolated / 'shared', isolated / 'alias'
    shared.mkdir()
    alias.symlink_to(shared, target_is_directory=True)
    prod, dev = shared, alias
    if relation == 'prod-ancestor':
        dev = alias / 'missing-child'
    elif relation == 'dev-ancestor':
        prod = shared / 'missing-child'
    run_storage_preview(isolated, {
        'data_root': str(isolated / 'prod'), 'dev_data_root': str(isolated / 'dev'),
        'results_dir': str(prod), 'dev_results_dir': str(dev),
    }, valid=False)


@pytest.mark.parametrize('shell', [False, True])
def test_cli_same_lane_nesting_and_shared_immutable_assets(isolated, shell):
    prod, dev, assets = isolated / 'prod', isolated / 'prod-dev', isolated / 'assets'
    prod.mkdir()
    dev.mkdir()
    assets.mkdir()
    # Development shares the same immutable weights/reference data by symlink;
    # production runtime images may also live in the development tree.
    (dev / 'weights').symlink_to(assets, target_is_directory=True)
    (dev / 'colabfold_db').symlink_to(assets, target_is_directory=True)
    (prod / 'biomodstack.db').write_text('existing production database')
    (dev / 'biomodstack.db').write_text('existing development database')
    report = run_storage_preview(isolated, {
        'data_root': str(prod), 'dev_data_root': str(dev),
        'results_dir': str(prod / 'nested'), 'work_dir': str(prod / 'nested/work'),
        'analysis_cache_dir': str(prod / 'nested/work/cache'),
        'dev_results_dir': str(dev / 'work/results'),
        'weights_root': str(assets), 'colabfold_db': str(assets),
        'container_dir': str(dev / 'runtime-images'),
    }, valid=True, shell=shell)
    assert Path(report['resolved']['dev_weights_root']).resolve() == assets
    assert report['resolved']['dev_analysis_cache_dir'] == str(dev / 'analysis_cache')


def test_schema_surface_matches_validator():
    schema = json.loads((ROOT / 'config/schemas/install-document.v1.schema.json').read_text())
    assert set(schema['properties']['profile']['properties']) == install.PROFILE_FIELDS
