"""Native fixed-family adapters; subprocess outcomes and observations remain separate."""
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path
from .models import AlignmentRequest, MafftSettings
from .native import run_mafft
from .tree import _TOKEN, map_mafft_tree


def map_tree(tree, mapping, expected):
    tokens = _TOKEN.findall(tree)
    used, unknown = [], []
    leaf = True
    for i, token in enumerate(tokens):
        if token.isspace(): continue
        if token in ('(', ','): leaf = True
        elif token in (')', ':', ';'): leaf = False
        elif leaf:
            name = token[1:-1].replace("''", "'") if token.startswith("'") else token
            if name in mapping:
                target = mapping[name]
                tokens[i] = target if re.fullmatch(r'[A-Za-z0-9.-]+', target) else "'" + target.replace("'", "''") + "'"
                used.append(target)
            else: unknown.append(name)
            leaf = False
    return ''.join(tokens), {'mapped': used, 'unmapped': unknown,
        'missing': [x for x in expected if x not in used],
        'duplicate_mapped': sorted({x for x in used if used.count(x) > 1})}


def compile_engine(request, executable, input_path, output_path, tree_path=None):
    s = request.settings
    prefix = [executable] if isinstance(executable, str) else list(executable)
    if s.engine == 'famsa':
        a = prefix + ['-t', str(s.threads), '-gt', s.guide_tree]
        if s.guide_tree == 'import': a.append(tree_path or str(Path(input_path).parent / 'guide.submitted.nwk'))
        a += ['-refine_mode', s.refine_mode]
        for name in ('keep_duplicates', 'medoidtree', 'dgr', 'dgo', 'dsp'):
            if getattr(s, name): a.append('-' + name)
        if s.verbose: a.append('-v')
        for name in ('medoid_seeds', 'medoid_threshold', 'trim_columns', 'r', 'sm', 'go', 'ge', 'tgo', 'tge', 'dist', 'gsd', 'gsl'):
            if getattr(s, name) is not None: a += ['-' + name, str(getattr(s, name))]
        return a + [input_path, output_path]
    if s.engine == 'muscle':
        a = prefix + ['-' + s.mode, input_path, '-output', output_path, '-threads', str(s.threads),
             '-amino' if request.roster.molecule == 'protein' else '-nt']
        if s.permutation is not None: a += ['-perm', s.permutation]
        if s.perturb is not None: a += ['-perturb', str(s.perturb)]
        for name in ('consiters', 'refineiters', 'minsuper', 'm_is', 'm_il', 'is_is', 'il_il', 's_is', 's_il', 'anchor_letter', 'randseed'):
            if getattr(s, name) is not None: a += ['-' + name, str(getattr(s, name))]
        if s.output_order is not None: a.append('-' + s.output_order)
        for name in ('quiet', 'verbose', 'accs', 'randomchaintree'):
            if getattr(s, name): a.append('-' + name)
        if s.hmmin is not None: a += ['-hmmin', str(Path(input_path).parent / 'hmmin.txt')]
        for name, flag, filename in (('guidetreeout', '-guidetreeout', 'guide.native.nwk'), ('hmmout', '-hmmout', 'hmm.native.txt'), ('native_log', '-log', 'muscle.native.log')):
            if getattr(s, name): a += [flag, str(Path(input_path).parent / filename)]
        if s.ensemble != 'none': a.append('-' + s.ensemble)
        if s.replicates is not None: a += ['-replicates', str(s.replicates)]
        return a
    if s.engine == 'twilight':
        a = prefix + ['-i', input_path, '-o', output_path, '-t', tree_path, '--type', 'n', '--cpu', str(s.threads), '--accurate']
        if s.method == 'tcs': a.append('tcs')
        if s.matrix is not None: a += ['--matrix', str(Path(input_path).parent / 'matrix.txt')]
        for name in ('acc_mode', 'acc_max_group', 'acc_sample_rate', 'acc_sample_top', 'consistency_weight', 'max_subtree', 'remove_gappy', 'match', 'mismatch', 'transition', 'gap_open', 'gap_extend', 'gap_ends', 'scale', 'xdrop', 'length_deviation', 'max_ambig', 'max_len', 'min_len'):
            value = getattr(s, name)
            if value is not None: a += ['--' + name.replace('_', '-'), str(value)]
        for name in ('wildcard', 'rooted', 'prune', 'filter', 'write_filtered', 'write_prune', 'check'):
            if getattr(s, name): a.append('--' + name.replace('_', '-'))
        if s.verbose: a.append('--verbose')
        return a
    raise ValueError('Unsupported engine')


def run_engine(request: AlignmentRequest, workdir: Path, executable=None, mafft='mafft', env=None):
    s = request.settings
    if s.engine == 'mafft': return run_mafft(request, workdir, executable or mafft, env)
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix='attempt-', dir=workdir))
    # Synthetic, whitespace-free native IDs make duplicate/unsafe display IDs reversible.
    ids = [f'bmsrow{i:08d}' for i in range(1, len(request.roster.rows) + 1)]
    row_map = dict(zip(ids, [r.row_id for r in request.roster.rows]))
    (workdir/'row-map.json').write_text(json.dumps(row_map, indent=2))
    inp = workdir/'input.fasta'
    inp.write_text(''.join(f'>{n}\n{r.sequence}\n' for n, r in zip(ids, request.roster.rows)))
    if s.engine == 'muscle' and s.hmmin is not None:
        (workdir/'hmmin.txt').write_bytes(s.hmmin.encode('utf8'))
    tree_path = None
    prep = None
    report = None
    if s.engine == 'famsa' and s.guide_tree_newick is not None:
        raw = s.guide_tree_newick
        (workdir/'guide.original.nwk').write_bytes(raw.encode('utf8'))
        reverse = {v: k for k, v in row_map.items()}
        mapping = {ident: ident for ident in ids}
        mapping.update({leaf: reverse.get(row_id, row_id) for leaf, row_id in s.leaf_mapping.items()})
        mapped, report = map_tree(raw, mapping, ids)
        tree_path = workdir/'guide.submitted.nwk'
        tree_path.write_bytes(mapped.encode('utf8'))
    if s.engine == 'twilight':
        if s.matrix is not None: (workdir/'matrix.txt').write_bytes(s.matrix.encode('utf8'))
        tree_path = workdir/'guide.submitted.nwk'
        if s.guide_source == 'mafft':
            roster = request.roster.model_copy(deep=True)
            for row, ident in zip(roster.rows, ids): row.row_id = ident
            prep_request = AlignmentRequest(roster=roster, settings=MafftSettings(engine='mafft', threads=s.threads, treeout=True))
            start = time.monotonic()
            prep = run_mafft(prep_request, workdir/'guide-preparation', mafft, env)
            prep['wall_seconds'] = time.monotonic() - start
            raw_path = Path(prep['attempt_dir'])/'input.fasta.tree'
            if not raw_path.exists():
                receipt = {'attempt_dir': str(workdir), 'returncode': prep['returncode'] or 1, 'preparation': prep, 'error': 'MAFFT guide tree unavailable'}
                (workdir/'receipt.json').write_text(json.dumps(receipt, indent=2)); return receipt
            raw = raw_path.read_text()
            mapped, report = map_mafft_tree(raw, ids)
            # TWILIGHT's Newick reader requires compact, unquoted synthetic IDs.
            mapped, _ = map_tree(mapped, {ident: ident for ident in ids}, ids)
            mapped = ''.join(token for token in _TOKEN.findall(mapped) if not token.isspace()) + '\n'
        else:
            raw = s.guide_tree or ''
            reverse = {v: k for k, v in row_map.items()}
            mapping = {leaf: reverse.get(row_id, row_id) for leaf, row_id in s.leaf_mapping.items()}
            mapped, report = map_tree(raw, mapping, ids)
        (workdir/'guide.original.nwk').write_text(raw)
        tree_path.write_text(mapped)
    ensemble = s.engine == 'muscle' and (s.ensemble != 'none' or s.permutation == 'all')
    output = workdir/('ensemble.@.fasta' if ensemble else 'alignment.fasta')
    args = compile_engine(request, executable or s.engine, str(inp), str(output), str(tree_path) if tree_path else None)
    start = time.monotonic()
    with (workdir/'engine.log').open('wb') as log:
        p = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, cwd=workdir, env=env, check=False)
    files = sorted(workdir.glob('ensemble.*.fasta')) if ensemble else [output] if output.exists() else []
    from .observations import observe_alignment
    native_roster = request.roster.model_copy(deep=True)
    for row, ident in zip(native_roster.rows, ids):
        row.row_id = ident
    observations = []
    for file in files:
        details = observe_alignment(file, native_roster)
        if 'output_headers' in details:
            details['native_order'] = details['output_headers']
        if 'observation_error' in details:
            details['parse_error'] = details['observation_error']
        observations.append({'artifact': file.name, **details})
    receipt = {'attempt_dir': str(workdir), 'returncode': p.returncode, 'requested': request.model_dump(mode='json'),
        'effective_settings': s.model_dump(mode='json'), 'argv': args, 'wall_seconds': time.monotonic()-start,
        'artifacts': [p.name for p in workdir.iterdir() if p.is_file() and p.name != 'receipt.json'],
        'members': [p.name for p in files], 'observations': observations}
    if s.engine == 'twilight': receipt.update(tree_mapping=report, preparation=prep)
    if s.engine == 'famsa': receipt['tree_mapping'] = report
    (workdir/'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt
