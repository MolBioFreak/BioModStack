"""Bounded read-only attempt logs, sent through the governed SSH transport.

No worker lifecycle commands, result publication, or arbitrary member selection.
The controller supplies the already validated active observation identity.
"""
import json
import os
from pathlib import PurePosixPath
import stat
import sys

MAX_LOG_BYTES = 1_048_576
IDENTITY_FIELDS = ('job_id', 'attempt_id', 'generation', 'boot_id',
                   'continuation_lease_id', 'plan_sha256', 'native_output_directory')


def read_logs(attempt_dir, expected, tail):
    path = PurePosixPath(attempt_dir)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Invalid attempt log directory')
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child

        def read(name, limit, *, from_end=False):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, 'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('Attempt log reader requires regular files')
                if from_end:
                    handle.seek(max(0, info.st_size - limit))
                elif info.st_size > limit:
                    raise ValueError('Attempt status exceeds bounded size')
                return handle.read(limit)

        def check():
            current = json.loads(read('status.json', 4 * MAX_LOG_BYTES))
            identity = {key: current.get(key, 0 if key == 'generation' else None)
                        for key in IDENTITY_FIELDS}
            if identity != expected or current.get('state') not in {'prepared', 'running', 'cancelling', 'awaiting_input'}:
                raise ValueError('Active attempt log identity changed')

        check()
        result = {}
        for field, name in (('nextflow_log', 'nextflow.log'), ('command_log', 'supervisor.log')):
            try:
                raw = read(name, MAX_LOG_BYTES, from_end=True)
            except FileNotFoundError:
                result[field] = None
            else:
                result[field] = '\n'.join(raw.decode('utf-8', errors='replace').splitlines()[-max(1, min(int(tail), 5000)):])
        check()
        return result
    finally:
        os.close(directory)


if __name__ == '__main__':
    print(json.dumps(read_logs(sys.argv[1], json.loads(sys.argv[2]), int(sys.argv[3]))))
