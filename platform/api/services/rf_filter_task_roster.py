"""Orchestrator-owned RF scheduling roster in existing Job provenance.

Only the Nextflow owner's lifecycle consumer writes this state. Filter products
never supply task membership, roles, thresholds or the completeness seal.
"""
import copy
import json
import re

from services.rf_filter_criteria import THRESHOLDS, enabled_criteria

KEY = 'rf_filter_task_roster'
PROCESSES = {'FilterRF3': 'rf3_prediction_filter', 'FilterRFD3': 'rfd3_backbone_filter'}
BRANCH_DEFAULTS = dict(diffusion_method='rfd3', pred_method='af2', run_rfd_only=False,
    skip_rfd=False, skip_rfd_seq=False, skip_rfd_seq_pred=False, skip_pred=False,
    rfd3_generation_request_path=None, rfd3_request_path=None,
    plr_validation_input_pdbs=None, plr_sequence_input_pdbs=None, plr_backbone_input_pdbs=None)
EVENT = re.compile(r'^\[([0-9a-f]{2}/[0-9a-f]+)\]\s+(Submitted|Cached) process >\s+([\w:]+)\s+\(([1-9][0-9]*)\)\s*$', re.I)


def launch_settings(params):
    settings = {key: params.get(key, default) for key, default in BRANCH_DEFAULTS.items()}
    for key, default in BRANCH_DEFAULTS.items():
        if isinstance(default, bool) and type(settings[key]) is not bool:
            raise ValueError('filter launch branch requires boolean')
    for mapping in THRESHOLDS.values():
        for keys in mapping.values():
            for key in keys:
                if key: settings[key] = params.get(key)
    for stage in THRESHOLDS: enabled_criteria(stage, settings)
    return settings


def roles(owner, settings):
    if owner == 'protein_local_redesign':
        skipped = any(settings[key] for key in ('plr_validation_input_pdbs', 'plr_sequence_input_pdbs', 'plr_backbone_input_pdbs', 'rfd3_request_path'))
        return {'rfd3_backbone_filter': 'skipped' if skipped else 'upstream'}
    rf3 = (settings['pred_method'] == 'rf3' and settings['diffusion_method'] != 'boltzgen'
           and not any(settings[key] for key in ('skip_rfd_seq_pred', 'run_rfd_only', 'skip_pred')))
    rfd3 = (settings['diffusion_method'] == 'rfd3'
            and not any(settings[key] for key in ('skip_rfd', 'skip_rfd_seq', 'skip_rfd_seq_pred')))
    return {'rf3_prediction_filter': 'selected_publication' if rf3 else 'skipped',
            'rfd3_backbone_filter': ('selected_publication' if settings['run_rfd_only'] and not settings['rfd3_generation_request_path'] else 'upstream') if rfd3 else 'skipped'}


def begin(job, params):
    from services.rf_filter_stage_accounting import _owner
    from services.core_protein_scientific_contract import revision_for_job
    owner = _owner(job) if revision_for_job(job) == 1 else None
    if owner is None: return False
    settings = launch_settings(params)
    prior = (job.provenance or {}).get(KEY)
    if prior is not None:
        if prior['owner'] != owner or prior['settings'] != settings:
            raise ValueError('filter launch authority changed on resume')
        roster = copy.deepcopy(prior)
        roster['complete'] = False
        roster['attempt'] += 1
        roster['observed_this_attempt'] = {stage: [] for stage in roster['stages']}
    else:
        roster = dict(schema=1, job_id=str(job.id), owner=owner, settings=settings,
            attempt=1, complete=False, stages={stage: {'role': role, 'tasks': {}} for stage, role in roles(owner, settings).items()},
            observed_this_attempt={stage: [] for stage in roles(owner, settings)})
    job.provenance = {**(job.provenance or {}), KEY: roster}
    return True


def observe(job, line):
    roster = (job.provenance or {}).get(KEY)
    if roster is None: return False
    match = EVENT.fullmatch(line.strip())
    if match is None:
        if re.search(r'(Submitted|Cached) process >.*\b(FilterRF3|FilterRFD3)\b', line):
            raise ValueError('filter scheduling event cannot be decoded')
        return False
    work_hash, event, process, task_id = match.groups()
    stage_id = PROCESSES.get(process.split(':')[-1])
    if stage_id is None: return False
    roster = copy.deepcopy(roster)
    stage = roster['stages'].get(stage_id)
    if stage is None or stage['role'] == 'skipped' or roster['complete']:
        raise ValueError('filter task scheduled outside launch authority')
    # Retain every observed task across resume, including cache hits. Do not
    # infer membership from success-only output channels or reset failed tasks.
    previous = stage['tasks'].get(task_id)
    if previous is not None and previous['process'] != process:
        raise ValueError('filter task process identity changed on resume')
    executions = previous.get('executions', []) if previous else []
    if previous and not executions:
        executions.append({'work_hash': previous['work_hash']})
    execution = {'attempt': roster['attempt'], 'work_hash': work_hash, 'event': event.lower()}
    if execution not in executions:
        executions.append(execution)
    stage['tasks'][task_id] = {'work_hash': work_hash, 'process': process, 'executions': executions}
    observed = roster['observed_this_attempt'][stage_id]
    if task_id not in observed: observed.append(task_id)
    job.provenance = {**(job.provenance or {}), KEY: roster}
    return True


def finish(job, exit_code):
    roster = (job.provenance or {}).get(KEY)
    if roster is None: return False
    roster = copy.deepcopy(roster)
    roster['complete'] = exit_code == 0 and all(set(roster['observed_this_attempt'][stage]) == set(record['tasks']) for stage, record in roster['stages'].items())
    job.provenance = {**(job.provenance or {}), KEY: roster}
    return True


def authority(job, owner):
    roster = (job.provenance or {}).get(KEY)
    if not isinstance(roster, dict) or roster.get('schema') != 1 or roster.get('job_id') != str(job.id) or roster.get('owner') != owner or roster.get('complete') is not True:
        raise ValueError('filter orchestrator task roster missing or incomplete')
    if {key: value['role'] for key, value in roster['stages'].items()} != roles(owner, roster['settings']):
        raise ValueError('filter roster launch role mismatch')
    return roster


async def begin_command(session, job, command):
    """Snapshot effective compiled launch flags, then commit before spawn."""
    from services.rf_filter_stage_accounting import _owner
    from services.core_protein_scientific_contract import revision_for_job
    if revision_for_job(job) != 1 or _owner(job) is None: return
    from services.boltz_launch_authority import command_params
    raw = command_params(command)
    params = {}
    for key, value in raw.items():
        try: params[key] = json.loads(value)
        except (ValueError, TypeError): params[key] = value
    # These workflow profiles have relevant overrides; CLI flags still win.
    profiles = command[command.index('-profile') + 1].split(',') if '-profile' in command else []
    defaults = {}
    for profile in profiles:
        if profile == 'rfd3_generation': defaults.update(run_rfd_only=True)
        if profile == 'rf3': defaults.update(pred_method='rf3', skip_rfd_seq=True)
    if begin(job, {**defaults, **params}): await session.commit()
