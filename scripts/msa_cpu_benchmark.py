#!/usr/bin/env python3
"""Bounded, offline synthetic MSA smoke benchmark; not an accuracy benchmark."""
import argparse
import hashlib
import json
import os
import platform
import signal
import subprocess
import time
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def fasta(path):
    rows = []
    for line in path.read_text().splitlines():
        if line.startswith('>'):
            rows.append([line[1:], ''])
        elif line.strip():
            if not rows:
                raise ValueError('sequence before header')
            rows[-1][1] += line.strip()
    return rows


def observe(source, output):
    """Observation only: never alters the process status or its output."""
    try:
        src, out = fasta(source), fasta(output)
        ids = [x[0] for x in out]
        source_map = dict(src)
        mismatches = [name for name, seq in out if name not in source_map or seq.replace('-', '') != source_map[name].replace('-', '')]
        lengths = sorted(set(len(seq) for _, seq in out))
        return {'input_rows': len(src), 'output_rows': len(out), 'missing_ids': sorted(set(source_map) - set(ids)), 'extra_ids': sorted(set(ids) - set(source_map)), 'duplicate_output_ids': len(ids) - len(set(ids)), 'degap_mismatches': mismatches, 'output_lengths': lengths}
    except (OSError, ValueError) as e:
        return {'unavailable': str(e)}


def invoke(argv, cwd, timeout, out, err, metrics):
    # A killed timer may leave no metrics; never reuse a previous run's file.
    metrics.unlink(missing_ok=True)
    wrapped = ['/usr/bin/time', '-f', '%U %S %M', '-o', str(metrics), '--'] + argv
    start = time.monotonic()
    with out.open('wb') as stdout, err.open('wb') as stderr:
        try:
            p = subprocess.Popen(wrapped, cwd=cwd, stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                rc = p.wait(timeout=timeout)
                status = 'completed' if rc == 0 else 'failed'
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
                rc, status = None, 'timeout'
        except OSError as e:
            stderr.write(str(e).encode())
            rc, status = None, 'launch_error'
    result = {'status': status, 'returncode': rc, 'wall_seconds': time.monotonic() - start}
    try:
        fields = metrics.read_text().strip().splitlines()[-1].split()
        result.update(user_seconds=float(fields[0]), system_seconds=float(fields[1]), peak_rss_kib=int(fields[2]))
    except (OSError, ValueError, IndexError):
        result.update(user_seconds=None, system_seconds=None, peak_rss_kib=None)
    return result


def fixtures():
    # Artificial strings only; the long/fragmented case is a stress smoke test.
    protein = [('p1', 'ACDEFGHIKLMNPQRSTVWY'), ('p2', 'ACDEFGHIKLMNPQRSVWY'), ('p3', 'ACDEYGHIKLMNPQRSTVWY'), ('p4', 'ACDEFGHIKLMNPQRSTVWY')]
    dna = [('n1', 'ACGTACGTTGCAACGT'), ('n2', 'ACGTACGTGCAACGT'), ('n3', 'ACGTTCGTTGCAACGT'), ('n4', 'ACGTACGTTGCAACGT')]
    long = [('l1', 'ACDEFGHIKLMNPQRSTVWY' * 20), ('l2', 'ACDEFGHIKLMNPQRSTVWY' * 18), ('l3', 'ACDEFGHIKLMNPQRSTVWY' * 4), ('l4', 'ACDEYGHIKLMNPQRSTVWY' * 15)]
    return {'protein_tiny': protein, 'dna_tiny': dna, 'protein_long_fragments': long}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--image', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path, help='new local output directory')
    ap.add_argument('--threads', type=int, default=1)
    ap.add_argument('--repeats', type=int, default=2)
    ap.add_argument('--timeout', type=float, default=60)
    a = ap.parse_args()
    if a.threads < 1 or a.repeats < 1 or a.timeout <= 0:
        ap.error('threads, repeats and timeout must be positive')
    image = a.image.resolve(strict=True)
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    envargs = ['env', 'OMP_NUM_THREADS=' + str(a.threads), 'OPENBLAS_NUM_THREADS=' + str(a.threads), 'MKL_NUM_THREADS=' + str(a.threads)]
    prefix = ['apptainer', 'exec', '--cleanenv', str(image)] + envargs
    provenance = {}
    for engine in ('mafft', 'famsa', 'muscle', 'twilight'):
        # Commands are fixed and do not include input content.
        cp = subprocess.run(prefix + ['sh', '-c', 'command -v "$1"; sha256sum "$(command -v "$1")"; "$1" "$2" 2>&1 | head -5', 'sh', engine, {'mafft': '--version', 'famsa': '-h', 'muscle': '-version', 'twilight': '--help'}[engine]], capture_output=True, text=True, timeout=30)
        provenance[engine] = {'exit_code': cp.returncode, 'text': cp.stdout, 'stderr': cp.stderr}
    result = {'schema': 1, 'description': 'synthetic performance/integrity smoke only; no biological accuracy inference', 'image_sha256': sha(image), 'host': {'platform': platform.platform(), 'processor': platform.processor(), 'logical_cpus': os.cpu_count()}, 'threads_requested': a.threads, 'thread_environment': envargs[1:], 'timeout_seconds': a.timeout, 'repeats': a.repeats, 'cache_note': 'first invocation is process-cold; OS/image caches not flushed; later invocations warm-ish, not controlled cold starts', 'provenance': provenance, 'fixtures': {}, 'runs': [], 'incomplete': ['TWILIGHT accurate/TCS and guide preparation not exercised; no compatible verified tree supplied', 'MUSCLE Super5 and ensembles, MAFFT alternate strategies, RNA and other engine settings not exercised', 'independent reference scoring unavailable; no SP/TC accuracy claims', 'real family, viewer, API and integrated acceptance not exercised']}
    for name, rows in fixtures().items():
        source = out / (name + '.fasta')
        source.write_text(''.join('>' + n + '\n' + s + '\n' for n, s in rows))
        result['fixtures'][name] = {'sha256': sha(source), 'rows': len(rows), 'lengths': [len(s) for _, s in rows], 'kind': 'synthetic'}
        engines = ['mafft', 'muscle'] + ([] if name == 'dna_tiny' else ['famsa'])
        for engine in engines:
            for rep in range(a.repeats):
                stem = f'{name}-{engine}-{rep + 1}'
                alignment = out / (stem + '.fasta')
                stdout = out / (stem + '.stdout')
                stderr = out / (stem + '.stderr')
                metrics = out / (stem + '.time')
                if engine == 'mafft':
                    command = ['mafft', '--thread', str(a.threads), '--nuc' if name == 'dna_tiny' else '--amino', str(source)]
                    stdout = alignment
                elif engine == 'famsa':
                    command = ['famsa', '-t', str(a.threads), str(source), str(alignment)]
                else:
                    command = ['muscle', '-align', str(source), '-output', str(alignment), '-threads', str(a.threads)]
                argv = prefix + command
                measurement = invoke(argv, out, a.timeout, stdout, stderr, metrics)
                result['runs'].append({'fixture': name, 'engine': engine, 'mode': 'default align', 'repeat': rep + 1, 'cache_label': 'first_process' if rep == 0 else 'subsequent_process', 'argv': argv, **measurement, 'output_sha256': sha(alignment) if alignment.exists() else None, 'integrity_observations': observe(source, alignment)})
                (out / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'report': str(out / 'report.json'), 'runs': len(result['runs']), 'statuses': [r['status'] for r in result['runs']]}, indent=2))


if __name__ == '__main__':
    main()
