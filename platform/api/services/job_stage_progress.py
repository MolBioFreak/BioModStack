"""Read-only stage presentation from retained execution evidence.

Never compile a plan, inspect artifacts, query children, or infer scientific work
from a mode here. Selected static components are a plan, not completion receipts;
dynamic templates describe possible future expansion, not scheduled stages.
"""
from collections.abc import Mapping
from typing import Literal, TypedDict


class ExecutionStage(TypedDict):
    id: str
    label: str
    state: Literal['planned', 'running', 'completed', 'awaiting_input', 'failed', 'cancelled', 'unknown']
    source: Literal['plan', 'recorded', 'model']


# Scheduler/whole-job messages must not become model stages.
_LIFECYCLE = frozenset({
    'complete', 'completed', 'failed', 'cancelled', 'canceled', 'queued',
    'pending', 'running', 'starting', 'initializing', 'preparing', 'waiting',
    'awaiting_input', 'submitted', 'waiting for gpu', 'waiting for resources',
    'md completion blocked', 'md result validation failed', 'result ingestion failed',
    'no candidates', 'frustrampnn result ingestion failed', 'result integrity failed',
    'md analysis failed', 'component failed', 'native validation pending',
})
_TERMINAL = frozenset({'completed', 'failed', 'cancelled', 'canceled'})


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _get(job, key, default=None):
    return job.get(key, default) if isinstance(job, Mapping) else getattr(job, key, default)


def _name(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    return None if value.casefold() in _LIFECYCLE else value


def stage_evidence(job):
    """The same narrow JSON fields are selected in the summary SQL query."""
    provenance = _mapping(_get(job, 'provenance'))
    plan = _mapping(_mapping(provenance.get('execution_plan_approval')).get('plan'))
    metadata = _mapping(plan.get('metadata'))
    components = _get(job, 'stage_plan_components', metadata.get('static_components'))
    assignment = _mapping(provenance.get('remote_execution_assignment'))
    assigned = _get(job, 'stage_assigned_components',
                    _mapping(assignment.get('resources')).get('components'))
    # The approved plan is the richer inventory; historical remote runs may
    # retain only the resource owner's selected component summary.
    if not isinstance(components, list) or not components:
        components = assigned
    terminals = _get(job, 'stage_terminal_states', provenance.get('stage_terminal_states'))
    return components if isinstance(components, list) else [], _mapping(terminals)


def project_execution_stages(job) -> list[ExecutionStage]:
    components, terminals = stage_evidence(job)
    stages: dict[str, ExecutionStage] = {}
    status = _get(job, 'status')
    status = getattr(status, 'value', status)

    def add(name, source, state='unknown'):
        name = _name(name)
        if name is not None:
            if name not in stages:
                stages[name] = {'id': name, 'label': name, 'state': state, 'source': source}
            elif source == 'recorded':
                # An observation supersedes the plan as the state's evidence.
                stages[name]['source'] = 'recorded'
        return name

    for component in components:
        component = _mapping(component)
        add(component.get('component_key'), 'plan',
            'unknown' if status in _TERMINAL else 'planned')

    completed = _get(job, 'completed_stages')
    completed = completed if isinstance(completed, list) else []
    for name in completed:
        if (name := add(name, 'recorded')) is not None:
            stages[name]['state'] = 'completed'

    # Explicit terminal receipts preserve failure/skip/unknown independently of
    # the root Job's outcome. Completion is owned by completed_stages.
    for name, receipt in terminals.items():
        if (name := add(name, 'recorded')) is not None and stages[name]['state'] != 'completed':
            terminal = _mapping(receipt).get('status')
            stages[name]['state'] = terminal if terminal in {'failed', 'cancelled'} else 'unknown'

    current = add(_get(job, 'current_stage'), 'recorded')
    if current is not None and current not in terminals and stages[current]['state'] != 'completed':
        stages[current]['state'] = (
            status if status in {'running', 'failed', 'cancelled'} else 'unknown'
        )

    awaiting = _get(job, 'awaiting_input') or status == 'awaiting_input'
    if awaiting:
        wait_stage = add(_get(job, 'awaiting_stage'), 'recorded') or current
        if wait_stage is not None:
            # Review is not failure, including a review after producing outputs.
            stages[wait_stage]['state'] = 'awaiting_input'

    if not stages:
        model_id = str(_get(job, 'model_id') or 'unknown')
        return [{'id': model_id, 'label': model_id, 'state': 'unknown', 'source': 'model'}]
    return list(stages.values())
