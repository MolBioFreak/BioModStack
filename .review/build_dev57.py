"""Isolated BMS-DEV-57 assembly and software checks; publishes no canonical ref."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

BASE = '6438833c044a63ae67120fb023d81f7c4e2c9a56'
PATCH_SHA256 = '391b6193c2045d53c24989ec29773d56e7de07a8db86e3045edbd98457f9b627'
ROOT = Path(os.environ['GITHUB_WORKSPACE'])
AREA = Path(os.environ['RUNNER_TEMP']) / 'bms-dev57'
CANDIDATE = AREA / 'candidate'
BASELINE = AREA / 'baseline'
EVIDENCE = AREA / 'evidence'
RECORD = 'platform/api/config/ngs_molbio_runtime/runtime_implementation_v2.json'
NEW = 'tests/test_msa_provider_projection.py'
TESTS = [
    'tests/test_msa_provider_setup.py', 'tests/test_msa_provider_controls.py',
    'tests/test_model_msa_handoff.py', 'tests/test_fold_cp_input_msa_metadata.py',
    'tests/test_msa_bundle_integration.py', 'tests/test_msa_controller_handoff.py',
    'tests/test_msa_policy_saved_admission.py', 'tests/test_ngs_molbio_runtime_record_builder.py',
    '../../tests/test_msa_api_client.py',
]

def run(args, cwd=ROOT, **kwargs):
    return subprocess.run(args, cwd=cwd, check=True, **kwargs)

def git(*args, cwd=CANDIDATE):
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()

def commit(message):
    git('diff', '--cached', '--check')
    git('-c', 'user.name=BMS Review', '-c', 'user.email=bms-review@users.noreply.github.com',
        'commit', '-m', message)
    return git('rev-parse', 'HEAD')

def assemble():
    EVIDENCE.mkdir(parents=True)
    patch = ROOT / '.review/bms-dev57.patch'
    assert hashlib.sha256(patch.read_bytes()).hexdigest() == PATCH_SHA256
    assert git('rev-parse', BASE, cwd=ROOT) == BASE
    git('worktree', 'add', '-b', 'candidate-bms-dev57', str(CANDIDATE), BASE, cwd=ROOT)
    git('worktree', 'add', '--detach', str(BASELINE), BASE, cwd=ROOT)
    git('apply', '--index', str(patch))
    commit('fix(msa): keep transport keys out of provider science settings (BMS-DEV-57)\n\nUse only the existing five ColabFold scientific aliases, preserve Neurosnap\nprojection and closed schemas, and name unsupported keys without values.\nAdd no-IO launcher-shaped, identity, preparation and diagnostic regressions.')
    shutil.copyfile(patch, EVIDENCE / 'source.patch')

def bind():
    git('rm', RECORD)
    freeze = commit('build(runtime): freeze BMS-DEV-57 provider settings repair')
    tree = git('rev-parse', 'HEAD^{tree}')
    (EVIDENCE / 'freeze.txt').write_text(freeze+'\n')
    (EVIDENCE / 'freeze-tree.txt').write_text(tree+'\n')
    with (EVIDENCE / 'freeze.object').open('wb') as stream:
        run(['git', 'cat-file', 'commit', freeze], cwd=CANDIDATE, stdout=stream)
    archive = AREA / 'freeze.tar'
    git('archive', '--format=tar', '-o', str(archive), freeze)
    frozen = AREA / 'frozen'
    frozen.mkdir()
    run(['tar', '-xf', str(archive), '-C', str(frozen)])
    run([str(CANDIDATE/'platform/api/.venv/bin/python'),
        str(frozen/'scripts/build_ngs_molbio_runtime_implementation_record.py'),
        '--successor-source-commit', freeze, '--successor-source-tree', tree,
        '--successor-commit-object', str(EVIDENCE/'freeze.object')],
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    shutil.copyfile(frozen/RECORD, CANDIDATE/RECORD)
    git('add', RECORD)
    revision = commit('build(runtime): bind BMS-DEV-57 provider settings repair')
    (EVIDENCE/'commit.txt').write_text(revision+'\n')
    (EVIDENCE/'tree.txt').write_text(git('rev-parse', 'HEAD^{tree}')+'\n')
    (EVIDENCE/'commits.txt').write_text(git('log', '--reverse', '--format=%H %s', BASE+'..HEAD')+'\n')
    with (EVIDENCE/'commit.object').open('wb') as stream:
        run(['git', 'cat-file', 'commit', revision], cwd=CANDIDATE, stdout=stream)
    git('archive', '--format=tar.gz', '-o', str(EVIDENCE/'source.tar.gz'), revision)
    git('bundle', 'create', str(EVIDENCE/'review.bundle'), BASE+'..HEAD')
    git('diff', '--check', BASE+'..HEAD')

def isolated(root, arguments, output):
    forwarded = ['HOME', 'USER', 'LOGNAME', 'PATH', 'UV_PYTHON', 'UV_CACHE_DIR']
    environment = [f'{key}={os.environ[key]}' for key in forwarded if key in os.environ]
    environment += [f'UV_PROJECT_ENVIRONMENT={CANDIDATE}/platform/api/.venv',
        'PYTHONDONTWRITEBYTECODE=1', '_BIOXP_PYTEST_NETNS=1',
        f'_BIOXP_PYTEST_PARENT_NETNS={os.readlink("/proc/self/ns/net")}']
    command = ['sudo', 'env', *environment, 'unshare', '--net',
        f'--setgid={os.getgid()}', f'--setuid={os.getuid()}',
        'timeout', '--signal=TERM', '--kill-after=15s', '600s',
        'uv', 'run', '--offline', '--frozen', '--group', 'dev', 'python', *arguments]
    with output.open('w') as stream:
        result = subprocess.run(command, cwd=root/'platform/api', stdout=stream,
                                stderr=subprocess.STDOUT, check=False)
    print(output.name, 'exit', result.returncode, flush=True)
    (output.with_suffix('.exit')).write_text(str(result.returncode)+'\n')
    return result.returncode

def counts(path):
    root = ET.parse(path).getroot()
    cases = root.findall('.//testcase')
    assert cases, f'No cases in {path}'
    records = {}
    for case in cases:
        identity = case.attrib.get('classname','')+'::'+case.attrib['name']
        assert identity not in records, f'Duplicate case {identity}'
        failure = case.find('failure')
        error = case.find('error')
        records[identity] = ('failed' if failure is not None else 'error' if error is not None
                             else 'skipped' if case.find('skipped') is not None else 'passed')
    return dict(total=len(records), **{key:list(records.values()).count(key)
                for key in ('passed','failed','error','skipped')}), records

def verify():
    baseline_code = isolated(BASELINE, ['-m','pytest','-q', *TESTS,
        '--junitxml='+str(EVIDENCE/'baseline.xml')], EVIDENCE/'baseline.log')
    shutil.copyfile(CANDIDATE/'platform/api'/NEW, BASELINE/'platform/api'/NEW)
    red_code = isolated(BASELINE, ['-m','pytest','-q',NEW,
        '--junitxml='+str(EVIDENCE/'baseline-new.xml')], EVIDENCE/'baseline-new.log')
    candidate_code = isolated(CANDIDATE, ['-m','pytest','-q',NEW,*TESTS,
        '--junitxml='+str(EVIDENCE/'candidate.xml')], EVIDENCE/'candidate.log')
    repro = str(ROOT/'.review/reproduce_dev57.py')
    for label, worktree in [('before',BASELINE), ('after',CANDIDATE)]:
        assert isolated(worktree,[repro,str(EVIDENCE/(label+'.json'))],
                        EVIDENCE/(label+'.log')) == 0
    before=json.loads((EVIDENCE/'before.json').read_text())
    after=json.loads((EVIDENCE/'after.json').read_text())
    assert before['colabfold_api']['outcome'] == 'unsupported scientific setting keys'
    assert after['colabfold_api']['outcome'] == 'accepted'
    assert before['neurosnap_api'] == after['neurosnap_api']
    assert after['neurosnap_api']['outcome'] == 'accepted'
    assert after['refusal'] == ('unsupported scientific setting keys: colabfold_api_host, '
        'colabfold_api_min_interval, colabfold_api_poll_interval')
    assert 'SECRET' not in after['refusal']
    base_summary, base_cases = counts(EVIDENCE/'baseline.xml')
    red_summary, red_cases = counts(EVIDENCE/'baseline-new.xml')
    summary, candidate_cases = counts(EVIDENCE/'candidate.xml')
    comparison = dict(base=BASE, candidate=git('rev-parse','HEAD'), baseline=base_summary,
        candidate_tests=summary, new_tests_on_baseline=red_summary,
        baseline_exit=baseline_code, candidate_exit=candidate_code, regression_exit=red_code)
    (EVIDENCE/'validation.json').write_text(json.dumps(comparison,indent=2)+'\n')
    print(json.dumps(comparison,indent=2),flush=True)
    assert baseline_code == 0, 'Existing baseline selection did not pass; inspect evidence'
    assert red_code == 1 and red_summary['failed'] > 0 and red_summary['error'] == 0
    assert candidate_code == 0 and summary['failed'] == summary['error'] == summary['skipped'] == 0
    assert set(base_cases) | set(red_cases) == set(candidate_cases)
    assert len(red_cases) == 32, 'New regression denominator changed'
    # Validate the real updater against the exact committed candidate, not a dirty tree.
    path = CANDIDATE/'scripts/biomodstack_dev_sync.py'
    spec = importlib.util.spec_from_file_location('dev57_updater',path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    revision=git('rev-parse','HEAD')
    result=module.validate_candidate_runtime_authority(CANDIDATE,revision)
    (EVIDENCE/'updater-authority.json').write_text(json.dumps(
        {'revision':revision,'result':result},indent=2,sort_keys=True)+'\n')
    assert not git('status','--porcelain'), 'Candidate worktree changed during validation'
    (EVIDENCE/'verified.txt').write_text(revision+'\n')

if __name__ == '__main__':
    {'assemble':assemble,'bind':bind,'verify':verify}[sys.argv[1]]()
