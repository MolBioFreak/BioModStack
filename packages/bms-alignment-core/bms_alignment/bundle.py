"""Portable directory bundles: relative filenames and SHA-256 verification."""
import hashlib
import json
import re
from pathlib import Path, PurePosixPath


def _safe_name(name):
    return (isinstance(name, str) and bool(name) and '\\' not in name
            and not any(ord(c) < 32 or ord(c) == 127 for c in name)
            and not PurePosixPath(name).is_absolute()
            and all(part not in ('', '.', '..') for part in name.split('/'))
            and name != 'manifest.json')


def make_bundle(source: Path, destination: Path, filenames: list[str], *, excluded: list[str] | None = None) -> dict:
    if len(set(filenames)) != len(filenames) or not all(_safe_name(n) for n in filenames):
        raise ValueError('unsafe or duplicate bundle path')
    destination.mkdir(parents=True, exist_ok=True)
    entries = []
    for name in filenames:
        src = (source / name).resolve()
        if not src.is_relative_to(source.resolve()) or not src.is_file():
            raise ValueError('source escapes bundle root or is missing')
        data = src.read_bytes()
        dst = destination / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.resolve().is_relative_to(destination.resolve()):
            raise ValueError('destination escapes bundle root')
        dst.write_bytes(data)
        entries.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
    manifest = {'schema': 'bms.alignment-bundle.v1', 'files': entries, 'dependencies': {'included': filenames, 'excluded': list(excluded or [])}}
    (destination/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


def verify_bundle(root: Path) -> dict:
    try:
        manifest = json.loads((root/'manifest.json').read_text())
    except (OSError, ValueError, UnicodeError) as exc:
        return {'valid': False, 'errors': [f'invalid manifest: {exc}']}
    errors = []
    if not isinstance(manifest, dict) or set(manifest) != {'schema', 'files', 'dependencies'} or manifest.get('schema') != 'bms.alignment-bundle.v1':
        return {'valid': False, 'errors': ['invalid manifest schema']}
    files = manifest['files']
    deps = manifest['dependencies']
    if not isinstance(files, list) or not isinstance(deps, dict) or set(deps) != {'included', 'excluded'} or not isinstance(deps['included'], list) or not isinstance(deps['excluded'], list):
        return {'valid': False, 'errors': ['invalid manifest structure']}
    names = []
    for item in files:
        if (not isinstance(item, dict) or set(item) != {'path', 'sha256', 'size'}
                or not _safe_name(item.get('path')) or not isinstance(item.get('sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', item['sha256'])
                or type(item.get('size')) is not int or item['size'] < 0):
            errors.append('invalid file descriptor'); continue
        name = item['path']
        if name in names:
            errors.append(f'duplicate path: {name}'); continue
        names.append(name)
        path = (root/name).resolve()
        if not path.is_relative_to(root.resolve()):
            errors.append(f'unsafe path: {name}'); continue
        if not path.is_file():
            errors.append(f'missing: {name}'); continue
        data = path.read_bytes()
        if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            errors.append(f'mismatch: {name}')
    if deps['included'] != names or any(not isinstance(x, str) for x in deps['excluded']):
        errors.append('invalid dependency list')
    return {'valid': not errors, 'errors': errors}
