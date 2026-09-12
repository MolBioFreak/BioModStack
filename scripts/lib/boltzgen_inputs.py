"""Preserve the native prepared spec and its typed relative file references.

No regeneration or path replacement: the directory is a Nextflow input artifact.
Only file/protein entity paths are native inputs, not arbitrary YAML strings.
"""
from pathlib import Path
import hashlib
import shutil
import yaml


def prepared_files(config):
    config = Path(config).resolve()
    root = config.parent
    found = {}

    def visit(path):
        path = Path(path)
        if not path.is_relative_to(root):
            raise ValueError('BoltzGen prepared input escapes preparation directory')
        if not path.is_file():
            raise ValueError('BoltzGen prepared input is not a file: ' + str(path))
        relative = path.relative_to(root).as_posix()
        if relative in found:
            return
        raw = path.read_bytes()
        found[relative] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}
        if path.suffix.lower() not in {'.yaml', '.yml'}:
            return
        document = yaml.safe_load(raw)
        if not isinstance(document, dict):
            raise ValueError('BoltzGen prepared YAML must be an object')
        for entity in document.get('entities', []):
            for kind in ('file', 'protein'):
                spec = entity.get(kind)
                if not isinstance(spec, dict) or 'path' not in spec:
                    continue
                paths = spec['path'] if isinstance(spec['path'], list) else [spec['path']]
                for name in paths:
                    reference = Path(name)
                    if reference.is_absolute() or '..' in reference.parts:
                        raise ValueError('BoltzGen prepared references must be contained relative paths')
                    visit(path.parent / reference)
    visit(config)
    return found


def snapshot(config, output):
    config = Path(config).resolve()
    output = Path(output)
    files = prepared_files(config)
    output.mkdir(parents=True, exist_ok=False)
    for relative, identity in files.items():
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(config.parent / relative, destination)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != identity['sha256']:
            raise ValueError('BoltzGen prepared input changed during staging')
    return files


def input_identity(path):
    path = Path(path)
    config = path / 'boltzgen_input.yaml' if path.is_dir() else path
    return prepared_files(config)


def identity_digest(identity):
    import json
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--output')
    parser.add_argument('--expected-sha256')
    args = parser.parse_args()
    if args.expected_sha256:
        if identity_digest(input_identity(args.config)) != args.expected_sha256:
            raise ValueError('BoltzGen prepared input identity changed')
    elif args.output:
        snapshot(args.config, args.output)
    else:
        parser.error('output or expected-sha256 is required')
