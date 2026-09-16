"""G-01 caller transport, using real Groovy rendering and non-model commands.

Requires BMS_TEST_NEXTFLOW_JAR (existing local Nextflow/Groovy jar), java,
bash, pytest and Biopython. No Nextflow task, container or model is launched.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALLERS = [
    ('boltz.nf', 'AlignBoltz', 'align_boltz.py'),
    ('antibody_batch.nf', 'AlignBoltzValidation', 'align_boltz.py'),
    ('antibody_batch.nf', 'BatchProtenixValidation', 'align_protenix.py'),
]


def render(tmp_path, caller, params):
    """Evaluate the actual process script preamble and GString, not a replica."""
    module, process, _ = caller
    source = (ROOT / 'modules' / module).read_text()
    block = source.split('process ' + process + ' {', 1)[1].split('\nprocess ', 1)[0]
    script = block.split('    script:', 1)[1].rsplit('}', 1)[0]
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    assert jar and Path(jar).is_file(), 'Set BMS_TEST_NEXTFLOW_JAR to the existing local Groovy-capable Nextflow jar'
    values = {'code_root': str(ROOT), **params}
    (tmp_path / 'params.json').write_text(json.dumps(values))
    runner = tmp_path / 'render.groovy'
    runner.write_text('''
import groovy.json.JsonSlurper
def params = new JsonSlurper().parse(new File(args[0]))
def task = [cpus: 2, index: 1]
def pdbs = 'fixture.pdb'
def original_designs = 'fixture.pdb'
def design_type = 'binder'
def normalizeGpuCsvValue = { ignored -> '' }
def rendered = {
''' + script + '\n}.call()\nprint rendered\n')
    return subprocess.run(['java', '-cp', jar, 'groovy.ui.GroovyMain', str(runner), str(tmp_path / 'params.json')],
                          capture_output=True, text=True, timeout=60)


INSTRUMENT = '''
# Only the real CLI parser is executed; scientific/model execution is excluded.
import argparse, ast, json, sys
from pathlib import Path
source = Path(sys.argv.pop(1))
module = ast.parse(source.read_text())
main = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
statements = []
for node in main.body:
    statements.append(node)
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'args' for t in node.targets):
        break
else:
    raise AssertionError('actual argparse boundary not found')
exec(compile(ast.Module(body=statements, type_ignores=[]), str(source), 'exec'))
Path('captured.json').write_text(json.dumps(vars(args), default=str))
'''


def capture(tmp_path, caller, params):
    rendered = render(tmp_path, caller, params)
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    # Execute only the actual rendered alignment invocation, never its enclosing
    # task (which includes inference, environment activation and publication).
    aligner = caller[2]
    match = re.search(r'(?m)^\s*python3?\s+\S*/' + re.escape(aligner) + r'\s+', rendered.stdout)
    assert match, rendered.stdout
    invocation = rendered.stdout[match.end():].split('2>&1 | tee', 1)[0]
    instrument = tmp_path / 'instrument.py'
    instrument.write_text(INSTRUMENT)
    command = ' '.join(map(shlex.quote, [sys.executable, str(instrument), str(ROOT / 'scripts' / aligner)]))
    # Protenix roles here represent the batch aggregates only; per-candidate
    # authority and rejection are exercised through its real main below.
    shell = 'set -euo pipefail\nPROTENIX_BINDER_CHAINS=H,L\nPROTENIX_TARGET_CHAINS=T\n' + command + ' ' + invocation
    result = subprocess.run(['bash', '-c', shell], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr + shell
    return json.loads((tmp_path / 'captured.json').read_text())


@pytest.mark.parametrize('caller', CALLERS)
@pytest.mark.parametrize('revision', [None, 1])
def test_actual_generated_alignment_command(tmp_path, caller, revision):
    args = capture(tmp_path, caller, {'core_protein_scientific_contract': revision,
                                    'binder_chains': 'H,L', 'target_chains': 'T'})
    assert args['core_protein_scientific_contract'] == revision
    assert args['chain_map_json'] is None  # Never fabricate alphabetical maps.
    if revision == 1 or caller[1] != 'AlignBoltz':
        assert args['binder_chains'] == 'H,L'
        assert args['target_chains'] == 'T'
    else:
        assert args['binder_chains'] == args['target_chains'] == ''
    if caller[1] == 'BatchProtenixValidation':
        assert args['chain_roles_json'] == 'chain_roles.json'


@pytest.mark.parametrize('caller', CALLERS)
@pytest.mark.parametrize('revision', [0, 2, False, 'bogus'])
def test_no_revision_downgrade(tmp_path, caller, revision):
    result = render(tmp_path, caller, {'core_protein_scientific_contract': revision})
    assert result.returncode != 0
    assert 'must be exactly 1' in result.stderr


@pytest.mark.parametrize('caller', CALLERS[:2])
def test_missing_request_roles_are_not_defaulted(tmp_path, caller):
    args = capture(tmp_path, caller, {'core_protein_scientific_contract': 1})
    assert args['binder_chains'] == args['target_chains'] == ''


@pytest.mark.parametrize('caller', CALLERS[:2])
def test_role_transport_does_not_execute_shell_bytes(tmp_path, caller):
    hostile = "H'$(touch INJECTED)"
    args = capture(tmp_path, caller, {'core_protein_scientific_contract': 1,
                                    'binder_chains': hostile, 'target_chains': 'T'})
    assert args['binder_chains'] == hostile
    assert not (tmp_path / 'INJECTED').exists()


@pytest.mark.parametrize('revision', [None, 1])
def test_generated_protenix_role_resolution_never_defaults_marked_batch(tmp_path, revision):
    rendered = render(tmp_path, CALLERS[2], {'core_protein_scientific_contract': revision})
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    start = rendered.stdout.index('    PROTENIX_BINDER_CHAINS=')
    # Select exactly the generated role-reading/fallback block, ending before
    # anchor/template work, without running any scientific task.
    block = rendered.stdout[start:].split('\n    if [ "false" = "true" ]; then', 1)[0]
    assert 'extract_target_templates.py' not in block
    (tmp_path / 'chain_roles.json').write_text(json.dumps({'entries': []}))
    result = subprocess.run(['bash', '-c', 'set -euo pipefail\n' + block +
                             '\nprintf "%s|%s" "$PROTENIX_BINDER_CHAINS" "$PROTENIX_TARGET_CHAINS"'],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ('H,L|T' if revision is None else '|')


@pytest.fixture
def protenix(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('transport_align_protenix', ROOT / 'scripts' / 'align_protenix.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_producer_roles_reach_actual_task_not_batch_union(tmp_path, monkeypatch, protenix):
    design = tmp_path / 'design'
    raw = tmp_path / 'raw'
    design.mkdir()
    raw.mkdir()
    (design / 'candidate.pdb').write_text('fixture: never parsed by instrumented alignment')
    (raw / 'confidence.json').write_text('{}')
    roles = tmp_path / 'roles.json'
    entry = {'name': 'candidate', 'binder_chain_ids': ['L', 'H'], 'target_chain_ids': ['T']}
    roles.write_text(json.dumps({'entries': [entry]}))
    monkeypatch.setattr(protenix, 'parse_design_name', lambda path: ('candidate_sample_0', 0))
    captured = []
    monkeypatch.setattr(protenix, 'align_structure', lambda task: (captured.append(task) or ('candidate', None)))
    argv = ['align_protenix', '--design_dir', str(design), '--protenix_dir', str(raw),
            '--output_dir', str(tmp_path / 'out'), '--design_type', 'binder',
            '--binder_chains', 'WRONG', '--target_chains', 'UNION',
            '--chain_roles_json', str(roles), '--core_protein_scientific_contract', '1']
    monkeypatch.setattr(sys, 'argv', argv)
    protenix.main()
    assert captured[0][4:6] == ('L,H', 'T')
    assert captured[0][8:] == (1, None)
    # A valid sidecar for another candidate must not fall back to aggregate CLI roles.
    roles.write_text(json.dumps({'entries': [{**entry, 'name': 'foreign'}]}))
    captured.clear()
    with pytest.raises(ValueError, match='Missing producer chain roles'):
        protenix.main()
    assert captured == []
    # Existing unmarked semantics remain in place.
    monkeypatch.setattr(sys, 'argv', argv[:-2])
    protenix.main()
    assert captured[0][4:6] == ('WRONG', 'UNION')


@pytest.mark.parametrize('entries', [[], [{'name': 'x'}],
    [{'name': 'x', 'binder_chain_ids': ['H'], 'target_chain_ids': ['H']}],
    [{'name': 'x', 'binder_chain_ids': ['H'], 'target_chain_ids': ['T']}] * 2])
def test_strict_producer_sidecar_rejects_missing_and_ambiguous_roles(tmp_path, protenix, entries):
    path = tmp_path / 'roles.json'
    path.write_text(json.dumps({'entries': entries}))
    with pytest.raises(ValueError):
        protenix.load_chain_roles(path, strict=True)


def test_identity_gate_requires_actual_mapping(protenix):
    from Bio.PDB import Chain, Model, Structure
    def mobile(ids):
        structure = Structure.Structure('fixture')
        model = Model.Model(0)
        structure.add(model)
        for chain_id in ids:
            model.add(Chain.Chain(chain_id))
        return structure
    protenix.validate_scientific_roles(['T', 'H', 'L'], mobile(['L', 'T', 'H']), ['H', 'L'], ['T'])
    with pytest.raises(ValueError, match='no shared-chain fallback'):
        protenix.validate_scientific_roles(['T', 'H', 'L'], mobile(['A', 'B', 'C']), ['H', 'L'], ['T'])
    with pytest.raises(ValueError, match='Missing, duplicate, or overlapping'):
        protenix.validate_scientific_roles(['H', 'T'], mobile(['H', 'T']), [], ['T'])
