"""Model-owned BindCraft2 preview and job-owned compilation handoff.

The pinned image resolves presets; this service never implements native settings.
No GPU execution, queue ownership, or scientific result publication lives here.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from paths import get_allowed_roots, get_results_dir, resolve_allowed_path
from services.bindcraft2_native import PIN, _canonical
from services.bindcraft2_typed import validate_request

IMAGE = Path('/mnt/BioModStack/apptainer/bindcraft2.sif')
SCRIPT = Path(__file__).resolve().parents[3] / 'scripts/compile_bindcraft2_campaign.py'


def _source_path(value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('BC2 source path must be a nonempty governed path')
    if Path(value).is_absolute():
        candidate = Path(value).resolve(strict=True)
        if not any(candidate.is_relative_to(root.resolve()) for root in get_allowed_roots().values()):
            raise ValueError('BC2 source path is outside governed roots')
    else:
        candidate = resolve_allowed_path(value)
    if not candidate.is_file() or candidate.suffix.lower() not in {'.pdb', '.cif', '.fasta', '.fa'}:
        raise ValueError('BC2 source must be an existing PDB, CIF or FASTA file')
    return candidate


def _sources(request: dict) -> list[tuple[str, int | None, Path]]:
    result = []
    for index, row in enumerate(request.get('targets', [])):
        result.append(('target', index, _source_path(row['target_path'])))
    if request.get('binder_scaffold'):
        path = _source_path(request['binder_scaffold'])
        if path.suffix.lower() not in {'.pdb', '.cif'}:
            raise ValueError('BC2 binder scaffold must be a structure')
        result.append(('scaffold', None, path))
    return result


def _identity(sources: list[tuple[str, int | None, Path]]) -> list[dict]:
    return [{'role': role, 'index': index, 'path': str(path),
             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            for role, index, path in sources]


def _native_compile(request: dict, destination: Path, *, image: Path = IMAGE,
                    script: Path = SCRIPT) -> dict:
    if not image.is_file():
        raise ValueError('Pinned BC2 image is unavailable')
    if not destination.is_absolute():
        raise ValueError('BC2 compilation destination must be absolute')
    # Explicit binds allow arbitrary governed input roots and the job's output root.
    # Bind an existing managed root, not a nonexistent preview/source child.
    binds = {str(script.parents[1]), str(get_results_dir().resolve())}
    for root in get_allowed_roots().values():
        root = root.resolve()
        if root.exists() and any(Path(row['target_path']).is_relative_to(root)
                                 for row in request.get('targets', [])):
            binds.add(str(root))
    if request.get('binder_scaffold'):
        scaffold = Path(request['binder_scaffold'])
        for root in get_allowed_roots().values():
            if root.exists() and scaffold.is_relative_to(root.resolve()):
                binds.add(str(root))
    command = ['apptainer', 'exec']
    for root in sorted(binds):
        command.extend(['--bind', f'{root}:{root}'])
    command.extend([str(image), 'python3', str(script), str(destination)])
    completed = subprocess.run(command, input=json.dumps(request, allow_nan=False), text=True,
                               capture_output=True, timeout=120, check=False)
    if completed.returncode:
        raise ValueError('Pinned BC2 compilation failed: ' + completed.stderr[-2000:])
    compiled = json.loads(completed.stdout)
    if compiled.get('upstream_commit') != PIN:
        raise ValueError('BC2 compilation source pin mismatch')
    return compiled


def _materialized(request: dict, sources: list[tuple[str, int | None, Path]],
                  destination: Path, *, copy: bool) -> dict:
    mapped = json.loads(json.dumps(request))
    for role, index, path in sources:
        staged = destination / 'sources' / (f'target_{index}{path.suffix.lower()}' if role == 'target' else f'binder_scaffold{path.suffix.lower()}')
        if copy:
            staged.parent.mkdir(parents=True, exist_ok=True)
            if staged.exists():
                if staged.read_bytes() != path.read_bytes():
                    raise ValueError('Existing BC2 source snapshot differs')
            else:
                shutil.copyfile(path, staged)
        if role == 'target':
            mapped['targets'][index]['target_path'] = str(staged)
        else:
            mapped['binder_scaffold'] = str(staged)
    return mapped


def _compile(settings: dict, destination: Path, *, copy: bool,
             compiler: Callable[[dict, Path], dict]) -> dict:
    validate_request(settings)
    destination = destination.resolve()
    sources = _sources(settings)
    before = _identity(sources)
    native_request = _materialized(settings, sources, destination, copy=copy)
    if copy:
        for role, index, source in sources:
            staged = Path(native_request['targets'][index]['target_path'] if role == 'target'
                          else native_request['binder_scaffold'])
            original = next(row for row in before if row['role'] == role and row['index'] == index)
            if hashlib.sha256(staged.read_bytes()).hexdigest() != original['sha256']:
                raise ValueError('BC2 materialized source differs from previewed source')
    compiled = compiler(native_request, destination / 'campaign')
    if _identity(sources) != before:
        raise ValueError('BC2 source changed during compilation')
    if compiled.get('requested_settings') != native_request:
        raise ValueError('BC2 native compilation did not retain the materialized request')
    # Preserve the saved operator request separately from CLI-materialized paths.
    compiled['requested_settings'] = settings
    compiled['request_sha256'] = hashlib.sha256(_canonical(settings)).hexdigest()
    if compiled.get('effective_sha256') != hashlib.sha256(_canonical(compiled['effective_settings'])).hexdigest():
        raise ValueError('BC2 native compilation digest mismatch')
    return {'compiled': compiled, 'sources': before,
            'requested_settings': settings, 'effective_settings': compiled['effective_settings'],
            'effective_sha256': compiled['effective_sha256'],
            'request_sha256': hashlib.sha256(_canonical(settings)).hexdigest(),
            'sweep_budget': compiled['sweep_budget']}


def preview_campaign(settings: dict, *, compiler: Callable[[dict, Path], dict] = _native_compile) -> dict:
    """Native CPU preview against a deterministic logical root; no directory created."""
    validate_request(settings)
    sources = _sources(settings)
    token = hashlib.sha256(_canonical({'request': settings, 'sources': _identity(sources)})).hexdigest()
    destination = get_results_dir().resolve() / '.bc2-preview' / token
    result = _compile(settings, destination, copy=False, compiler=compiler)
    result.pop('compiled')
    result['preview_digest'] = hashlib.sha256(_canonical(result)).hexdigest()
    result['note'] = 'Job-owned materialization recompiles with its actual paths; read back that effective digest.'
    return result


def materialize_campaign(settings: dict, output_dir: Path, *, preview_digest: str,
                         compiler: Callable[[dict, Path], dict] = _native_compile) -> dict:
    """Copy governed sources, compile pinned native settings, persist receipt for Nextflow."""
    if preview_campaign(settings, compiler=compiler)['preview_digest'] != preview_digest:
        raise ValueError('BC2 preview is stale or source bytes changed')
    destination = Path(output_dir).resolve() / 'bindcraft2'
    if not destination.is_relative_to(get_results_dir().resolve()):
        raise ValueError('BC2 campaign must be inside the managed job output root')
    result = _compile(settings, destination, copy=True, compiler=compiler)
    compiled = result['compiled']
    receipt_path = destination / 'compilation.json'
    data = _canonical(compiled) + b'\n'
    if receipt_path.exists() and receipt_path.read_bytes() != data:
        raise ValueError('Existing BC2 compilation differs')
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    if not receipt_path.exists():
        receipt_path.write_bytes(data)
    return {'bc2_compilation': str(receipt_path), 'bc2_campaign_dir': str(destination),
            'bindcraft2_settings': settings, 'bc2_effective_sha256': result['effective_sha256'],
            'bc2_effective_settings': result['effective_settings'],
            'bc2_source_identities': result['sources'], 'bc2_request_sha256': result['request_sha256'],
            'bc2_sweep_budget': result['sweep_budget']}


def read_campaign_receipt(output_dir: Path) -> dict:
    """Exact job-owned settings and digest readback, without reparsing result scores."""
    path = Path(output_dir).resolve() / 'bindcraft2' / 'compilation.json'
    if not path.is_relative_to(get_results_dir().resolve()):
        raise ValueError('BC2 output is outside managed results')
    compiled = json.loads(path.read_text())
    if compiled.get('upstream_commit') != PIN or compiled.get('effective_sha256') != hashlib.sha256(_canonical(compiled['effective_settings'])).hexdigest():
        raise ValueError('BC2 persisted effective settings digest differs')
    if compiled['native_request'].get('project_folder') != str(path.parent / 'campaign'):
        raise ValueError('BC2 receipt is not bound to this output')
    return {'upstream_commit': PIN, 'requested_settings': compiled['requested_settings'],
            'effective_settings': compiled['effective_settings'], 'effective_sha256': compiled['effective_sha256'],
            'request_sha256': compiled['request_sha256'], 'sweep_budget': compiled['sweep_budget']}
