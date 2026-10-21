"""Download-decoration regressions; these fixtures do not assert scientific validity."""
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote

import pytest

import paths
from services.bindcraft2_publication import native_workbench_page


def _page_and_receipt(root: Path, names: list[str]):
    page = {'stage': 'draw', 'arm': None, 'rows': [{'design': 'candidate', 'sequence': 'AAA'}],
            'total': 1, 'offset': 0, 'limit': 100}
    receipt = {'root': str(root), 'files': {name: {'sha256': 'a' * 64, 'bytes': 1,
        'media_type': 'chemical/x-mmcif'} for name in names}, 'candidates': [],
        'native_documents': [{'path': names[0], 'artifact_id': 'exact-document', 'job_id': 'source',
            'arm': None, 'native_rows': [{'stage': 'draw', 'design': 'candidate'}],
            'binder_chains': 'B', 'target_chains': 'A'}] if names else []}
    return page, receipt


def test_explicit_download_roots_keep_scalar_containment_and_order(monkeypatch, tmp_path):
    root = tmp_path / 'data'
    inner = root / 'nested'
    inner.mkdir(parents=True)
    target = inner / 'structure #1.cif'
    target.write_text('data_example\n')
    outside = tmp_path / 'data_sibling'
    outside.mkdir()
    (outside / 'other.cif').write_text('data_other\n')
    (root / 'escape').symlink_to(outside, target_is_directory=True)
    roots = {'bms_results': root, 'nested': inner}
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: roots)
    expected = paths.to_allowed_relative(target)
    calls = []
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: calls.append(1) or {})
    assert paths.to_allowed_relative(target, allowed_roots=roots) == expected == 'bms_results/nested/structure #1.cif'
    with pytest.raises(ValueError, match='not under allowed roots'):
        paths.to_allowed_relative(root / 'escape' / 'other.cif', allowed_roots=roots)
    with pytest.raises(ValueError, match='not under allowed roots'):
        paths.to_allowed_relative(target, allowed_roots={})
    assert calls == []


def test_explicit_roots_still_resolve_each_file(monkeypatch, tmp_path):
    root, outside = tmp_path / 'data', tmp_path / 'outside'
    root.mkdir(); outside.mkdir()
    for directory in (root, outside):
        (directory / 'record.cif').write_text('data_example\n')
    selected = root / 'selected.cif'
    selected.symlink_to(root / 'record.cif')
    roots = {'bms_results': root}
    assert paths.to_allowed_relative(selected, allowed_roots=roots) == 'bms_results/record.cif'
    selected.unlink()
    selected.symlink_to(outside / 'record.cif')
    with pytest.raises(ValueError, match='not under allowed roots'):
        paths.to_allowed_relative(selected, allowed_roots=roots)


def test_native_download_roots_are_prepared_once_and_preserve_full_response(monkeypatch, tmp_path):
    root = tmp_path / 'data'
    root.mkdir()
    names = ['structure #1.cif', *[f'candidate-{n}.cif' for n in range(35)]]
    for name in names:
        (root / name).write_text('x')
    roots = {'bms_results': root}
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: roots)
    downloads = {name: '/api/files/download/' + quote(paths.to_allowed_relative(root / name), safe='/') for name in names}
    calls = []
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: calls.append(1) or roots)
    page, receipt = _page_and_receipt(root, names)
    original = deepcopy((page, receipt))
    expected_document = {**receipt['native_documents'][0], 'download_url': downloads[names[0]],
                         'native_source': {'job_id': 'source', 'artifact_id': 'exact-document'}}
    expected = {**page, 'rows': [{**page['rows'][0], 'structures': [expected_document]}],
        'artifacts': [{'path': name, **entry, 'download_url': downloads[name],
                      **({'artifact_id': 'exact-document', 'job_id': 'source'} if name == names[0] else {})}
                     for name, entry in receipt['files'].items()]}
    assert native_workbench_page(page, receipt) == expected
    assert (page, receipt) == original
    assert calls == [1]


def test_native_download_roots_are_fresh_on_next_response(monkeypatch, tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir(); second.mkdir()
    roots = {'bms_results': first}
    calls = []
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: calls.append(1) or dict(roots))
    page, receipt = _page_and_receipt(first, ['one.cif', 'two.cif'])
    assert native_workbench_page(page, receipt)['artifacts'][0]['download_url'] == '/api/files/download/bms_results/one.cif'
    roots['bms_results'] = second
    assert all(row['download_url'] is None for row in native_workbench_page(page, receipt)['artifacts'])
    assert calls == [1, 1]


def test_native_download_decoration_keeps_escaping_file_unavailable(monkeypatch, tmp_path):
    root, outside = tmp_path / 'data', tmp_path / 'outside'
    root.mkdir(); outside.mkdir()
    (root / 'safe.cif').write_text('x')
    (outside / 'other.cif').write_text('x')
    (root / 'escape').symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {'bms_results': root})
    page, receipt = _page_and_receipt(root, ['safe.cif', 'escape/other.cif'])
    result = native_workbench_page(page, receipt)
    assert [row['download_url'] for row in result['artifacts']] == ['/api/files/download/bms_results/safe.cif', None]
    assert result['rows'][0]['structures'][0]['artifact_id'] == 'exact-document'


def test_native_download_config_value_error_does_not_hide_results(monkeypatch, tmp_path):
    calls = []
    def unavailable():
        calls.append(1)
        raise ValueError('configuration unavailable')
    monkeypatch.setattr(paths, 'get_allowed_roots', unavailable)
    page, receipt = _page_and_receipt(tmp_path, ['one.cif', 'two.cif'])
    result = native_workbench_page(page, receipt)
    assert all(row['download_url'] is None for row in result['artifacts'])
    assert result['rows'][0]['structures'][0]['artifact_id'] == 'exact-document'
    assert calls == [1]


def test_empty_native_inventory_does_not_resolve_roots(monkeypatch, tmp_path):
    def unexpected():
        raise AssertionError('no artifacts need download paths')
    monkeypatch.setattr(paths, 'get_allowed_roots', unexpected)
    page, receipt = _page_and_receipt(tmp_path, [])
    assert native_workbench_page(page, receipt) == {**page,
        'rows': [{**page['rows'][0], 'structures': []}], 'artifacts': []}
