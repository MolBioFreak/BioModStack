from services import addon_status


def test_browsing_reads_only_liveness_and_does_not_claim_readiness(monkeypatch):
    calls = []
    def read(url, timeout):
        calls.append((url, timeout))
        assert url.endswith('/health/live')
        return {"status": "alive", "version": "1.2"}
    monkeypatch.setattr(addon_status, '_read_json', read)
    monkeypatch.setenv('BMS_STATS_TOOLKIT_URL', 'http://inert.invalid/')
    status = addon_status.probe_stats_addon()
    assert calls == [('http://inert.invalid/health/live', 1.5)]
    assert status['available'] is True
    assert status['ready'] is None
    assert status['capability_count'] is None
    assert status['api_version'] is None
    assert status['version'] == '1.2'
    assert 'readiness not assessed' in status['detail']


def test_actual_service_failure_remains_visible(monkeypatch):
    def read(*args):
        raise OSError('offline')
    monkeypatch.setattr(addon_status, '_read_json', read)
    status = addon_status.probe_stats_addon()
    assert status['available'] is False
    assert status['ready'] is None
    assert status['detail'] == 'standalone probe failed: OSError'
