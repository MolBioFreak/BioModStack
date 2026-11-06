"""Spawn transport specimens only: no molecular/scientific values or execution."""
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from types import SimpleNamespace
import pytest
from scripts.bms_rfd4.publication import NativeObserver
from scripts.bms_rfd4.worker_observation import observe_metrics_workers


def initializer(marker):
    os.environ['BMS_OBSERVER_TEST_MARKER'] = marker


def transport_task():
    # These compiled call frames model the observed ABI, not scientific output.
    namespace = {}
    exec(compile('def write(file_name):\n    return None\n',
                 '/biotite/structure/io/trajfile.py', 'exec'), namespace)
    exec(compile('def _write_trajectories_if_present():\n'
                 '    example_id = "transport-only"\n'
                 '    traj_key = "native-key"\n'
                 '    write("/not-a-scientific-fixture.xtc")\n',
                 '/rfproteina/metrics/utils/metrics_utils.py', 'exec'), namespace)
    namespace['_write_trajectories_if_present']()
    return os.environ['BMS_OBSERVER_TEST_MARKER']


def test_spawn_transport_keeps_native_initializer_and_result(tmp_path):
    observer = NativeObserver()
    module = SimpleNamespace(initialize_metrics_worker=initializer)
    with observe_metrics_workers(observer, tmp_path, native_module=module):
        with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn'),
                                 initializer=module.initialize_metrics_worker, initargs=('unchanged',)) as pool:
            assert pool.submit(transport_task).result(timeout=30) == 'unchanged'
    assert module.initialize_metrics_worker is initializer
    assert not observer.errors
    assert observer.events == [{'event': 'native_relation', 'role': 'trajectory',
        'path': '/not-a-scientific-fixture.xtc', 'example_id': 'transport-only',
        'producer_field': '_write_trajectories_if_present:XTCFile.write',
        'trajectory_key': 'native-key', 'format': 'xtc',
        'time_semantics': 'native_step_labels_not_physical_time'}]
    assert not list(tmp_path.iterdir())


def test_observation_install_failure_does_not_stop_native(tmp_path):
    observer = NativeObserver()
    with observe_metrics_workers(observer, tmp_path, native_module=SimpleNamespace()):
        reached_native = True
    assert reached_native
    assert observer.errors[0].startswith('worker_observation_install:')


def test_native_exception_unchanged_and_initializer_restored(tmp_path):
    module = SimpleNamespace(initialize_metrics_worker=initializer)
    with pytest.raises(RuntimeError, match='native failure'):
        with observe_metrics_workers(NativeObserver(), tmp_path, native_module=module):
            raise RuntimeError('native failure')
    assert module.initialize_metrics_worker is initializer
