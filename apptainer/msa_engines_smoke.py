#!/usr/bin/env python3
"""Exercise a built image with synthetic data; not a scientific benchmark."""
import argparse
import pathlib
import re
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('image', type=pathlib.Path)
parser.add_argument('--scratch', type=pathlib.Path, required=True)
args = parser.parse_args()
image = args.image.resolve(strict=True)
args.scratch.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='msa-smoke-', dir=args.scratch) as tmp:
    root = pathlib.Path(tmp)
    protein = {'row_A': 'MKTAYIAKQRQISFVKSHFSRQ', 'row_B': 'MKTAYIAKQRQISFVKSHFTRQ', 'row_C': 'MKTAYIAKQRQISFVKSHFSEQ'}
    dna = {'row_A': 'ACGTACGTACGTACGTACGTACGT', 'row_B': 'ACGTACGTACGTTCGTACGTACGT', 'row_C': 'ACGTACGTACGTACGTACGTTCGT'}
    def fasta(name, rows):
        (root / name).write_text(''.join('>' + k + '\n' + v + '\n' for k, v in rows.items()))
    fasta('protein.fa', protein)
    fasta('dna.fa', dna)
    def run(*argv, out=None):
        cmd = ['apptainer', 'exec', '--cleanenv', '--containall', '--bind', f'{root}:/work', str(image), *argv]
        print('+', ' '.join(cmd), flush=True)
        result = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=120)
        if result.returncode:
            print(result.stdout, result.stderr)
            raise SystemExit(result.returncode)
        if out:
            (root / out).write_text(result.stdout)
        return result
    def check(name, source):
        text = (root / name).read_text()
        rows = {}
        key = None
        for line in text.splitlines():
            if line.startswith('>'):
                key = line[1:].split()[0]
                assert key not in rows
                rows[key] = ''
            elif line.strip():
                assert key is not None
                rows[key] += line.strip()
        assert set(rows) == set(source), (name, rows.keys())
        assert len({len(s) for s in rows.values()}) == 1
        assert all(rows[k].replace('-', '').upper() == source[k] for k in rows), name
        print('PASS', name, 'rows', len(rows), 'columns', len(next(iter(rows.values()))), flush=True)
    run('mafft', '--thread', '1', '--retree', '2', '--maxiterate', '0', '--treeout', '/work/dna.fa', out='mafft.fa')
    check('mafft.fa', dna)
    run('famsa', '-t', '1', '/work/protein.fa', '/work/famsa.fa')
    check('famsa.fa', protein)
    run('muscle', '-align', '/work/protein.fa', '-threads', '1', '-output', '/work/muscle.fa')
    check('muscle.fa', protein)
    tree = (root / 'dna.fa.tree').read_text()
    print('MAFFT tree:', tree.strip(), flush=True)
    # Fixture-only structural leaf token replacement, not a general Newick mapper.
    expected = {f'{i}_{k}': k for i, k in enumerate(dna, 1)}
    tokens = re.findall(r'(?<=[(,])\s*([^():,;\s]+)', tree)
    assert len(tokens) == len(expected) and set(tokens) == set(expected), tokens
    mapped = re.sub(r'(?<=[(,])(\s*)([^():,;\s]+)', lambda m: m[1] + expected[m[2]], tree)
    (root / 'mapped.nwk').write_text(re.sub(r'\s+', '', mapped) + '\n')
    (root / 'supplied.nwk').write_text('((row_A:0.1,row_B:0.1):0.1,row_C:0.1);\n')
    for tree_name, mode in [('mapped.nwk', 'accurate'), ('supplied.nwk', 'tcs')]:
        out = f'twilight-{mode}.fa'
        opt = ['--accurate'] + (['tcs'] if mode == 'tcs' else [])
        run('twilight', '-C', '1', '-t', '/work/' + tree_name, '-i', '/work/dna.fa', '-o', '/work/' + out, *opt)
        check(out, dna)
