"""Real worker sealing/return owners, isolated files and controlled boot epochs."""
import json
from pathlib import Path

import pytest

from tools import bms_remote_worker as worker


def attempt(tmp_path, state='running', *, reboot=True):
    source = tmp_path / 'bundle/source'
    source.mkdir(parents=True)
    archive = source / '.bms-source.tar'
    archive.write_bytes(b'isolated source archive fixture')
    output = tmp_path / 'results'
    output.mkdir()
    envelope = dict(schema='bms.remote-execution.v1', job_id='job', attempt_id='attempt',
        source_revision='a'*40, source_tree='b'*40,
        source_archive_sha256=worker.sha256_file(archive),
        files=[dict(relative_path='source/.bms-source.tar', size_bytes=archive.stat().st_size,
            sha256=worker.sha256_file(archive), mode=0o644)],
        working_directory=str(Path(__file__).resolve().parents[3]), output_directory=str(output),
        command=['never-run-science'], environment={'API_TOKEN': 'private-fixture-value'})
    worker.atomic_json(worker.envelope_path(tmp_path), envelope)
    value = worker.base_status(envelope, state)
    if state != 'prepared':
        value['started_at'] = worker.utc_now()
    if reboot:
        value['boot_id'] = 'previous-boot'
    worker.atomic_json(worker.status_path(tmp_path), value)
    (tmp_path / 'nextflow.log').write_text('failure API_TOKEN=private-fixture-value\n')
    (tmp_path / 'unrelated-secret.log').write_text('must not sweep')
    (output / 'native.pdb').write_text('retained native bytes')
    return envelope
