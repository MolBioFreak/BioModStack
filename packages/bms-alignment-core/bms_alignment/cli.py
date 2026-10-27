import argparse
import json
from pathlib import Path
from .models import AlignmentRequest, SequenceSet
from .formats import compare, export, summary
from .native import run_mafft
from .bundle import verify_bundle


def main():
    parser = argparse.ArgumentParser(prog='bms-alignment')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('schema')
    for command in ('validate', 'inspect'):
        p = sub.add_parser(command); p.add_argument('file')
    p = sub.add_parser('run'); p.add_argument('file'); p.add_argument('workdir'); p.add_argument('--mafft', default='mafft')
    p = sub.add_parser('compare'); p.add_argument('left'); p.add_argument('right')
    p = sub.add_parser('export'); p.add_argument('file'); p.add_argument('format', choices=['fasta', 'clustal', 'stockholm', 'a3m']); p.add_argument('--original', action='store_true')
    p = sub.add_parser('bundle'); p.add_argument('action', choices=['verify']); p.add_argument('directory')
    a = parser.parse_args()
    read = lambda f: json.loads(Path(f).read_text())
    if a.command == 'schema': result = AlignmentRequest.model_json_schema()
    elif a.command == 'validate': result = AlignmentRequest.model_validate(read(a.file)).model_dump(mode='json')
    elif a.command == 'run':
        result = run_mafft(AlignmentRequest.model_validate(read(a.file)), Path(a.workdir), a.mafft)
        print(json.dumps(result, indent=2)); return result['returncode']
    elif a.command == 'inspect': result = summary(SequenceSet.model_validate(read(a.file)))
    elif a.command == 'compare': result = compare(SequenceSet.model_validate(read(a.left)), SequenceSet.model_validate(read(a.right)))
    elif a.command == 'export':
        text, warnings = export(SequenceSet.model_validate(read(a.file)), a.format, a.original)
        import sys
        for warning in warnings: print('WARNING: '+warning, file=sys.stderr)
        print(text, end=''); return 0
    else:
        result = verify_bundle(Path(a.directory))
        print(json.dumps(result, indent=2)); return 0 if result['valid'] else 1
    print(json.dumps(result, indent=2)); return 0

if __name__ == '__main__':
    raise SystemExit(main())
