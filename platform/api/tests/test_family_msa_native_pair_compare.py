"""Opt-in native cross-engine pair-oracle test; retained Project reads are stubbed."""
import itertools
import json
import os
import random
import subprocess
from pathlib import Path

import pytest


def synthetic_roster():
    rng = random.Random(107)
    alphabet = 'ACDEFGHIKLMNPQRSTVWY'
    base = ''.join(rng.choice(alphabet) for _ in range(65))
    result = []
    for i in range(8):
        s = list(base)
        for _ in range(18):
            s[rng.randrange(len(s))] = rng.choice(alphabet)
        for _ in range(5):
            if rng.randrange(2): s.insert(rng.randrange(len(s)), rng.choice(alphabet))
            else: s.pop(rng.randrange(len(s)))
        result.append(''.join(s))
    return result


def parse_fasta(data):
    rows = {}
    for line in data.decode().splitlines():
        if line.startswith('>'):
            key = line[1:].split()[0]
            assert key not in rows
            rows[key] = ''
        elif line.strip(): rows[key] += line.strip()
    return rows


def oracle(rows):
    mapped = {}
    for key, seq in rows.items():
        n = 0
        mapped[key] = {}
        for col, symbol in enumerate(seq):
            if symbol not in '-.':
                mapped[key][col] = n
                n += 1
    pairs = set()
    for a, b in itertools.combinations(sorted(rows), 2):
        for col in mapped[a].keys() & mapped[b].keys():
            pairs.add((a, mapped[a][col], b, mapped[b][col]))
    return pairs


@pytest.mark.asyncio
async def test_real_engines_through_project_compare_with_pair_oracle(tmp_path, monkeypatch):
    from routers import family_msa_results as route
    image = os.environ.get('BMS_TEST_FAMILY_MSA_SIF')
    if not image:
        pytest.skip('set BMS_TEST_FAMILY_MSA_SIF to the pinned engine image')
    sequences = synthetic_roster()
    rows = [{'row_id': f'r{i}', 'header': 'duplicate display', 'sequence': seq,
             'source_member_id': f'member{i}', 'occurrence_id': 'occ',
             'source_positions': list(range(len(seq))), 'transformation_provenance': 'synthetic original'}
            for i, seq in enumerate(sequences)]
    input_file = tmp_path / 'input.fasta'
    input_file.write_text(''.join(f'>r{i}\n{s}\n' for i, s in enumerate(sequences)))
    mapped_input = tmp_path / 'native.fasta'
    mapped_input.write_text(''.join(f'>bmsrow{i + 1:08d}\n{s}\n' for i, s in enumerate(sequences)))
    row_map = {f'bmsrow{i + 1:08d}': f'r{i}' for i in range(len(rows))}
    prefix = ['apptainer', 'exec', '--bind', f'{tmp_path}:{tmp_path}', image]
    env = {**os.environ, 'APPTAINERENV_TMPDIR': str(tmp_path)}
    outputs = {}
    commands = {
        'mafft': ['mafft', '--amino', '--thread', '1', '--retree', '2', '--maxiterate', '0', str(input_file)],
        'famsa': ['famsa', '-t', '1', str(mapped_input), str(tmp_path / 'famsa.fasta')],
        'muscle': ['muscle', '-align', str(mapped_input), '-output', str(tmp_path / 'muscle.fasta'), '-threads', '1', '-amino'],
    }
    for engine, args in commands.items():
        proc = subprocess.run(prefix + args, capture_output=True, timeout=120, check=False, env=env)
        assert proc.returncode == 0, engine + ': ' + proc.stderr.decode(errors='replace')
        data = proc.stdout if engine == 'mafft' else (tmp_path / f'{engine}.fasta').read_bytes()
        parsed = parse_fasta(data)
        if engine != 'mafft':
            assert set(parsed) == set(row_map)
            parsed = {row_map[key]: value for key, value in parsed.items()}
        assert set(parsed) == {f'r{i}' for i in range(len(rows))}
        assert len({len(x) for x in parsed.values()}) == 1
        for i, seq in enumerate(sequences):
            assert parsed[f'r{i}'].replace('-', '').replace('.', '').upper() == seq
        outputs[engine] = (data, oracle(parsed))
    assert len({frozenset(value[1]) for value in outputs.values()}) == 3
    async def project_result(request, session, project, receipt):
        assert project == 'p'
        entries = [{'id': 'a', 'role': 'alignment', 'logical_path': 'attempt/alignment.fasta'},
                   {'id': 'receipt', 'role': 'receipt'}]
        blobs = {'a': outputs[receipt][0], 'receipt': json.dumps({'requested': {'roster': {'molecule': 'protein', 'rows': rows}}}).encode()}
        if receipt != 'mafft':
            entries.append({'id': 'map', 'role': 'row_map', 'logical_path': 'attempt/row-map.json'})
            blobs['map'] = json.dumps(row_map).encode()
        async def read(key): return blobs[key]
        return entries, read
    monkeypatch.setattr(route, 'project_result', project_result)
    for left, right in itertools.combinations(outputs, 2):
        response = await route.compare_project_results(None, 'p', route.ComparisonInput(
            left_receipt_id=left, left_artifact_id='a', right_receipt_id=right, right_artifact_id='a'), None)
        agreement = route.ComparisonResponse.model_validate(response).report.agreement
        a, b = outputs[left][1], outputs[right][1]
        assert (agreement.left_aligned_pairs, agreement.right_aligned_pairs, agreement.shared_aligned_pairs, agreement.union_aligned_pairs) == (len(a), len(b), len(a & b), len(a | b))
        assert agreement.jaccard == len(a & b) / len(a | b)
        assert agreement.matched_rows == len(rows)
        print(f'{left} vs {right}: left={len(a)} right={len(b)} shared={len(a & b)} union={len(a | b)} jaccard={agreement.jaccard:.6f}')
