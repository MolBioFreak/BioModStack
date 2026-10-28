#!/usr/bin/env python3
"""Stage a qualified CPU Studio runtime; never install units or start services.

Activation is a reviewed descriptor copy into the existing service owner's config.
The interpreter/dependencies remain isolated from the API and model runtimes.
"""
from __future__ import annotations
import argparse
import hashlib
import json

from pathlib import Path
import secrets
import re
import zipfile
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify(prepared, wheel):
    from atomworks_studio_package import verify_record
    verify_record(wheel)
    receipt = json.loads((prepared / 'share/atomworks-studio/receipt.json').read_text())
    if receipt['wheel_sha256'] != sha(wheel) or receipt['runtime_lock_sha256'] != sha(ROOT / 'docker/atomworks-studio.lock'):
        raise ValueError('prepared runtime wheel/lock identity mismatch')
    descriptor = ROOT / 'third_party/atomworks_studio/host-build.json'
    if receipt['build_provenance']['host_build_descriptor_sha256'] != sha(descriptor):
        raise ValueError('prepared runtime host descriptor mismatch')
    site = prepared / 'lib/python3.12/site-packages'
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.startswith(('annotation_studio/', 'atomworks_studio_editing/', 'atomworks_studio_rfd4/')) and not name.startswith('annotation_studio/web/'):
                if (site / name).read_bytes() != archive.read(name):
                    raise ValueError('installed wheel member mismatch: ' + name)
    pins = dict(re.findall(r'^([A-Za-z0-9_.-]+)==(\S+)',
                           (ROOT / 'docker/atomworks-studio.lock').read_text(), re.MULTILINE))
    code = 'import importlib.metadata,json,sys; print(json.dumps({n:importlib.metadata.version(n) for n in json.loads(sys.argv[1])}))'
    installed = json.loads(subprocess.check_output([str(prepared / 'bin/python'), '-I', '-B', '-c', code, json.dumps(list(pins))], text=True))
    if installed != pins:
        raise ValueError('prepared runtime installed versions differ from the lock')
    for name, expected in receipt['installed_web_sha256'].items():
        if sha(site / 'annotation_studio/web' / name) != expected:
            raise ValueError('prepared runtime web mismatch: ' + name)
    for name, expected in receipt['build_provenance']['replaced_python'].items():
        if sha(site / name) != expected:
            raise ValueError('prepared runtime Python mismatch: ' + name)
    for name, expected in receipt['notice_sha256'].items():
        if sha(prepared / 'share/atomworks-studio' / name) != expected:
            raise ValueError('prepared runtime notice mismatch: ' + name)
    return receipt


def stage(args):
    prepared, destination = args.prepared.resolve(), args.destination.resolve()
    verify(prepared, args.wheel)
    destination.mkdir(parents=True, exist_ok=False)
    destination.chmod(0o700)
    runtime = destination / 'venv'
    # Copy, not symlink/hardlink to expiring scratch or shared writable caches.
    subprocess.run(['cp', '-a', '--reflink=auto', str(prepared), str(runtime)], check=True)
    # uv venv Python may link to an interpreter in expiring scratch. Retain the
    # complete standalone interpreter body, not just its ELF executable.
    original_python = (prepared / 'bin/python').resolve()
    if original_python.parent.parent in (Path('/usr'), Path('/usr/local')):
        raise ValueError('staging requires the prepared standalone Python distribution')
    interpreter = destination / 'python'
    subprocess.run(['cp', '-a', '--reflink=auto', str(original_python.parent.parent), str(interpreter)], check=True)
    for name in ('python', 'python3', 'python3.12'):
        path = runtime / 'bin' / name
        path.unlink(missing_ok=True)
        path.symlink_to(interpreter / 'bin' / original_python.name)
    cfg = runtime / 'pyvenv.cfg'
    cfg.write_text('\n'.join('home = ' + str(interpreter / 'bin') if line.startswith('home =') else line
                             for line in cfg.read_text().splitlines()) + '\n')
    # Console scripts retain absolute shebangs; relocate only this copied venv.
    for path in (runtime / 'bin').iterdir():
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            if data.startswith(b'#!' + str(prepared).encode() + b'/bin/'):
                path.write_bytes(data.replace(str(prepared).encode(), str(runtime).encode(), 1))
    shutil.copy2(args.wheel, destination / args.wheel.name)
    shutil.copy2(ROOT / 'docker/atomworks-studio.lock', destination / 'runtime.lock')
    shutil.copytree(args.geometry_bundle, destination / 'geometry')
    verify(runtime, destination / args.wheel.name)
    subprocess.run([str(runtime / 'bin/python'), '-B', '-c',
        'import torch, rdkit; from annotation_studio.main import app; assert torch.__version__ == "2.7.1+cpu"'], check=True)
    subprocess.run(['uv', 'pip', 'check', '--python', str(runtime / 'bin/python')], check=True)
    sessions = destination / 'sessions'
    sessions.mkdir(mode=0o700)
    env = destination / 'development.env'
    values = {
        'BMS_ATOMWORKS_STUDIO_SECRET': secrets.token_hex(32),
        'BMS_ATOMWORKS_STUDIO_URL': f'http://127.0.0.1:{args.port}',
        'BMS_MOLECULAR_PREPARATION_PYTHON': str(runtime / 'bin/python'),
        'BMS_MOLECULAR_GEOMETRY_BUNDLE': str(destination / 'geometry'),
        'ANNOTATION_STUDIO_SESSION_DIR': str(sessions),
    }
    # EnvironmentFile syntax; no shell evaluation and no secret in stdout/receipt.
    env.write_text(''.join(k + '=' + json.dumps(v) + '\n' for k, v in values.items()))
    env.chmod(0o600)
    descriptor = {'schema_version': 'bms.atomworks-studio-runtime.v1',
                  'python': str(runtime / 'bin/python'), 'environment_file': str(env),
                  'port': args.port, 'sessions': str(sessions)}
    (destination / 'development.json').write_text(json.dumps(descriptor, indent=2) + '\n')
    manifest = {str(p.relative_to(destination)): sha(p)
                for p in sorted(destination.rglob('*'))
                if p.is_file() and not p.is_symlink() and p != env}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'staged': str(destination), 'descriptor': str(destination / 'development.json'),
                      'manifest_sha256': sha(destination / 'manifest.json'), 'activated': False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--geometry-bundle', type=Path)
    parser.add_argument('--port', type=int)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if args.verify_only:
        verify(args.prepared, args.wheel)
        manifest_path = args.prepared.parent / 'manifest.json'
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            for name, expected in manifest.items():
                if sha(manifest_path.parent / name) != expected:
                    raise ValueError('staged payload mismatch: ' + name)
            print(json.dumps({'verified_files': len(manifest), 'manifest_sha256': sha(manifest_path)}))
        else:
            print(json.dumps({'verified_prepared_runtime': str(args.prepared)}))
        return
    if not args.destination or not args.geometry_bundle or args.port is None:
        parser.error('staging requires destination, geometry-bundle and port')
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    stage(args)


if __name__ == '__main__':
    main()
