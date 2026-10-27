import argparse
import json
from pathlib import Path
from .models import AlignmentRequest, SequenceSet
from .formats import compare, export, summary
from .native import run_mafft
from .engines import run_engine
from .bundle import verify_bundle, make_bundle, inspect_project_bundle


def main():
    parser = argparse.ArgumentParser(prog='bms-alignment')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('schema')
    for command in ('validate', 'inspect'):
        p = sub.add_parser(command); p.add_argument('file')
    p = sub.add_parser('run'); p.add_argument('file'); p.add_argument('workdir'); p.add_argument('--executable'); p.add_argument('--mafft', default='mafft')
    p = sub.add_parser('compare'); p.add_argument('left'); p.add_argument('right')
    p = sub.add_parser('export'); p.add_argument('file'); p.add_argument('format', choices=['fasta', 'clustal', 'stockholm', 'a3m']); p.add_argument('--original', action='store_true')
    p = sub.add_parser('bundle'); bs = p.add_subparsers(dest='action', required=True)
    p = bs.add_parser('verify'); p.add_argument('directory')
    p = bs.add_parser('inspect-project'); p.add_argument('directory')
    p = bs.add_parser('create'); p.add_argument('source'); p.add_argument('directory'); p.add_argument('files', nargs='+'); p.add_argument('--exclude', action='append', default=[])
    p = sub.add_parser('api'); p.add_argument('--url', required=True); api = p.add_subparsers(dest='api_action', required=True)
    p = api.add_parser('model'); p.add_argument('model_id', choices=['family_msa_protein', 'family_msa_nucleic'])
    for action in ('preview', 'submit'):
        p = api.add_parser(action); p.add_argument('file', help='JobSubmission JSON')
    p = api.add_parser('job'); p.add_argument('job_id')
    p = api.add_parser('export-project'); p.add_argument('project_id'); p.add_argument('receipt_id'); p.add_argument('directory'); p.add_argument('--shared-state', action='append', default=[])
    p = api.add_parser('import-project'); p.add_argument('project_id'); p.add_argument('directory'); p.add_argument('--replay-key', required=True); p.add_argument('--stream', action='store_true', help='upload raw bundle files in chunks')
    a = parser.parse_args()
    read = lambda f: json.loads(Path(f).read_text())
    if a.command == 'schema': result = AlignmentRequest.model_json_schema()
    elif a.command == 'validate': result = AlignmentRequest.model_validate(read(a.file)).model_dump(mode='json')
    elif a.command == 'run':
        result = run_engine(AlignmentRequest.model_validate(read(a.file)), Path(a.workdir), a.executable, a.mafft)
        print(json.dumps(result, indent=2)); return result['returncode']
    elif a.command == 'inspect': result = summary(SequenceSet.model_validate(read(a.file)))
    elif a.command == 'compare': result = compare(SequenceSet.model_validate(read(a.left)), SequenceSet.model_validate(read(a.right)))
    elif a.command == 'api':
        from .http import AlignmentClient, JobSubmission
        client = AlignmentClient(a.url)
        if a.api_action == 'model': result = client.model(a.model_id)
        elif a.api_action == 'job': result = client.job(a.job_id)
        elif a.api_action == 'export-project': result = client.export_project_bundle(a.project_id, a.receipt_id, a.directory, shared_state_ids=a.shared_state)
        elif a.api_action == 'import-project':
            method = client.import_project_bundle_streaming if a.stream else client.import_project_bundle
            result = method(a.project_id, a.directory, replay_key=a.replay_key)
        else: result = getattr(client, a.api_action)(JobSubmission.model_validate(read(a.file)))
        if not isinstance(result, dict):
            result = result.model_dump(mode='json', by_alias=True)
    elif a.command == 'export':
        text, warnings = export(SequenceSet.model_validate(read(a.file)), a.format, a.original)
        import sys
        for warning in warnings: print('WARNING: '+warning, file=sys.stderr)
        print(text, end=''); return 0
    elif a.action == 'inspect-project':
        result = inspect_project_bundle(Path(a.directory))
        print(json.dumps(result, indent=2)); return 0
    elif a.action == 'create':
        result = make_bundle(Path(a.source), Path(a.directory), a.files, excluded=a.exclude)
        print(json.dumps(result, indent=2)); return 0
    else:
        result = verify_bundle(Path(a.directory))
        print(json.dumps(result, indent=2)); return 0 if result['valid'] else 1
    print(json.dumps(result, indent=2)); return 0

if __name__ == '__main__':
    raise SystemExit(main())
