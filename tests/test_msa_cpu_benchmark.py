import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location('msa_cpu_benchmark', Path(__file__).resolve().parents[1] / 'scripts/msa_cpu_benchmark.py')
assert spec is not None and spec.loader is not None
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def test_observations_do_not_modify_or_reject_mismatch(tmp_path):
    source = tmp_path / 'source'
    output = tmp_path / 'output'
    source.write_text('>a\nAC\n>b\nTT\n')
    output.write_text('>a\nA-G\n>a\nAC-\n')
    before = output.read_bytes()
    obs = b.observe(source, output)
    assert obs['degap_mismatches'] == ['a']
    assert obs['missing_ids'] == ['b']
    assert obs['duplicate_output_ids'] == 1
    assert output.read_bytes() == before


def test_timeout_and_failed_status_are_distinct(tmp_path):
    def run(code, timeout):
        return b.invoke([sys.executable, '-c', code], tmp_path, timeout, tmp_path / 'out', tmp_path / 'err', tmp_path / 'time')
    assert run('raise SystemExit(3)', 5)['status'] == 'failed'
    timed = run('import time; time.sleep(5)', .1)
    assert timed['status'] == 'timeout'
    assert timed['peak_rss_kib'] is None
    assert timed['user_seconds'] is None


def test_missing_output_is_observation(tmp_path):
    assert 'unavailable' in b.observe(tmp_path / 'missing', tmp_path / 'missing2')
