"""Selected blind-pose request and attachment authority; no score interpretation.

The caller resolves an independent target structure server-side. The selected
Design PDB supplies *binder sequence only*. No candidate target chain, pose,
template, MSA, interface restraint or native geometry enters the predictor.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import sys
import uuid
from pathlib import Path
from typing import Sequence

from sqlalchemy import select

from database import Design, Job, JobArtifact
from paths import get_allowed_roots, get_inputs_dir, get_code_root, resolve_runtime_data_path

SCHEMA = 'bms.blind-pose.selected.v1'
KEY = 'blind_pose_selected'
_SAFE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*\Z')

class BlindPoseError(ValueError):
    pass


def _regular(path: Path) -> bytes:
    if not stat.S_ISREG(path.lstat().st_mode) or path.parent.is_symlink():
        raise BlindPoseError('structure or result must be a regular file')
    return path.read_bytes()


def _source(path: str) -> tuple[Path, bytes]:
    resolved = resolve_runtime_data_path(path)
    if not any(resolved.is_relative_to(root.resolve()) for root in get_allowed_roots().values()):
        raise BlindPoseError('structure path outside managed roots')
    if resolved.suffix.lower() not in {'.pdb', '.cif', '.mmcif'}:
        raise BlindPoseError('blind pose sequence sources must be PDB or CIF-backed')
    return resolved, _regular(resolved)


def prepare_selected(source_job: Job, designs: Sequence[Design], *, target_pdb: str,
                     binder_chains: dict[str, list[str]], target_chains: list[str],
                     directory: Path) -> dict:
    """Snapshot an exact subset, independent target and v1 leaf manifest.

    `target_pdb` is a server-resolved target-state path, not a client path.
    Source ownership is deliberately exact (the requested source Job), not a
    global Design lookup and not a filename/sequence match.
    """
    if not designs or len({d.id for d in designs}) != len(designs):
        raise BlindPoseError('select distinct Design IDs')
    if set(binder_chains) != {d.id for d in designs}:
        raise BlindPoseError('every selected design needs explicit binder chain roles')
    if not target_chains or len(target_chains) != len(set(target_chains)) or any(
        not isinstance(c, str) or len(c) != 1 for c in target_chains
    ):
        raise BlindPoseError('explicit distinct target chain IDs required')
    target_path, target_bytes = _source(target_pdb)
    target_name = 'target' + target_path.suffix.lower()
    selected = []
    snapshots = []
    for index, design in enumerate(designs):
        if design.job_id != source_job.id:
            raise BlindPoseError('foreign selected Design owner')
        chains = binder_chains[design.id]
        if not isinstance(chains, list) or not chains or len(chains) != len(set(chains)) or any(
            not isinstance(c, str) or len(c) != 1 for c in chains
        ) or set(chains) & set(target_chains):
            raise BlindPoseError('explicit distinct binder chains may not overlap target chains')
        path, content = _source(design.pdb_path)
        key = f'candidate-{index:04d}'
        filename = key + path.suffix.lower()
        selected.append({'candidate_key': key, 'source_pdb': filename, 'binder_chains': chains})
        snapshots.append((filename, content, design.id, hashlib.sha256(content).hexdigest()))
    # Validate sequences and roles *before* creating any inputs. Import the same
    # leaf compiler used by actual execution, avoiding a second parser.
    scripts = str(get_code_root() / 'scripts')
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from run_binder_blind_pose import compile_selected_inputs
    import tempfile
    get_inputs_dir().mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=get_inputs_dir()) as tmp:
        scratch = Path(tmp)
        for filename, content, _, _ in snapshots:
            (scratch / filename).write_bytes(content)
        (scratch / target_name).write_bytes(target_bytes)
        compiled = compile_selected_inputs({'schema_version': 1, 'target_chains': target_chains,
                                            'candidates': selected}, scratch, scratch / target_name)
    directory.mkdir(parents=True, exist_ok=False)
    try:
        for filename, content, _, _ in snapshots:
            (directory / filename).write_bytes(content)
        (directory / target_name).write_bytes(target_bytes)
        manifest = {'schema_version': 1, 'target_chains': target_chains, 'candidates': selected}
        (directory / 'selection.json').write_text(json.dumps(manifest, sort_keys=True) + '\n')
        binding = {'schema': SCHEMA, 'source_job_id': source_job.id,
                   'target_source_sha256': hashlib.sha256(target_bytes).hexdigest(),
                   'target_source_name': target_path.name, 'target_snapshot_name': target_name,
                   'candidates': [{'candidate_key': row['candidate_key'], 'design_id': design_id,
                                   'source_sha256': digest, 'source_pdb': row['source_pdb'], 'binder_chains': row['binder_chains']}
                                  for row, (_, _, design_id, digest) in zip(selected, snapshots)],
                   'target_chains': target_chains,
                   'component_sha256': {row['candidate_key']: hashlib.sha256(
                       json.dumps(row['components'], sort_keys=True).encode()).hexdigest() for row in compiled}}
        (directory / 'binding.json').write_text(json.dumps(binding, sort_keys=True) + '\n')
        return binding
    except Exception:
        shutil.rmtree(directory)
        raise


def launch_params(directory: Path, *, variant: str, model_id_or_path: str,
                  num_loops: int, num_sampling_steps: int,
                  num_diffusion_samples: int, seed: int | None) -> dict:
    if variant not in ('fast', 'full') or not isinstance(model_id_or_path, str) or not re.fullmatch(r'[A-Za-z0-9._/-]*', model_id_or_path):
        raise BlindPoseError('unsupported ESMFold2 variant or model identity')
    if any(type(v) is not int or v < 1 for v in (num_loops, num_sampling_steps, num_diffusion_samples)):
        raise BlindPoseError('positive integer sampling settings required')
    if num_loops > 12 or num_sampling_steps > 1000 or num_diffusion_samples > 8:
        raise BlindPoseError('ESMFold2 sampling settings exceed native model bounds')
    if seed is not None and (type(seed) is not int or seed < 0):
        raise BlindPoseError('seed must be a nonnegative integer or null')
    manifest = json.loads((directory / 'selection.json').read_text())
    binding = json.loads((directory / 'binding.json').read_text())
    return {'blind_pose_selection_manifest': str(directory / 'selection.json'),
            'blind_pose_candidate_pdbs': [str(directory / row['source_pdb']) for row in manifest['candidates']],
            'target_pdb': str(directory / binding['target_snapshot_name']),
            'esmfold2_validation_variant': variant, 'esmf_model_id_or_path': model_id_or_path,
            'esmfold2_validation_num_loops': num_loops,
            'esmfold2_validation_num_sampling_steps': num_sampling_steps,
            'esmfold2_validation_num_diffusion_samples': num_diffusion_samples,
            'esmf_seed': seed}


def _requested_settings(params: dict) -> dict:
    return {'model_variant': params['esmfold2_validation_variant'],
            'model_id_or_path': params['esmf_model_id_or_path'],
            'num_loops': params['esmfold2_validation_num_loops'],
            'num_sampling_steps': params['esmfold2_validation_num_sampling_steps'],
            'num_diffusion_samples': params['esmfold2_validation_num_diffusion_samples'],
            'seed': params.get('esmf_seed')}


def _result_inventory(root: Path, binding: dict, requested_settings: dict) -> tuple[dict, dict]:
    requested_samples = requested_settings['num_diffusion_samples']
    receipt_path = root / 'blind_pose_results' / 'blind_pose_receipt.json'
    receipt = json.loads(_regular(receipt_path))
    expected = {row['candidate_key']: row for row in binding['candidates']}
    rows = receipt.get('records')
    if (receipt.get('assessment') != 'blind_pose_sequence_only_complex_cofold' or
        receipt.get('predictor') != 'esmfold2' or not isinstance(rows, list) or not rows):
        raise BlindPoseError('blind pose native receipt is missing or wrong')
    seen = set()
    settings = receipt.get('requested_settings')
    if settings != requested_settings:
        raise BlindPoseError('native settings differ from selected request')
    names = {'blind_pose_results/blind_pose_receipt.json'}
    for row in rows:
        key, sample = row.get('candidate_key'), row.get('sample_id')
        if key not in expected or not isinstance(sample, str) or not re.fullmatch(re.escape(key) + r'_[0-9]+', sample):
            raise BlindPoseError('native sample has no exact selected identity')
        if sample in seen or row.get('source_sha256') != expected[key]['source_sha256'] or row.get('target_sha256') != binding['target_source_sha256'] or row.get('binder_chains') != expected[key]['binder_chains'] or row.get('target_chains') != binding['target_chains'] or row.get('classification') != 'unclassified' or row.get('conditioning') != 'sequence_only_complex_cofold_no_templates_no_interface_restraints':
            raise BlindPoseError('native sample source or classification disagrees')
        seen.add(sample)
        for role, suffix in (('cif', '.cif'), ('metrics', '.metrics.json')):
            relative = f'blind_pose_results/{key}/{sample}{suffix}'
            if row.get(role) != f'{key}/{sample}{suffix}':
                raise BlindPoseError('native sample artifact pairing disagrees')
            names.add(relative)
        native_metrics = json.loads(_regular(root / f'blind_pose_results/{key}/{sample}.metrics.json'))
        if native_metrics != row.get('raw_metrics') or native_metrics.get('sample_id') != sample or native_metrics.get('cif') != sample + '.cif':
            raise BlindPoseError('native metrics pairing disagrees')
    if set(expected) != {row['candidate_key'] for row in rows}:
        raise BlindPoseError('selected candidate missing from native output')
    for key in expected:
        native_root = root / 'blind_pose_results' / key
        components = json.loads(_regular(native_root / 'components.json'))
        if hashlib.sha256(json.dumps(components, sort_keys=True).encode()).hexdigest() != binding['component_sha256'][key]:
            raise BlindPoseError('predictor components differ from selected sequence-only request')
        manifest = json.loads(_regular(native_root / 'manifest.json'))
        selected_samples = [row for row in rows if row['candidate_key'] == key]
        if (manifest.get('workflow') != 'esmfold2' or manifest.get('sequence_name') != key or
            manifest.get('sample_count') != requested_samples or len(selected_samples) != requested_samples or
            {sample.get('sample_id') for sample in manifest.get('samples', [])} !=
                {row['sample_id'] for row in selected_samples}):
            raise BlindPoseError('native sample roster differs from requested subset')
        names.update((f'blind_pose_results/{key}/components.json',
                      f'blind_pose_results/{key}/manifest.json'))
    files = {}
    for name in sorted(names):
        content = _regular(root / name)
        files[name] = {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}
    return receipt, files


def _verify_request_snapshots(job: Job, binding: dict) -> None:
    params = job.params
    manifest = Path(params['blind_pose_selection_manifest'])
    if manifest.name != 'selection.json' or manifest.is_symlink():
        raise BlindPoseError('selection manifest path changed')
    selected = json.loads(_regular(manifest))
    candidates = selected.get('candidates')
    if (selected.get('schema_version') != 1 or selected.get('target_chains') != binding['target_chains']
            or not isinstance(candidates, list) or len(candidates) != len(binding['candidates'])):
        raise BlindPoseError('selection manifest differs from persisted request')
    paths = params['blind_pose_candidate_pdbs']
    if not isinstance(paths, list) or len(paths) != len(candidates):
        raise BlindPoseError('candidate path roster changed')
    for row, saved, path in zip(candidates, binding['candidates'], paths):
        if (row != {'candidate_key': saved['candidate_key'],
                    'source_pdb': saved['source_pdb'],
                    'binder_chains': saved['binder_chains']}
                or Path(path) != manifest.parent / row['source_pdb']
                or hashlib.sha256(_regular(Path(path))).hexdigest() != saved['source_sha256']):
            raise BlindPoseError('selected candidate snapshot changed')
    target = Path(params['target_pdb'])
    if target != manifest.parent / binding['target_snapshot_name'] or hashlib.sha256(_regular(target)).hexdigest() != binding['target_source_sha256']:
        raise BlindPoseError('independent target snapshot changed')


async def publish_selected(job: Job, root: Path, session) -> dict:
    """Attach immutable native evidence to a child Job, never overwrite parents."""
    binding = (job.params or {}).get(KEY)
    if not isinstance(binding, dict) or binding.get('schema') != SCHEMA:
        raise BlindPoseError('missing persisted selected request binding')
    _verify_request_snapshots(job, binding)
    root = root.absolute()
    if not job.output_dir or Path(job.output_dir).absolute() != root or root.is_symlink():
        raise BlindPoseError('job output root differs from published result root')
    native, files = _result_inventory(root, binding, _requested_settings(job.params))
    previous = (job.provenance or {}).get(KEY)
    identity = {'schema': SCHEMA, 'attempt': job.retry_count or 0,
                'remote_attempt_id': job.remote_attempt_id, 'binding': binding,
                'files': files}
    if previous is not None and previous != identity:
        raise BlindPoseError('blind pose replay changed')
    existing = (await session.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == job.id))).all()
    owned = {a.logical_path: a for a in existing if a.logical_path.startswith('blind_pose/')}
    if previous is None and owned:
        raise BlindPoseError('orphan blind pose artifacts')
    if previous is not None and set(owned) != {'blind_pose/' + name for name in files}:
        raise BlindPoseError('registered blind pose artifacts changed')
    for name, meta in files.items():
        key = 'blind_pose/' + name
        path = root / name
        a = owned.get(key)
        if a is None:
            a = JobArtifact(id=str(uuid.uuid4()), owner_job_id=job.id, attempt=identity['attempt'],
                            logical_path=key, storage_path=str(path), sha256=meta['sha256'],
                            bytes=meta['bytes'], media_type='chemical/x-mmcif' if name.endswith('.cif') else 'application/json',
                            provenance={'schema': SCHEMA, 'remote_attempt_id': job.remote_attempt_id})
            session.add(a)
        elif (a.attempt, a.storage_path, a.sha256, a.bytes) != (identity['attempt'], str(path), meta['sha256'], meta['bytes']):
            raise BlindPoseError('registered blind pose artifact changed')
    job.provenance = {**(job.provenance or {}), KEY: identity}
    await session.flush()
    return native


async def read_selected(job: Job, session) -> dict:
    receipt = (job.provenance or {}).get(KEY)
    if not isinstance(receipt, dict) or receipt.get('schema') != SCHEMA:
        raise BlindPoseError('blind pose result not published')
    if receipt['attempt'] != (job.retry_count or 0) or receipt['remote_attempt_id'] != job.remote_attempt_id:
        raise BlindPoseError('blind pose attempt changed')
    if not job.output_dir or Path(job.output_dir).is_symlink():
        raise BlindPoseError('published result root changed')
    _verify_request_snapshots(job, receipt['binding'])
    native, files = _result_inventory(Path(job.output_dir).absolute(), receipt['binding'],
                                       _requested_settings(job.params))
    if files != receipt['files']:
        raise BlindPoseError('blind pose result bytes changed')
    artifacts = (await session.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == job.id))).all()
    selected = {a.logical_path: a for a in artifacts if a.logical_path.startswith('blind_pose/')}
    if set(selected) != {'blind_pose/' + name for name in files}:
        raise BlindPoseError('blind pose artifact registry changed')
    for name, entry in files.items():
        a = selected['blind_pose/' + name]
        if (a.attempt, a.storage_path, a.sha256, a.bytes) != (receipt['attempt'], str(Path(job.output_dir).absolute() / name), entry['sha256'], entry['bytes']):
            raise BlindPoseError('blind pose artifact registry disagrees')
    return {'publication': receipt, 'native': native,
            'records': [{**row, 'design_id': next(c['design_id'] for c in receipt['binding']['candidates'] if c['candidate_key'] == row['candidate_key'])} for row in native['records']]}
