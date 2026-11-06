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
    p = api.add_parser('import-project'); p.add_argument('project_id'); p.add_argument('directory'); p.add_argument('--replay-key', required=True); p.add_argument('--domain-experiment-id'); p.add_argument('--stream', action='store_true', help='upload raw bundle files in chunks')
    p = api.add_parser('settings-schema')
    p = api.add_parser('receipts'); p.add_argument('project_id'); p.add_argument('--after')
    for action in ('result', 'document', 'artifact', 'export-zip'):
        p = api.add_parser(action); p.add_argument('project_id'); p.add_argument('receipt_id')
        if action in ('document', 'artifact'): p.add_argument('artifact_id')
        if action in ('artifact', 'export-zip'): p.add_argument('destination')
        if action == 'export-zip': p.add_argument('--shared-state', action='append', default=[])
    for action in ('resources', 'freeze', 'resolve'):
        p = api.add_parser(action); p.add_argument('project_id'); p.add_argument('dataset_id'); p.add_argument('revision_id')
        if action == 'resources': p.add_argument('--offset', type=int, default=0); p.add_argument('--limit', type=int, default=50)
        else: p.add_argument('file', help='Closed request JSON')
    for action in ('compare', 'ensemble-support', 'create-shared-state', 'save-shared-state'):
        p = api.add_parser(action); p.add_argument('project_id')
        if action == 'save-shared-state': p.add_argument('dataset_id')
        p.add_argument('file', help='Closed request JSON')
    p = api.add_parser('shared-state'); p.add_argument('project_id'); p.add_argument('dataset_id'); p.add_argument('--revision-id')
    p = api.add_parser('import-alignment'); p.add_argument('project_id'); p.add_argument('file'); p.add_argument('--format', required=True, choices=['fasta', 'stockholm', 'clustal', 'a3m']); p.add_argument('--molecule', required=True, choices=['protein', 'dna', 'rna']); p.add_argument('--replay-key', required=True)
    p = api.add_parser('import-zip'); p.add_argument('project_id'); p.add_argument('file'); p.add_argument('--replay-key', required=True); p.add_argument('--domain-experiment-id')
    p = api.add_parser('submit-operation'); p.add_argument('project_id'); p.add_argument('file', help='ManagedOperation JSON')
    for action in ('operation', 'operation-artifact'):
        p = api.add_parser(action); p.add_argument('project_id'); p.add_argument('job_id')
        if action == 'operation-artifact': p.add_argument('destination')
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
        from .http import (AlignmentClient, JobSubmission, AlignmentImport, BrowserExport,
                           ComparisonInput, EnsembleSupportInput, FreezeRequest, ResolveRequest,
                           SharedCreate, SharedSave, ManagedOperation)
        from pydantic import TypeAdapter
        client = AlignmentClient(a.url)
        if a.api_action == 'model': result = client.model(a.model_id)
        elif a.api_action == 'job': result = client.job(a.job_id)
        elif a.api_action == 'export-project': result = client.export_project_bundle(a.project_id, a.receipt_id, a.directory, shared_state_ids=a.shared_state)
        elif a.api_action == 'import-project':
            method = client.import_project_bundle_streaming if a.stream else client.import_project_bundle
            result = method(a.project_id, a.directory, replay_key=a.replay_key, domain_experiment_id=a.domain_experiment_id)
        elif a.api_action == 'submit-operation': result = client.submit_operation(a.project_id, TypeAdapter(ManagedOperation).validate_python(read(a.file)))
        elif a.api_action == 'operation': result = client.operation(a.project_id, a.job_id)
        elif a.api_action == 'operation-artifact':
            data = client.operation_artifact(a.project_id, a.job_id); Path(a.destination).write_bytes(data)
            result = {'destination': a.destination, 'bytes': len(data)}
        elif a.api_action == 'settings-schema': result = client.settings_schema()
        elif a.api_action == 'receipts': result = client.receipts(a.project_id, after=a.after)
        elif a.api_action == 'resources': result = client.resources(a.project_id, a.dataset_id, a.revision_id, offset=a.offset, limit=a.limit)
        elif a.api_action in ('freeze', 'resolve'):
            contract = FreezeRequest if a.api_action == 'freeze' else ResolveRequest
            result = getattr(client, a.api_action)(a.project_id, a.dataset_id, a.revision_id, contract.model_validate(read(a.file)))
        elif a.api_action == 'result': result = client.result(a.project_id, a.receipt_id)
        elif a.api_action == 'document': result = client.document(a.project_id, a.receipt_id, a.artifact_id)
        elif a.api_action in ('artifact', 'export-zip'):
            data = client.artifact(a.project_id, a.receipt_id, a.artifact_id) if a.api_action == 'artifact' else client.export_project_zip(a.project_id, a.receipt_id, BrowserExport(shared_state_ids=a.shared_state))
            Path(a.destination).write_bytes(data)
            result = {'destination': a.destination, 'bytes': len(data)}
        elif a.api_action == 'import-alignment': result = client.import_alignment(a.project_id, Path(a.file).read_bytes(), AlignmentImport(format=a.format, molecule=a.molecule, replay_key=a.replay_key))
        elif a.api_action == 'import-zip': result = client.import_project_zip(a.project_id, Path(a.file).read_bytes(), replay_key=a.replay_key, domain_experiment_id=a.domain_experiment_id)
        elif a.api_action == 'shared-state': result = client.shared_state(a.project_id, a.dataset_id, revision_id=a.revision_id)
        elif a.api_action == 'save-shared-state': result = client.save_shared_state(a.project_id, a.dataset_id, SharedSave.model_validate(read(a.file)))
        elif a.api_action in ('compare', 'ensemble-support', 'create-shared-state'):
            contract = {'compare': ComparisonInput, 'ensemble-support': EnsembleSupportInput, 'create-shared-state': SharedCreate}[a.api_action]
            result = getattr(client, a.api_action.replace('-', '_'))(a.project_id, contract.model_validate(read(a.file)))
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
