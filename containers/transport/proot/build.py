#!/usr/bin/env python3
"""Rebuild the checked-in PRoot artifact without publishing or installing it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
PINS = json.loads((HERE / 'pins.json').read_text())
SOURCE = PINS['source_commit']
UTHASH = PINS['uthash_commit']

def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='bms-proot-build-') as temporary:
        root = Path(temporary)
        source = root / 'source'
        assert hashlib.sha256((HERE / 'readonly-open.patch').read_bytes()).hexdigest() == PINS['patch_sha256']
        run('git', 'clone', '--no-checkout', PINS['source_repository'], str(source))
        run('git', '-C', str(source), 'checkout', '--detach', SOURCE)
        run('git', '-C', str(source), 'submodule', 'update', '--init', 'lib/uthash')
        assert subprocess.check_output(['git', '-C', str(source / 'lib/uthash'), 'rev-parse', 'HEAD'], text=True).strip() == UTHASH
        run('git', '-C', str(source), 'apply', str(HERE / 'readonly-open.patch'))
        assert hashlib.sha256((HERE / 'lazy-files.patch').read_bytes()).hexdigest() == PINS['lazy_patch_sha256']
        assert hashlib.sha256((HERE / 'cow.c').read_bytes()).hexdigest() == PINS['cow_source_sha256']
        run('git', '-C', str(source), 'apply', str(HERE / 'lazy-files.patch'))
        shutil.copyfile(HERE / 'cow.c', source / 'src/extension/cow.c')
        run('docker', 'build', '-t', 'bms-proot-readonly-builder', str(HERE))
        run('docker', 'run', '--rm', '--network=none', '--user', f'{os.getuid()}:{os.getgid()}',
            '-v', f'{source}:/proot', '-w', '/proot/src', 'bms-proot-readonly-builder',
            'make', '-j4', 'HAS_SWIG=', 'HAS_PYTHON_CONFIG=', 'GIT_VERSION=', 'GIT_COMMIT=',
            'VERSION=udocker-fe64598-bms-lazy-2')
        run('docker', 'run', '--rm', '--network=none', '--user', f'{os.getuid()}:{os.getgid()}',
            '-v', f'{source}:/proot', 'bms-proot-readonly-builder',
            'strip', '--strip-debug', '/proot/src/proot')
        artifact = source / 'src/proot'
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if digest != PINS['artifact_sha256'] or artifact.stat().st_size != PINS['artifact_size']:
            raise RuntimeError(f'rebuild differs from pinned artifact: {digest}')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(artifact, args.output)
        args.output.chmod(0o755)
        print(digest, args.output)

if __name__ == '__main__':
    main()
