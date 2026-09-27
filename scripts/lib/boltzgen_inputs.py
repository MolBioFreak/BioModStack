"""Preserve the native prepared spec and its typed relative file references.

No regeneration or path replacement: the directory is a Nextflow input artifact.
Only native file/protein/ligand paths are inputs, not arbitrary YAML strings.
Referenced scaffold YAML carries a native file spec at its document root.
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
        # Native scaffold YAML is a file spec at the document root, not an
        # entities document. Traverse its structural source as well.
        entities = document.get('entities', []) if 'entities' in document else [{'file': document}]
        for entity in entities:
            for kind in ('file', 'protein', 'ligand'):
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


def materialize_generation_input(params, output, *, allowed_input_roots):
    """Prepare an already normalized/resolved typed request once, before launch.

    The caller supplies the existing ownership-approved roots and an allocated
    attempt-private output directory. This does not normalize requests, resolve
    SAbDab scaffolds, install assets, or execute the native model. Supplied native
    YAML trees keep using snapshot(); they are not regenerated here.
    """
    import json
    from scripts import prep_boltzgen as prep
    from scripts.lib.portable_inputs import _contained, _identity

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    roots = [Path(root).resolve() for root in allowed_input_roots]
    copied, input_texts = {}, {}

    def stage(value):
        source = _contained(value, roots)
        if source in copied:
            return copied[source]
        # Exact path identity avoids same-basename collisions without chain or
        # source-role inference. Repeated references share one immutable copy.
        name = 'source_' + hashlib.sha256(str(source).encode()).hexdigest() + source.suffix
        identity, _ = _identity(source)
        destination = output / name
        shutil.copyfile(source, destination)
        if _identity(destination)[0] != identity:
            raise ValueError('BoltzGen source changed during preparation')
        copied[source] = name
        input_texts[name] = destination.read_text()
        return name

    args = prep.preparation_parser().parse_args(['--output_yaml', str(output / 'boltzgen_input.yaml')])
    for name in vars(args):
        if name == 'output_yaml':
            continue
        key = 'boltzgen_target_pdb_path' if name == 'target_pdb' else 'boltzgen_' + name
        if key in params and params[key] is not None:
            setattr(args, name, params[key])
    for name in ('input_pdb', 'ligand_pdb', 'dna_structure', 'target_pdb', 'scaffold_path'):
        if getattr(args, name):
            setattr(args, name, stage(getattr(args, name)))
    if args.nanobody_scaffold_specs:
        specs = json.loads(args.nanobody_scaffold_specs) if isinstance(args.nanobody_scaffold_specs, str) else args.nanobody_scaffold_specs
        specs = json.loads(json.dumps(specs))
        for scaffold in specs:
            scaffold['spec']['path'] = stage(scaffold['spec']['path'])
        args.nanobody_scaffold_specs = json.dumps(specs)
        prep._write_scaffold_yaml_files(Path(args.output_yaml), specs)
    config = prep.build_design_config(args, preview=True, input_texts=input_texts)
    Path(args.output_yaml).write_text(yaml.safe_dump(config, sort_keys=False))
    identity = input_identity(output)
    return {'boltzgen_yaml_config': str(output.resolve()),
            'boltzgen_prepared_sha256': identity_digest(identity)}


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
