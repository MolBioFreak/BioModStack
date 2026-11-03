"""Retain selected La motif references beside the unchanged native config."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import yaml


def motif_references(config_path):
    config_path = Path(config_path)
    try:
        cfg = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError:
        # Native evaluation owns config validation; custody is not a new gate.
        return {}
    generation = cfg.get('generation') if isinstance(cfg, dict) else None
    dataset = generation.get('dataset') if isinstance(generation, dict) else None
    if not isinstance(dataset, dict):
        return {}
    task = dataset.get('motif_task_name')
    motifs = dataset.get('motif_dict_cfg')
    if not isinstance(task, str) or not isinstance(motifs, dict):
        return {}
    motif = motifs.get(task)
    if not isinstance(motif, dict):
        return {}
    sidecar = config_path.with_name(config_path.name + '.motifs.json')
    retained = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
    if task in retained:
        return {task: config_path.parent / retained[task]['path']}
    path = motif.get('motif_pdb_path')
    # Native DATA_PATH interpolations belong to the selected asset owner, not
    # controller path discovery. Historical missing files remain native errors.
    if not isinstance(path, str) or '${' in path:
        return {}
    path = Path(path)
    path = path if path.is_absolute() else config_path.parent / path
    return {task: path} if path.is_file() else {}


def retain_motif_references(source_config, target_config, *, allowed_roots=None):
    """Freeze bytes and relative execution bindings; never rewrite original YAML."""
    target_config = Path(target_config)
    retained = {}
    for task, source in motif_references(source_config).items():
        if allowed_roots is not None:
            from .portable_inputs import _contained
            source = _contained(source, [Path(p).resolve() for p in allowed_roots])
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        relative = Path('motif_references') / digest / source.name
        target = target_config.parent / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        retained[task] = {'path': relative.as_posix(), 'sha256': digest}
    target_config.with_name(target_config.name + '.motifs.json').write_text(json.dumps(retained, indent=2) + '\n')
    return retained
