"""Read RF3/RFD3 owning-stage evidence without replacing terminal result owners."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from services.core_protein_scientific_contract import revision_for_job
from services.rf_filter_criteria import validate_outcome

KEY = 'rf_filter_stages'
STAGES = {'rf3_prediction_filter': 'rf3', 'rfd3_backbone_filter': 'rfd3'}
STATES = ('passed', 'rejected_threshold', 'unevaluable_missing', 'invalid_evidence')


def _read(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('filter artifact path escapes owner')
    path = root / relative
    if any(part.is_symlink() for part in [path, *path.parents] if part != root.parent):
        raise ValueError('filter artifact is a symlink')
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('filter artifact is missing or oversized')
    return path.read_bytes()


def _json(raw):
    def invalid(value):
        raise ValueError('filter JSON contains nonfinite value')
    return json.loads(raw, parse_constant=invalid)


def _bound(root, descriptor):
    if not isinstance(descriptor, dict):
        raise ValueError('filter artifact descriptor missing')
    raw = _read(root, descriptor['path'])
    if hashlib.sha256(raw).hexdigest() != descriptor['sha256'] or len(raw) != descriptor['bytes']:
        raise ValueError('filter artifact hash/size mismatch')
    return raw


def _counts(rows):
    values = {key: sum(row['disposition'] == key for row in rows) for key in STATES}
    values['input_count'] = len(rows)
    return values


def _owner(job):
    # Resolve the actual existing workflow. This grants no positive result support
    # to an inventory entry or to the protein_design legacy fallback.
    # Internal BoltzGen publication has its own stronger filter owner; its old
    # launch names must not be re-resolved through the retired direct launcher.
    if str(job.model_id or '') in {'boltzgen', 'boltzgen_child'}:
        return None
    from services.nextflow import (resolve_nextflow_entrypoint,
        MODEL_MODE_WORKFLOW_ENTRYPOINTS, WORKFLOW_ENTRYPOINTS)
    explicit = MODEL_MODE_WORKFLOW_ENTRYPOINTS.get((str(job.model_id or ''), str(job.mode or '')),
                                                  WORKFLOW_ENTRYPOINTS.get(str(job.model_id or '')))
    if explicit not in {'workflows/protein_design.nf', 'workflows/protein_local_redesign.nf'}:
        return None
    params = job.params if isinstance(job.params, dict) else json.loads(job.params or '{}')
    entry = resolve_nextflow_entrypoint(effective_profile=str(job.model_id or ''),
        model_id=job.model_id, mode=job.mode, params=params)
    return {'workflows/protein_design.nf': 'protein_design',
            'workflows/protein_local_redesign.nf': 'protein_local_redesign'}.get(entry)


def prepare_filter_stages(job, output_root):
    if revision_for_job(job) != 1:
        return None
    owner = _owner(job)
    if owner is None:
        return None
    from services.rf_filter_task_roster import authority
    roster = authority(job, owner)
    root = Path(output_root)
    expected_stages = list(STAGES) if owner == 'protein_design' else ['rfd3_backbone_filter']
    records = {}
    for stage_id in expected_stages:
        relative = f'run/filter_stages/{stage_id}.json'
        raw = _read(root, relative)
        stage = _json(raw)
        expected = roster['stages'][stage_id]
        if stage.get('role') != expected['role']:
            raise ValueError('filter stage role differs from launch authority')
        if (stage.get('schema') != 'bms.rf-filter.stage.v1' or stage.get('job_id') != str(job.id)
                or stage.get('stage_id') != stage_id or stage.get('owner') != owner
                or stage.get('role') not in ('upstream', 'selected_publication', 'skipped')):
            raise ValueError('filter stage owner binding mismatch')
        descriptors = stage.get('invocations')
        if (not isinstance(descriptors, list) or type(stage.get('expected_tasks')) is not int
                or len(descriptors) != stage['expected_tasks']):
            raise ValueError('filter invocation inventory mismatch')
        if {descriptor.get('task_id') for descriptor in descriptors} != set(expected['tasks']):
            raise ValueError('filter invocation differs from orchestrator task roster')
        task_parent = root / f'run/filter_{STAGES[stage_id]}'
        actual = {path.name for path in task_parent.glob(f'{STAGES[stage_id]}_filter_*')}
        if actual != {f'{STAGES[stage_id]}_filter_{task}' for task in expected['tasks']}:
            raise ValueError('filter physical task inventory differs from orchestrator roster')
        if stage['role'] == 'skipped' and descriptors:
            raise ValueError('skipped filter has invocations')
        if stage.get('state') != ('skipped' if stage['role'] == 'skipped' else 'observed'):
            raise ValueError('filter invocation state mismatch')
        rows = []
        seen = set()
        for descriptor in descriptors:
            task_id = descriptor.get('task_id')
            if not isinstance(task_id, str) or not task_id or task_id in seen:
                raise ValueError('duplicate or missing filter task identity')
            seen.add(task_id)
            exact = f'run/filter_{STAGES[stage_id]}/{STAGES[stage_id]}_filter_{task_id}/invocation.json'
            if descriptor.get('path') != exact:
                raise ValueError('filter invocation path binding mismatch')
            invocation = _json(_bound(root, descriptor))
            if set(invocation) != {'schema', 'stage_id', 'job_id', 'task_id', 'counts', 'outcomes'}:
                raise ValueError('filter invocation fields invalid')
            if (invocation.get('schema') != 'bms.rf-filter.invocation.v1'
                    or invocation.get('stage_id') != stage_id or invocation.get('job_id') != str(job.id)
                    or invocation.get('task_id') != task_id):
                raise ValueError('filter invocation identity mismatch')
            task_root = root / Path(exact).parent
            task_rows = [_json(line) for line in _bound(task_root, invocation['outcomes']).splitlines()]
            ids = set()
            for row in task_rows:
                validate_outcome(row, stage_id, roster['settings'])
                cid = row.get('candidate_id')
                if not isinstance(cid, str) or not cid or cid in ids or row.get('disposition') not in STATES:
                    raise ValueError('filter candidate identity/disposition invalid')
                ids.add(cid)
                if row.get('core_protein_scientific_contract') != 1 or type(row.get('passed')) is not bool:
                    raise ValueError('filter outcome revision/boolean invalid')
                if row['passed'] != (row['disposition'] == 'passed'):
                    raise ValueError('filter outcome pass mismatch')
                criteria = row.get('criteria')
                if not isinstance(criteria, list) or any(c.get('candidate_id') != cid for c in criteria):
                    raise ValueError('filter criterion identity mismatch')
                artifacts = row.get('artifacts', {})
                if set(artifacts) != {'structure', 'metadata', 'published_structure'}:
                    raise ValueError('filter source artifact set invalid')
                if artifacts['structure'].get('path') != f'inputs/{cid}':
                    raise ValueError('filter source identity mismatch')
                _bound(task_root, artifacts['structure'])
                metadata = artifacts['metadata']
                if metadata is not None:
                    _bound(task_root, metadata)
                if row.get('source_sha256') != (metadata['sha256'] if metadata else None):
                    raise ValueError('filter metadata source mismatch')
                published = artifacts['published_structure']
                if row['passed'] != (published is not None):
                    raise ValueError('filter publication disposition mismatch')
                if published is not None:
                    _bound(task_root, published)
                for provenance in row.get('descriptor_provenance', {}).values():
                    if provenance['source_sha256'] != artifacts['structure']['sha256']:
                        raise ValueError('filter descriptor source mismatch')
                row['task_id'] = task_id
            source_names = {path.name for path in (task_root / 'inputs').iterdir()
                            if path.name.endswith(('.pdb', '.cif', '.cif.gz'))}
            if source_names != ids:
                raise ValueError('filter input inventory lacks exact dispositions')
            published_names = {Path(row['artifacts']['published_structure']['path']).name
                               for row in task_rows if row['passed']}
            if {path.name for path in (task_root / 'passed').iterdir()} != published_names:
                raise ValueError('filter output inventory differs from pass set')
            if invocation.get('counts') != _counts(task_rows):
                raise ValueError('filter invocation counts mismatch')
            rows.extend(task_rows)
        counts = _counts(rows)
        if stage.get('counts') != counts:
            raise ValueError('filter stage counts mismatch')
        if stage['role'] == 'selected_publication':
            # This is the existing PublishResults directory, not a guessed input
            # or an arbitrary fallback. Upstream fanout has no such equality.
            selected = Counter((Path(row['artifacts']['published_structure']['path']).name,
                                row['artifacts']['published_structure']['sha256']) for row in rows if row['passed'])
            joined = Counter()
            publication_paths = set()
            selection = stage.get('selection')
            if not isinstance(selection, list):
                raise ValueError('filter terminal selection missing')
            for terminal in selection:
                if (terminal.get('parent_job_id') != str(job.id) or terminal.get('parent_workflow_id') != owner
                        or terminal.get('producer_method') != STAGES[stage_id]):
                    raise ValueError('filter terminal selection owner mismatch')
                cid = terminal.get('candidate_id')
                if not isinstance(cid, str) or not cid or Path(cid).name != cid:
                    raise ValueError('filter terminal candidate identity invalid')
                path = f'results/best_designs/candidate_{cid}.pdb'
                if path in publication_paths:
                    raise ValueError('filter terminal selection duplicate')
                publication_paths.add(path)
                digest = hashlib.sha256(_read(root, path)).hexdigest()
                if digest != terminal['producer_artifact_sha256']:
                    raise ValueError('filter terminal artifact mismatch')
                joined[(Path(terminal['producer_output_key']).name, digest)] += 1
            actual_paths = {path.relative_to(root).as_posix() for path in (root / 'results/best_designs').glob('*.pdb')}
            if selected != joined or actual_paths != publication_paths:
                raise ValueError('filter selected publication pass-set mismatch')
        elif stage.get('selection') != []:
            raise ValueError('upstream filter cannot own terminal identities')
        records[stage_id] = {**stage, 'dispositions': rows,
            'artifact': {'path': relative, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}}
    prior = (job.provenance or {}).get(KEY)
    if prior is not None and prior != records:
        raise ValueError('filter stage replay changed')
    return records


def retain_filter_stages(job, records):
    if records is not None:
        job.provenance = {**(job.provenance or {}), KEY: records}
