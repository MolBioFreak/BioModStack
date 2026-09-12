#!/usr/bin/env python3
"""Native sequence-file membership and byte custody; no scientific scoring.

Called by the real sequence-only workflow after Run and Filter finish. The
shared bridge still owns transport, completion and import transactions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

SCHEMA = 'bms.sequence-design-native-results.v1'
RESULT_PATH = 'results/sequence_design_results.json'
MODES = {'fampnn': {'design', 'fixed_backbone', 'binder_design'}, 'proteinmpnn': {'design'}}


class SequenceDesignResultError(ValueError):
    pass


def digest(payload: bytes) -> dict:
    return {'sha256': hashlib.sha256(payload).hexdigest(), 'size_bytes': len(payload)}


def _object(payload: bytes, label: str) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SequenceDesignResultError(f'Duplicate {label} key: {key}')
            result[key] = value
        return result
    try:
        value = json.loads(payload, object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(SequenceDesignResultError(f'Nonfinite {label} value')))
    except (ValueError, UnicodeError) as exc:
        raise SequenceDesignResultError(f'Invalid {label}: {exc}') from exc
    if not isinstance(value, dict):
        raise SequenceDesignResultError(f'{label} must be an object')
    return value


def _native_metadata(payload: bytes, name: str, engine: str) -> dict:
    value = _object(payload, 'native sequence metadata')
    if value.get('design') != name or not isinstance(value.get('sequence'), str) or not value['sequence']:
        raise SequenceDesignResultError(f'Native sequence identity/sequence mismatch: {name}')
    score_keys = ('score',) if engine == 'proteinmpnn' else (
        'fampnn_avg_psce', 'fampnn_max_residue_psce', 'fampnn_min_residue_psce')
    for key in score_keys:
        if key not in value and engine == 'fampnn':
            continue  # Sequence-only output need not claim sidechain confidence.
        number = value.get(key)
        try:
            finite = not isinstance(number, bool) and isinstance(number, (int, float, str)) and math.isfinite(float(number))
        except (TypeError, ValueError, OverflowError):
            finite = False
        if not finite:
            raise SequenceDesignResultError(f'Invalid native {key}: {name}')
    return value


def _check_selection(engine: str, mode: str) -> None:
    if mode not in MODES.get(engine, set()):
        raise SequenceDesignResultError('Unsupported public sequence result engine/mode')


def build_result_index(*, engine: str, mode: str, source: Path, settings: Path,
                       unfiltered_dir: Path, filtered_dir: Path) -> dict:
    """Bind actual staged tuple members, allowing native empty filtered output."""
    _check_selection(engine, mode)
    source_bytes = source.read_bytes()
    if not source_bytes:
        raise SequenceDesignResultError('Missing source structure bytes')
    settings_bytes = settings.read_bytes()
    native_settings = _object(settings_bytes, 'sequence settings')
    if native_settings.get('sequence_design_engine') != engine or native_settings.get('sequence_design_mode') != mode:
        raise SequenceDesignResultError('Source settings disagree with native result selection')
    if not unfiltered_dir.is_dir() or not filtered_dir.is_dir():
        raise SequenceDesignResultError('Native unfiltered/filtered collection was not emitted')
    unfiltered = {path.stem: path for path in unfiltered_dir.glob('*.pdb')}
    filtered = {path.stem: path for path in filtered_dir.glob('*.pdb')}
    if not unfiltered or not set(filtered) <= set(unfiltered):
        raise SequenceDesignResultError('Missing canonical candidates or foreign filtered membership')
    if {path.stem for path in unfiltered_dir.glob('*.json')} != set(unfiltered):
        raise SequenceDesignResultError('Canonical sequence structures and metrics must pair exactly')
    if {path.stem for path in filtered_dir.glob('*.json')} != set(filtered):
        raise SequenceDesignResultError('Filtered sequence structures and metrics must pair exactly')
    final_subdir = 'fampnn_filtered' if engine == 'fampnn' else 'mpnn_filtered'
    candidates = []
    for name, pdb in sorted(unfiltered.items()):
        metrics = pdb.with_suffix('.json')
        structure_bytes, metrics_bytes = pdb.read_bytes(), metrics.read_bytes()
        if not structure_bytes:
            raise SequenceDesignResultError(f'Empty native structure: {name}')
        _native_metadata(metrics_bytes, name, engine)
        selected = name in filtered
        if selected and (filtered[name].read_bytes() != structure_bytes or filtered[name].with_suffix('.json').read_bytes() != metrics_bytes):
            raise SequenceDesignResultError(f'Native filter changed candidate bytes: {name}')
        candidates.append({'name': name, 'selected': selected,
            'structure': {'path': f'pdb_files/{name}.pdb', **digest(structure_bytes)},
            'metrics': {'path': f'pdb_files/{name}.json', **digest(metrics_bytes)},
            'filtered_structure_path': f'collected/{final_subdir}/{name}.pdb' if selected else None,
            'filtered_metrics_path': f'collected/{final_subdir}/{name}.json' if selected else None})
    return {'schema': SCHEMA, 'engine': engine, 'mode': mode,
        'source': {'name': source.name, **digest(source_bytes)},
        'settings': native_settings, 'settings_bytes': digest(settings_bytes),
        'unfiltered_count': len(candidates), 'selected_count': len(filtered),
        'candidates': candidates}


def _published_bytes(root: Path, relative: str) -> bytes:
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(part in {'.', '..'} for part in path.parts):
        raise SequenceDesignResultError('Non-relative native publication path')
    candidate = root
    for part in path.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise SequenceDesignResultError('Symlink in native publication')
    if not candidate.is_file():
        raise SequenceDesignResultError(f'Missing native publication: {relative}')
    return candidate.read_bytes()


def load_result_index(root: Path, engine: str, mode: str) -> tuple[dict, list[tuple[dict, dict]], str]:
    """Validate the complete native collection before any database mutation."""
    _check_selection(engine, mode)
    if root.is_symlink():
        raise SequenceDesignResultError('Symlinked result root')
    payload = _published_bytes(root, RESULT_PATH)
    index = _object(payload, 'sequence result index')
    if index.get('schema') != SCHEMA or (index.get('engine'), index.get('mode')) != (engine, mode):
        raise SequenceDesignResultError('Foreign sequence result index')
    settings = index.get('settings')
    if not isinstance(settings, dict) or (settings.get('sequence_design_engine'), settings.get('sequence_design_mode')) != (engine, mode):
        raise SequenceDesignResultError('Foreign native sequence settings')
    source = index.get('source')
    if not isinstance(source, dict) or not isinstance(source.get('name'), str) or not source['name']:
        raise SequenceDesignResultError('Missing native sequence source identity')
    for identity in (source, index.get('settings_bytes')):
        if not isinstance(identity, dict) or not isinstance(identity.get('sha256'), str) or len(identity['sha256']) != 64 or any(ch not in '0123456789abcdef' for ch in identity['sha256']) or type(identity.get('size_bytes')) is not int or identity['size_bytes'] <= 0:
            raise SequenceDesignResultError('Invalid native input byte identity')
    rows = index.get('candidates')
    if not isinstance(rows, list) or not rows:
        raise SequenceDesignResultError('Missing canonical sequence membership')
    seen, selected, validated = set(), 0, []
    final_subdir = 'fampnn_filtered' if engine == 'fampnn' else 'mpnn_filtered'
    for row in rows:
        if not isinstance(row, dict):
            raise SequenceDesignResultError('Invalid native candidate entry')
        name = row.get('name')
        if not isinstance(name, str) or not name or '/' in name or '\\' in name or name in {'.', '..'} or name in seen or type(row.get('selected')) is not bool:
            raise SequenceDesignResultError('Invalid/duplicate native candidate identity')
        seen.add(name)
        metadata = None
        for role, suffix in (('structure', 'pdb'), ('metrics', 'json')):
            binding = row.get(role)
            expected = f'pdb_files/{name}.{suffix}'
            if not isinstance(binding, dict) or binding.get('path') != expected:
                raise SequenceDesignResultError('Native candidate publication path mismatch')
            content = _published_bytes(root, expected)
            if digest(content) != {k: binding.get(k) for k in ('sha256', 'size_bytes')}:
                raise SequenceDesignResultError(f'Native candidate byte mismatch: {name}')
            if role == 'metrics':
                metadata = _native_metadata(content, name, engine)
            selected_path = row.get('filtered_' + role + '_path')
            expected_selected = f'collected/{final_subdir}/{name}.{suffix}' if row['selected'] else None
            if selected_path != expected_selected:
                raise SequenceDesignResultError('Native filtered membership path mismatch')
            if row['selected'] and _published_bytes(root, selected_path) != content:
                raise SequenceDesignResultError(f'Native selected bytes changed: {name}')
        selected += int(row['selected'])
        validated.append((row, metadata))
    if type(index.get('unfiltered_count')) is not int or index['unfiltered_count'] != len(rows) or type(index.get('selected_count')) is not int or index['selected_count'] != selected:
        raise SequenceDesignResultError('Native sequence counts disagree with membership')
    return index, validated, hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('engine', 'mode', 'source', 'settings', 'unfiltered-dir', 'filtered-dir', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    index = build_result_index(engine=args.engine, mode=args.mode, source=Path(args.source),
        settings=Path(args.settings), unfiltered_dir=Path(args.unfiltered_dir), filtered_dir=Path(args.filtered_dir))
    Path(args.output).write_text(json.dumps(index, allow_nan=False, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
