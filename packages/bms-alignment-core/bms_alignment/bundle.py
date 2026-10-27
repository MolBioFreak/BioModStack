"""Portable directory bundles: relative filenames and SHA-256 verification."""
import hashlib
import json
from pathlib import Path, PurePosixPath


def make_bundle(source: Path, destination: Path, filenames: list[str]) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    entries = []
    for name in filenames:
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or not relative.parts or name == 'manifest.json':
            raise ValueError('unsafe bundle path')
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
    manifest = {'schema': 'bms.alignment-bundle.v1', 'files': entries, 'dependencies': {'included': filenames, 'excluded': []}}
    (destination/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


def verify_bundle(root: Path) -> dict:
    manifest = json.loads((root/'manifest.json').read_text())
    errors = []
    for item in manifest['files']:
        name = item['path']
        rel = PurePosixPath(name)
        path = (root/name).resolve()
        if rel.is_absolute() or '..' in rel.parts or not path.is_relative_to(root.resolve()):
            errors.append(f'unsafe path: {name}'); continue
        if not path.is_file():
            errors.append(f'missing: {name}'); continue
        data = path.read_bytes()
        if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            errors.append(f'mismatch: {name}')
    return {'valid': not errors, 'errors': errors}
