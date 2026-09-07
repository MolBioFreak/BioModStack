"""Compact path-free execution settings from hash-bound producer receipts."""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, FiniteFloat

KEY = 'core_protein_execution_settings'
REQUEST_KEY = 'core_protein_requested_params'

class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid')

Scalar = StrictBool | StrictInt | FiniteFloat | StrictStr | None

class Setting(Closed):
    key: Literal['seed','model_variant','local_files_only','num_loops','num_sampling_steps',
        'num_diffusion_samples','msa_format','msa_max_sequences','msa_remove_insertions',
        'pdb_include_dna_rna','chain_id','cdr_only','force_field','max_iterations',
        'energy_tolerance','restraint_mode','antibody_chain','fix_structure']
    scope: str = Field(min_length=1, max_length=160)
    requested: Scalar
    effective: Scalar
    origin: Literal['request','workflow_default','runner_default','compute_tier']

class Source(Closed):
    scope: str = Field(min_length=1, max_length=160)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    size_bytes: int | None = Field(default=None, ge=0)

class ExecutionReceipt(Closed):
    model: Literal['esmfold2','openmm']
    artifact_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    settings: list[Setting]
    sources: list[Source]

class ExecutionSettings(Closed):
    status: Literal['ok','unavailable']
    reason: Literal['missing_execution_receipt','invalid_execution_receipt'] | None
    receipts: list[ExecutionReceipt]

ESM_KEYS = {'seed','model_variant','local_files_only','num_loops','num_sampling_steps',
    'num_diffusion_samples','msa_format','msa_max_sequences','msa_remove_insertions',
    'pdb_include_dna_rna','chain_id'}
OPENMM_KEYS = {'cdr_only','force_field','max_iterations','energy_tolerance','restraint_mode','antibody_chain','fix_structure'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _requested(original, model, key, scope):
    if scope.startswith('component:'):
        components = original.get('esmf_complex_components', original.get('complex_components', []))
        selected = [c for c in components if c.get('id') == scope.removeprefix('component:')]
        if len(selected) != 1:
            raise ValueError('ambiguous component request identity')
        original = selected[0]
        return key in original, original.get(key)
    prefix = 'esmf_' if model == 'esmfold2' else 'openmm_'
    alias = prefix + key
    if alias in original and key in original and canonical(original[alias]) != canonical(original[key]):
        raise ValueError('conflicting request aliases')
    name = alias if alias in original else key
    return name in original, original.get(name)


def prepare_receipt(job, root: Path, path: Path):
    """Hash the retained receipt bytes and independently bind requested origin.

    argv is validated internally and never copied into the API or Design metadata.
    Source file identity is the producer's actual staged-byte SHA, not a path alias.
    """
    from services.core_protein_result_contract import _artifact
    evidence, content = _artifact(root, str(path))
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError('execution receipt must be an object')
    if type(payload.get('schema_version')) is not int or payload['schema_version'] != 1 or type(payload.get('core_protein_scientific_contract')) is not int or payload['core_protein_scientific_contract'] != 1:
        raise ValueError('invalid execution receipt revision')
    original = (job.provenance or {}).get(REQUEST_KEY)
    if not isinstance(original, dict):
        raise ValueError('missing trusted request origin')
    model = payload['model']
    if model not in ('esmfold2','openmm'):
        raise ValueError('unsupported execution receipt')
    argv = payload['argv']
    if not isinstance(argv, list) or any(type(v) is not str for v in argv):
        raise ValueError('invalid execution argv')
    if model == 'esmfold2':
        runner_path = Path(__file__).resolve().parents[3] / 'scripts/run_esmfold2_inference.py'
        spec = importlib.util.spec_from_file_location('esm_receipt_parser', runner_path)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        try:
            effective = vars(runner.build_parser().parse_args(argv))
        except SystemExit as exc:
            raise ValueError('invalid execution argv') from exc
        component_effective = json.loads(effective['complex_components_json'] or '[]')
    else:
        effective = {}
        for key in OPENMM_KEYS:
            flag = '--' + key
            if key in ('cdr_only','fix_structure'):
                effective[key] = flag in argv
            elif flag in argv:
                if argv.count(flag) != 1: raise ValueError('duplicate execution argument')
                value = argv[argv.index(flag)+1]
                effective[key] = int(value) if key == 'max_iterations' else float(value) if key == 'energy_tolerance' else value
            elif key == 'restraint_mode':
                effective[key] = 'none'
    settings = []
    seen = set()
    for raw_key, item in payload['settings'].items():
        key = raw_key.split('.',1)[1] if raw_key.startswith('component:') else raw_key
        if key not in (ESM_KEYS if model == 'esmfold2' else OPENMM_KEYS):
            continue
        scope = item['scope']
        if (scope,key) in seen: raise ValueError('duplicate scoped setting')
        seen.add((scope,key))
        present, requested = _requested(original, model, key, scope)
        if canonical(item['requested']) != canonical(requested):
            raise ValueError('receipt requested settings differ from owning Job')
        if item['origin'] == 'request' and not present:
            raise ValueError('forged request origin')
        if present and not (model == 'openmm' and requested is None) and item['origin'] not in ('request','compute_tier'):
            raise ValueError('lost request origin')
        observed = effective
        if scope.startswith('component:'):
            selected = [c for c in component_effective if c.get('id') == scope.removeprefix('component:')]
            if len(selected) != 1: raise ValueError('ambiguous executed component')
            observed = selected[0]
        if key not in observed or canonical(observed[key]) != canonical(item['effective']):
            raise ValueError('effective settings differ from executed argv')
        settings.append(Setting(key=key, scope=scope, requested=requested,
                                effective=item['effective'], origin=item['origin']))
    expected_keys = ESM_KEYS if model == 'esmfold2' else OPENMM_KEYS
    if {s.key for s in settings if not s.scope.startswith('component:')} != expected_keys:
        raise ValueError('missing required effective settings')
    sources = [Source(scope=s['scope'], sha256=s['sha256'], size_bytes=s.get('size_bytes')) for s in payload['sources']]
    projection = ExecutionReceipt(model=model, artifact_sha256=evidence['sha256'], settings=settings, sources=sources).model_dump()
    return {'artifact': evidence, 'receipt': projection}


async def persist_openmm_receipts(job, root, session):
    """Retain job-scoped execution evidence, not candidate numerical authority.

    Every direct Design receives the owning Job's receipt set for provenance;
    these records deliberately make no per-candidate OpenMM association claim.
    """
    from services.core_protein_scientific_contract import revision_for_job
    from sqlalchemy import select
    from database import Design
    if revision_for_job(job) != 1 or REQUEST_KEY not in (job.provenance or {}):
        return
    root = Path(root)
    paths = sorted((root / 'run/openmm/relaxation').rglob('*effective_settings.json'))
    if not paths:
        if (job.params or {}).get('openmm_enabled') is True:
            raise ValueError('missing OpenMM execution receipts')
        return
    records = [prepare_receipt(job, root, path) for path in paths]
    if any(r['receipt']['model'] != 'openmm' for r in records):
        raise ValueError('foreign OpenMM execution receipt')
    prior = (job.provenance or {}).get(KEY, [])
    prior_openmm = [r for r in prior if r['receipt']['model'] == 'openmm']
    if prior_openmm and canonical(prior_openmm) != canonical(records):
        raise ValueError('OpenMM receipt replay changed')
    records = [r for r in prior if r['receipt']['model'] != 'openmm'] + records
    job.provenance = {**(job.provenance or {}), KEY: records}
    rows = list((await session.execute(select(Design).where(Design.job_id == job.id))).scalars())
    for row in rows:
        row.confidence_metrics = {**(row.confidence_metrics or {}), KEY: records}
    await session.flush()


def verify_receipts(job):
    stored = (job.provenance or {}).get(KEY)
    if not isinstance(stored, list) or not stored:
        return ExecutionSettings(status='unavailable', reason='missing_execution_receipt', receipts=[])
    try:
        from paths import get_data_root, resolve_runtime_data_path
        root = Path(job.output_dir)
        root = resolve_runtime_data_path(root) if root.is_absolute() else get_data_root()/root
        records = [prepare_receipt(job, root, Path(record['artifact']['path'])) for record in stored]
        if canonical(records) != canonical(stored):
            raise ValueError('persisted execution receipt changed')
        return ExecutionSettings(status='ok', reason=None, receipts=[r['receipt'] for r in records])
    except (ValueError, TypeError, KeyError, IndexError, OSError, RuntimeError):
        return ExecutionSettings(status='unavailable', reason='invalid_execution_receipt', receipts=[])
