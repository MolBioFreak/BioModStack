"""Incremental accounting contracts; fake byte sizes, never model execution.

Cache states are the output of Cache.probe, not filename-existence heuristics.
The existing managed-runtime suite owns real copy/ENOSPC/boot fault injection.
"""
import errno
from types import SimpleNamespace

import pytest
from tools import bms_managed_runtime as managed

GiB = 1024 ** 3


def row(name='weights/model.bin', size=10 * GiB, digest='a' * 64, kind=None):
    value = dict(name=name, sha256=digest, size_bytes=size, mode=0o444)
    if kind is not None:
        value['kind'] = kind
    return value


def observe(rows, states):
    return {'artifacts': [dict({key: r[key] for key in ('name', 'sha256', 'size_bytes')}, state=s)
        for r, s in zip(rows, states, strict=True)]}


class Storage:
    def __init__(self, state='cache_hit'):
        self.state = state
        self.calls = []

    def probe(self, item):
        self.calls.append(item)
        return {**item, 'state': self.state}


def account(rows, installed, cached):
    storage = Storage(cached)
    additional = managed.incremental_admission_rows({'artifacts': rows}, observe(rows, installed), storage)
    return sum(r['size_bytes'] for r in additional), additional, storage


@pytest.mark.parametrize('cache_state,installed,expected', [
    ('missing', 'missing', 30), ('cache_hit', 'missing', 10),
    ('cache_hit', 'verified', 0), ('missing', 'verified', 20),
    ('corrupt', 'missing', 30), ('cache_hit', 'incompatible', 10),
    ('cache_hit', 'corrupt', 10),
])
def test_weight_budget_matches_actual_upload_and_install(cache_state, installed, expected):
    total, _, _ = account([row()], [installed], cache_state)
    assert total == expected * GiB


@pytest.mark.parametrize('cached,expected', [('missing', 20), ('cache_hit', 0)])
def test_image_never_receives_a_managed_copy(cached, expected):
    total, _, _ = account([row('containers/model.sif', kind='runtime_image')], ['missing'], cached)
    assert total == expected * GiB


def test_deduplicate_uploads_but_not_distinct_install_destinations():
    total, _, storage = account([row('weights/a'), row('weights/b')], ['missing'] * 2, 'missing')
    assert total == 40 * GiB  # one upload + one CAS + two distinct destinations
    assert len(storage.calls) == 1


def test_links_are_generated_metadata_not_cache_uploads():
    link = row('weights/link', size=6, kind='runtime_link')
    link.update(target='model', mode=0o777)
    total, _, storage = account([link], ['missing'], 'missing')
    assert total == 6
    assert storage.calls == []


def test_image_and_regular_bytes_are_distinct_cache_namespaces():
    _, _, storage = account([row(), row('containers/m.sif', kind='runtime_image')],
        ['missing'] * 2, 'missing')
    assert len(storage.calls) == 2


def test_conflicting_content_sizes_are_refused_before_any_probe():
    storage = Storage()
    rows = [row('weights/a'), row('weights/b', size=1)]
    with pytest.raises(ValueError, match='conflicting_cache_object_sizes'):
        managed.incremental_admission_rows({'artifacts': rows}, observe(rows, ['missing'] * 2), storage)
    assert not storage.calls


@pytest.mark.parametrize('fault', ['count', 'name', 'digest', 'state'])
def test_no_credit_from_mismatched_observation(fault):
    rows = [row()]
    observed = observe(rows, ['verified'])
    if fault == 'count':
        observed['artifacts'] = []
    elif fault == 'name':
        observed['artifacts'][0]['name'] = 'weights/other'
    elif fault == 'digest':
        observed['artifacts'][0]['sha256'] = 'b' * 64
    else:
        observed['artifacts'][0]['state'] = 'probably-present'
    with pytest.raises(ValueError, match='admission_observation_mismatch'):
        managed.incremental_admission_rows({'artifacts': rows}, observed, Storage())


@pytest.mark.parametrize('fault', ['identity', 'unknown', 'exception'])
def test_uncertain_cache_does_not_receive_credit(fault):
    class BadStorage:
        def probe(self, item):
            if fault == 'exception':
                raise OSError('unreadable fixture')
            return {**item, **({'sha256': 'b' * 64, 'state': 'cache_hit'} if fault == 'identity'
                              else {'state': 'exists'})}
    rows = [row()]
    with pytest.raises((ValueError, OSError)):
        managed.incremental_admission_rows({'artifacts': rows}, observe(rows, ['missing']), BadStorage())


def test_corrupt_shared_image_is_not_an_authorized_repair():
    with pytest.raises(ValueError, match='corrupt_shared_image'):
        account([row('containers/a.sif', kind='runtime_image')], ['corrupt'], 'corrupt')


def test_warm_retry_fits_when_cold_admission_does_not(monkeypatch):
    monkeypatch.setattr(managed.os, 'fstatvfs', lambda _: SimpleNamespace(
        f_frsize=4096, f_bsize=4096, f_bavail=20 * GiB // 4096))
    rows = [row()]
    _, cold, _ = account(rows, ['missing'], 'missing')
    _, warm, _ = account(rows, ['missing'], 'cache_hit')
    with pytest.raises(OSError) as caught:
        managed.check_space(123, {'artifacts': rows}, cold)
    assert caught.value.errno == errno.ENOSPC
    assert managed.check_space(123, {'artifacts': rows}, warm)['additional_copy_bytes'] == 10 * GiB


def test_headroom_is_preserved_even_for_fully_installed_release(monkeypatch):
    monkeypatch.setattr(managed.os, 'fstatvfs', lambda _: SimpleNamespace(
        f_frsize=4096, f_bsize=4096, f_bavail=(GiB - 4096) // 4096))
    with pytest.raises(OSError):
        managed.check_space(123, {'artifacts': [row()]}, [])


def test_large_closure_deduplicates_once_per_content_identity():
    rows = [row(f'weights/model/{i:05d}', size=1024, digest=f'{i % 1024:064x}') for i in range(88037)]
    total, _, storage = account(rows, ['missing'] * len(rows), 'missing')
    assert len(storage.calls) == 1024
    assert total == (88037 + 2 * 1024) * 1024
