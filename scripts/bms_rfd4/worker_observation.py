"""Passive spawn-worker journal transport; native scheduling/results unchanged.

The pinned MetricsPool passes initialize_metrics_worker to ProcessPoolExecutor.
Only that initializer reference is decorated, retaining its arguments and call.
Journals are private to this observation session, not scientific outputs.
"""
from __future__ import annotations
import contextlib
import functools
import json
import os
from pathlib import Path
import sys
import tempfile


def initialize_observed_worker(native_initializer, journal_dir, *args):
    # Preserve native initializer exceptions and order. Observation is not a gate.
    native_initializer(*args)
    try:
        from .publication import NativeObserver
        observer = NativeObserver()
        previous = sys.getprofile()
        journal = Path(journal_dir) / f'{os.getpid()}.jsonl'

        def profile(frame, event, value):
            if previous is not None:
                previous(frame, event, value)
            observer.observe(frame, event, value)
            if not observer.events and not observer.errors:
                return
            try:
                with journal.open('a') as stream:
                    for item in observer.events:
                        stream.write(json.dumps({'event': item}, allow_nan=False) + '\n')
                    for error in observer.errors:
                        stream.write(json.dumps({'error': error}) + '\n')
            except Exception as exc:
                # No worker result or future is replaced by an observation error.
                print(f'BMS worker observation unavailable: {exc}', file=sys.stderr)
            finally:
                observer.events.clear()
                observer.errors.clear()
        sys.setprofile(profile)
    except Exception as exc:
        print(f'BMS worker observation unavailable: {exc}', file=sys.stderr)


@contextlib.contextmanager
def observe_metrics_workers(observer, root, *, native_module=None):
    """Collect after the native owner drains/shuts down its own pool.

    No pools, futures, task payloads or model settings are created/modified here.
    Inline workers remain covered by the launcher's existing observer.
    """
    original = None
    directory = None
    try:
        if native_module is None:
            from rfproteina.metrics.utils import metrics_utils as native_module
        original = native_module.initialize_metrics_worker
        directory = tempfile.TemporaryDirectory(prefix='.worker-observation-', dir=root)
        native_module.initialize_metrics_worker = functools.partial(
            initialize_observed_worker, original, directory.name)
    except Exception as exc:
        observer.errors.append(f'worker_observation_install: {type(exc).__name__}: {exc}')
    try:
        yield
    finally:
        if original is not None:
            native_module.initialize_metrics_worker = original
        if directory is not None:
            try:
                for journal in sorted(Path(directory.name).glob('*.jsonl')):
                    with journal.open() as stream:
                        for line in stream:
                            try:
                                item = json.loads(line)
                                if 'event' in item:
                                    observer.events.append(item['event'])
                                elif 'error' in item:
                                    observer.errors.append(item['error'])
                            except Exception as exc:
                                observer.errors.append(f'worker_observation_read: {type(exc).__name__}: {exc}')
            except Exception as exc:
                observer.errors.append(f'worker_observation_collect: {type(exc).__name__}: {exc}')
            finally:
                try:
                    directory.cleanup()
                except Exception as exc:
                    observer.errors.append(f'worker_observation_cleanup: {type(exc).__name__}: {exc}')
