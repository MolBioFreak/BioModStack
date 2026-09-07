"""Stage-only owning-workflow metadata collection; never a result finalizer."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--owner', required=True, choices=['protein_design', 'protein_local_redesign'])
    parser.add_argument('--stage-id', required=True, choices=['rf3_prediction_filter', 'rfd3_backbone_filter'])
    parser.add_argument('--role', required=True, choices=['upstream', 'selected_publication', 'skipped'])
    parser.add_argument('--expected-tasks', required=True, type=int)
    parser.add_argument('--receipt', action='append', default=[])
    parser.add_argument('--terminal-manifest', action='append', default=[])
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.expected_tasks < 0 or len(args.receipt) != args.expected_tasks:
        raise ValueError('filter invocation inventory mismatch')
    if args.role == 'skipped' and args.expected_tasks:
        raise ValueError('skipped filter cannot have invocations')
    counts = {key: 0 for key in ('input_count', 'passed', 'rejected_threshold', 'unevaluable_missing', 'invalid_evidence')}
    invocations = []
    pass_set = {}
    seen = set()
    model = 'rf3' if args.stage_id == 'rf3_prediction_filter' else 'rfd3'
    for directory in sorted(args.receipt):
        path = Path(directory) / 'invocation.json'
        raw = path.read_bytes()
        invocation = json.loads(raw)
        if invocation['job_id'] != args.job_id or invocation['stage_id'] != args.stage_id:
            raise ValueError('foreign filter invocation')
        outcomes = path.parent / invocation['outcomes']['path']
        outcome_raw = outcomes.read_bytes()
        if hashlib.sha256(outcome_raw).hexdigest() != invocation['outcomes']['sha256']:
            raise ValueError('filter outcome hash mismatch')
        for row in [json.loads(line) for line in outcome_raw.splitlines()]:
            if row['passed'] and args.role == 'selected_publication':
                artifact = row['artifacts']['published_structure']
                name = Path(artifact['path']).name
                if name in pass_set:
                    raise ValueError('ambiguous selected filter output identity')
                pass_set[name] = artifact['sha256']
        task = invocation['task_id']
        if task in seen:
            raise ValueError('duplicate filter invocation')
        seen.add(task)
        for key in counts:
            value = invocation['counts'][key]
            if type(value) is not int or value < 0:
                raise ValueError('invalid filter count')
            counts[key] += value
        invocations.append({'path': f'run/filter_{model}/{path.parent.name}/invocation.json',
                            'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'task_id': task})
    selection = []
    if args.role == 'selected_publication':
        selected = {}
        ids = set()
        for item in args.terminal_manifest:
            terminal = json.loads(Path(item).read_bytes())
            if (terminal['parent_job_id'] != args.job_id or terminal['parent_workflow_id'] != args.owner
                    or terminal['producer_method'] != model):
                raise ValueError('filter terminal owner mismatch')
            name = Path(terminal['producer_output_key']).name
            if name in selected or terminal['candidate_id'] in ids:
                raise ValueError('duplicate filter terminal identity')
            selected[name] = terminal['producer_artifact_sha256']
            ids.add(terminal['candidate_id'])
            selection.append(terminal)
        if selected != pass_set:
            raise ValueError('filter terminal selected pass-set mismatch')
    elif args.terminal_manifest:
        raise ValueError('upstream filter cannot own terminal identities')
    result = {'schema': 'bms.rf-filter.stage.v1', 'job_id': args.job_id, 'owner': args.owner,
              'stage_id': args.stage_id, 'role': args.role,
              'state': 'skipped' if args.role == 'skipped' else 'observed',
              'expected_tasks': args.expected_tasks, 'invocations': invocations, 'counts': counts,
              'selection': selection}
    Path(args.output).write_text(json.dumps(result, allow_nan=False, sort_keys=True))


if __name__ == '__main__':
    main()
