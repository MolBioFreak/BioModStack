import json
import subprocess
import tempfile
from pathlib import Path
from .models import AlignmentRequest
from .formats import parse


def compile_mafft(request: AlignmentRequest, executable: str, input_path: str) -> list[str]:
    s = request.settings
    prefix = [executable] if isinstance(executable, str) else list(executable)
    args = prefix + ['--amino' if request.roster.molecule == 'protein' else '--nuc', '--thread', str(s.threads)]
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
    from .observations import observe_alignment
    receipt['observations'] = observe_alignment(workdir/'alignment.fasta', request.roster)
    if 'output_headers' in receipt['observations']:
        receipt['output_headers'] = receipt['observations']['output_headers']
    portable_receipt = {**receipt, 'attempt_dir': workdir.name}
    (workdir/'receipt.json').write_text(json.dumps(portable_receipt, indent=2) + '\n')
    return receipt
