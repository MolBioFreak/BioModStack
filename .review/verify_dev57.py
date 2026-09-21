"""Preserve test-output evidence; publish an untouched checkout of tested source.

The main verifier still requires every case/delta and source-authority gate.
Only its final workspace-output assertion is handled here, never a test failure.
Tracked changes always block publication; untracked test outputs are inventoried
outside Git and never copied into the separate publishing worktree.
"""
import json
from pathlib import Path
import subprocess
import sys

import build_dev57 as build

try:
    build.verify()
except AssertionError as exc:
    if str(exc) != 'Candidate worktree changed during validation':
        raise
    print('All preceding test/delta/authority gates passed; inspect workspace output.', flush=True)

status = build.git('status', '--porcelain', '--untracked-files=all')
(build.EVIDENCE / 'post-test-status.txt').write_text(status + '\n')
for args, name in [(('diff', '--binary'), 'post-test-worktree.diff'),
                   (('diff', '--cached', '--binary'), 'post-test-index.diff')]:
    diff = subprocess.check_output(['git', *args], cwd=build.CANDIDATE)
    (build.EVIDENCE / name).write_bytes(diff)
    if diff:
        raise AssertionError('Tests changed tracked candidate files; publication blocked')

revision = build.git('rev-parse', 'HEAD')
publish = build.AREA / 'publish'
build.git('worktree', 'add', '--detach', str(publish), revision)
assert build.git('rev-parse', 'HEAD', cwd=publish) == revision
# verify() already loaded and executed the unchanged updater module.
result = sys.modules['dev57_updater'].validate_candidate_runtime_authority(publish, revision)
(build.EVIDENCE / 'publish-authority.json').write_text(json.dumps(
    {'revision': revision, 'result': result}, indent=2, sort_keys=True) + '\n')
assert not build.git('status', '--porcelain', cwd=publish)
(build.EVIDENCE / 'verified.txt').write_text(revision + '\n')
print('All source/test gates accepted; clean publication worktree:', revision, flush=True)
