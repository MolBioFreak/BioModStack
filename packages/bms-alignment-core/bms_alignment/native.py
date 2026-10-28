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
    if s.strategy == 'fft_ns_2': args += ['--retree', str(s.retree if s.retree is not None else 2), '--maxiterate', str(s.maxiterate if s.maxiterate is not None else 0)]
    elif s.strategy in ('linsi', 'ginsi', 'einsi'):
        args += [{'linsi': '--localpair', 'ginsi': '--globalpair', 'einsi': '--genafpair'}[s.strategy], '--maxiterate', str(s.maxiterate if s.maxiterate is not None else 1000)]
    elif s.strategy == 'auto': args += ['--auto']
    if s.strategy != 'fft_ns_2' and s.retree is not None: args += ['--retree', str(s.retree)]
    if s.strategy == 'auto' and s.maxiterate is not None: args += ['--maxiterate', str(s.maxiterate)]
    if s.pair_method is not None: args.append('--' + s.pair_method)
    for name in ('op', 'ep', 'weighti', 'lop', 'lep', 'lexp', 'bl', 'jtt', 'tm', 'kimura', 'randomseed'):
        if getattr(s, name) is not None: args += ['--' + name, str(getattr(s, name))]
    for name, flag in (('long_gap_open', '--LOP'), ('long_gap_extend', '--LEXP')):
        if getattr(s, name) is not None: args += [flag, str(getattr(s, name))]
    for name in ('threadit', 'threadtb', 'partsize', 'groupsize', 'opdist', 'exp', 'gep', 'gexp', 'gop', 'kappa', 'lhlimit', 'minimumweight', 'shiftpenalty', 'similaritylevel', 'skipiterate', 'sparsepickup', 'terminalmargin', 'unalignlevel'):
        value = getattr(s, name)
        if value is not None: args += ['--' + name, str(value)]
    for name in ('parttree', 'linkage', 'direction', 'memsave', 'nucleotide_scoring', 'tree_method', 'refinement_policy', 'distance_output'):
        value = getattr(s, name)
        if value is not None: args.append('--' + value)
    if s.mixedlinkage is not None: args += ['--mixedlinkage', str(s.mixedlinkage)]
    for name in ('fft', 'nofft', 'unweight', 'termgappenalty', 'legacygappenalty', 'anysymbol', 'preservecase', 'fmodel', 'alga', 'allowshift', 'bunkatsunashi', 'focus', 'leavegappyregion', 'noscore', 'oneiteration', 'sp', 'nodeout', 'scoreout'):
        if getattr(s, name): args.append('--' + name)
    for name in ('treein', 'aamatrix', 'anchors'):
        if getattr(s, name) is not None: args += ['--' + name, str(Path(input_path).parent / (name + '.txt'))]
    if s.treeout: args.append('--treeout')
    if s.reorder: args.append('--reorder')
    if s.quiet: args.append('--quiet')
    return args + [input_path]


def run_mafft(request: AlignmentRequest, workdir: Path, executable: str = 'mafft', env=None) -> dict:
    """Run without a shell; retain inputs, raw outputs and native exit status."""
    # Revalidate mutable rosters before emitting native identifiers.
    request = AlignmentRequest.model_validate(request.model_dump())
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix='attempt-', dir=workdir))
    input_file = workdir / 'input.fasta'
    native_roster = request.roster.model_copy(deep=True)
    focus_report = None
    if request.settings.focus_rows is not None:
        selected = set(request.settings.focus_rows.splitlines())
        row_map = {}
        for i, row in enumerate(native_roster.rows, 1):
            ident = ('_focus_' if row.row_id in selected else '') + f'bmsrow{i:08d}'
            row_map[ident] = row.row_id
            row.row_id = ident
        (workdir/'row-map.json').write_text(json.dumps(row_map, indent=2) + '\n')
        focus_report = {'requested_row_ids': sorted(selected), 'unmatched_row_ids': sorted(selected - set(row_map.values()))}
    input_file.write_text(''.join(f'>{r.row_id}\n{r.sequence}\n' for r in native_roster.rows), encoding='utf8')
    for name in ('treein', 'aamatrix', 'anchors'):
        value = getattr(request.settings, name)
        if value is not None: (workdir / (name + '.txt')).write_bytes(value.encode('utf8'))
    args = compile_mafft(request, executable, str(input_file.resolve()))
    with (workdir/'alignment.fasta').open('wb') as out, (workdir/'engine.log').open('wb') as err:
        p = subprocess.run(args, stdout=out, stderr=err, cwd=workdir, env=env, check=False)
    receipt = {'attempt_dir': str(workdir), 'returncode': p.returncode, 'requested': request.model_dump(mode='json'),
               'effective_settings': request.settings.model_dump(mode='json'),
               'argv': [Path(args[0]).name, *args[1:-1], input_file.name],
               'artifacts': sorted(p.name for p in workdir.iterdir() if p.is_file())}
    from .observations import observe_alignment
    receipt['observations'] = observe_alignment(workdir/'alignment.fasta', native_roster)
    if focus_report is not None: receipt['focus_mapping'] = focus_report
    if 'output_headers' in receipt['observations']:
        receipt['output_headers'] = receipt['observations']['output_headers']
    portable_receipt = {**receipt, 'attempt_dir': workdir.name}
    (workdir/'receipt.json').write_text(json.dumps(portable_receipt, indent=2) + '\n')
    return receipt
