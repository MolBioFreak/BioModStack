import json
import subprocess
import tempfile
from pathlib import Path
from .models import AlignmentRequest
from .formats import parse


def compile_mafft(request: AlignmentRequest, executable: str, input_path: str) -> list[str]:
    s = request.settings
    args = [executable, '--amino' if request.roster.molecule == 'protein' else '--nuc', '--thread', str(s.threads)]
    if s.strategy == 'fft_ns_2': args += ['--retree', '2', '--maxiterate', '0']
    elif s.strategy == 'linsi': args += ['--localpair', '--maxiterate', '1000']
    elif s.strategy == 'ginsi': args += ['--globalpair', '--maxiterate', '1000']
    elif s.strategy == 'auto': args += ['--auto']
    if s.treeout: args.append('--treeout')
    return args + [input_path]


def run_mafft(request: AlignmentRequest, workdir: Path, executable: str = 'mafft', env=None) -> dict:
    """Run without a shell; retain inputs, raw outputs and native exit status."""
    # Revalidate mutable rosters before emitting native identifiers.
    request = AlignmentRequest.model_validate(request.model_dump())
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix='attempt-', dir=workdir))
    input_file = workdir / 'input.fasta'
    input_file.write_text(''.join(f'>{r.row_id}\n{r.sequence}\n' for r in request.roster.rows), encoding='utf8')
    args = compile_mafft(request, executable, str(input_file.resolve()))
    with (workdir/'alignment.fasta').open('wb') as out, (workdir/'engine.log').open('wb') as err:
        p = subprocess.run(args, stdout=out, stderr=err, cwd=workdir, env=env, check=False)
    receipt = {'attempt_dir': str(workdir), 'returncode': p.returncode, 'requested': request.model_dump(mode='json'),
               'effective_settings': request.settings.model_dump(mode='json'),
               'argv': [Path(args[0]).name, *args[1:-1], input_file.name],
               'artifacts': ['input.fasta', 'alignment.fasta', 'engine.log']}
    tree = Path(str(input_file) + '.tree')
    if tree.exists(): receipt['artifacts'].append(tree.name)
    if p.returncode == 0:
        output = parse((workdir/'alignment.fasta').read_text(), 'fasta', request.roster.molecule)
        input_by_id = {r.row_id: r for r in request.roster.rows}
        receipt['output_headers'] = [r.header for r in output.rows]
        receipt['observations'] = {'missing_ids': sorted(input_by_id.keys() - set(receipt['output_headers'])),
                                   'unexpected_ids': sorted(set(receipt['output_headers']) - input_by_id.keys())}
    portable_receipt = {**receipt, 'attempt_dir': workdir.name}
    (workdir/'receipt.json').write_text(json.dumps(portable_receipt, indent=2) + '\n')
    return receipt
