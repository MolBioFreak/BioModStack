#!/usr/bin/env python3
"""Shared local/worker review checkpoints. No HTTP, science, or resource release.

The controller owns authorization and quiescence attestation; see the integration
contract. State and context paths are scheduler-owned, never browser parameters.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

VERSION = 'bms.interactive-checkpoint/1'
BINDINGS = ('job_id', 'attempt_id', 'worker_id', 'source_digest', 'plan_digest')
STAGES = {'post_rfantibody': 'plr_backbone_input_pdbs',
          'post_fampnn': 'plr_sequence_input_pdbs',
          'post_structure_validation': 'plr_validation_input_pdbs',
          'post_ppiflow_generator': None}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def file_hash(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.checkpoint-')
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read(path):
    return json.loads(Path(path).read_text())


def contained(root, relative):
    root = Path(root).resolve()
    p = Path(relative)
    if p.is_absolute() or '..' in p.parts:
        raise ValueError('artifact path must be contained and relative')
    target = root / p
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ValueError('artifact symlink/escape')
    return target


@contextmanager
def locked(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield root


def validate_context(context):
    if set(context) != set(BINDINGS) or any(not isinstance(context[k], str) or not context[k] for k in BINDINGS):
        raise ValueError('closed checkpoint context requires all identity bindings')
    return context


def verify(root, receipt):
    body = {k: v for k, v in receipt.items() if k != 'checkpoint_id'}
    if receipt['checkpoint_id'] != digest(body) or receipt['schema_version'] != VERSION:
        raise ValueError('checkpoint identity/schema mismatch')
    for artifact in receipt['artifacts']:
        p = contained(root, artifact['path'])
        if not p.is_file() or p.stat().st_size != artifact['size_bytes'] or file_hash(p) != artifact['sha256']:
            raise ValueError('checkpoint artifact missing or corrupt: ' + artifact['artifact_id'])
    return receipt


def seal(root, context, stage, candidates, review=(), metadata=None):
    """Copy declared task outputs, not asynchronously published directories.

    Candidate sequence is authoritative; no sorting, ranking, filtering or science
    occurs here. Reopening the same stage must reproduce the exact sealed identity.
    """
    validate_context(context)
    if stage not in STAGES:
        raise ValueError('unsupported checkpoint stage')
    with locked(root) as root:
        artifacts = []
        source_bindings = {}
        seen = set()
        for role, paths in [('candidate', candidates), ('review', review)]:
            for source in paths:
                staged_source = str(Path(source).absolute())
                source = Path(source).resolve(strict=True)  # Nextflow stages symlinks.
                if not source.is_file():
                    raise ValueError('declared artifact is not a file')
                sha = file_hash(source)
                artifact_id = f'{role}:{source.name}' if role == 'candidate' else f'review:{sha}:{source.name}'
                for alias in (staged_source, str(source)):
                    source_bindings.setdefault(alias, artifact_id)
                key = (role, source.name) if role == 'candidate' else (role, source.name, sha)
                if key in seen:
                    if role == 'review':
                        continue
                    raise ValueError('duplicate candidate basename')
                seen.add(key)
                relative = f'artifacts/{sha}/{source.name}'
                destination = contained(root, relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    fd, temporary = tempfile.mkstemp(dir=destination.parent)
                    try:
                        with os.fdopen(fd, 'wb') as target, source.open('rb') as origin:
                            shutil.copyfileobj(origin, target)
                            target.flush()
                            os.fsync(target.fileno())
                        if file_hash(temporary) != sha:
                            raise ValueError('artifact changed while sealing')
                        os.chmod(temporary, 0o444)
                        os.replace(temporary, destination)
                        for parent in (destination.parent, destination.parent.parent, root):
                            directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                            try:
                                os.fsync(directory_fd)
                            finally:
                                os.close(directory_fd)
                    finally:
                        if os.path.exists(temporary):
                            os.unlink(temporary)
                artifacts.append({'artifact_id': artifact_id, 'role': role,
                                  'path': relative, 'sha256': sha,
                                  'size_bytes': source.stat().st_size,
                                  'format': source.suffix.lstrip('.')})
        if not any(a['role'] == 'candidate' for a in artifacts):
            raise ValueError('cannot open review without candidates')
        # Typed source aliases let native import adapters resolve path-bearing
        # manifest fields without rewriting archived bytes or guessing prefixes.
        bindings = {'schema_version': 'bms.review-artifact-bindings/1',
                    'bindings': [{'worker_path': alias, 'artifact_id': artifact_id}
                                 for alias, artifact_id in sorted(source_bindings.items())]}
        encoded = json.dumps(bindings, sort_keys=True, indent=2).encode()
        binding_sha = hashlib.sha256(encoded).hexdigest()
        binding_path = f'artifacts/{binding_sha}/review_artifact_bindings.json'
        binding_target = contained(root, binding_path)
        if not binding_target.exists():
            atomic_json(binding_target, bindings)
            binding_target.chmod(0o444)
            for parent in (binding_target.parent.parent, root):
                fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        artifacts.append({'artifact_id': 'review:artifact_bindings', 'role': 'review',
                          'path': binding_path, 'sha256': binding_sha,
                          'size_bytes': len(encoded), 'format': 'json'})
        artifacts = [a for a in artifacts if a['role'] == 'candidate'] + sorted(
            (a for a in artifacts if a['role'] == 'review'), key=lambda a: a['artifact_id'])
        receipt = {'schema_version': VERSION, **context, 'stage': stage,
                   'artifacts': artifacts, 'metadata': metadata or {},
                   'science_complete': False, 'retrieval_policy': 'explicit_review_only'}
        receipt['checkpoint_id'] = digest(receipt)
        verify(root, receipt)
        if (root / 'receipt.json').exists():
            if read(root / 'receipt.json') != receipt:
                raise ValueError('stale stage/attempt or changed checkpoint artifacts')
        else:
            atomic_json(root / 'receipt.json', receipt)
        # Recover a crash between immutable receipt and mutable status publication.
        if not (root / 'state.json').exists():
            atomic_json(root / 'state.json', {'state': 'awaiting_review', 'sequence': 1})
        return receipt


def authorize_decision(root, decision, *, principal, authorize):
    """Host authorization adapter MUST verify job ownership + review retrieval.

    authorize(principal, receipt, decision) is trusted server code, not a request
    field. Returns True only after explicit authorized review-set retrieval.
    """
    with locked(root) as root:
        receipt = verify(root, read(root / 'receipt.json'))
        if authorize(principal, receipt, decision) is not True:
            raise PermissionError('review decision is not authorized')
        expected = {'schema_version', 'checkpoint_id', *BINDINGS, 'decision_id', 'action', 'selected_artifacts'}
        if set(decision) != expected or decision['schema_version'] != VERSION:
            raise ValueError('invalid closed decision schema')
        if any(decision[k] != receipt[k] for k in ('checkpoint_id', *BINDINGS)):
            raise ValueError('stale decision identity')
        if not isinstance(decision['decision_id'], str) or not decision['decision_id']:
            raise ValueError('decision_id required')
        if decision['action'] not in ('continue', 'reject'):
            raise ValueError('unsupported decision action')
        selected = decision['selected_artifacts']
        canonical = [{'artifact_id': a['artifact_id'], 'sha256': a['sha256']}
                     for a in receipt['artifacts'] if a['role'] == 'candidate']
        if not isinstance(selected, list) or any(a not in canonical for a in selected):
            raise ValueError('unknown or changed selected artifact')
        if selected != [a for a in canonical if a in selected]:
            raise ValueError('selection must retain canonical order without duplicates')
        if (decision['action'] == 'continue' and not selected) or (decision['action'] == 'reject' and selected):
            raise ValueError('invalid selection for decision action')
        state = read(root / 'state.json')
        if state['state'] != 'awaiting_review':
            raise ValueError('decision already consumed; replay rejected')
        state.update(state='approved' if decision['action'] == 'continue' else 'rejected',
                     decision=decision, principal=str(principal), sequence=state['sequence'] + 1)
        atomic_json(root / 'state.json', state)
        return state


def prepare_continuation(root, context, *, execution_id, quiescence, reservation):
    """Trusted worker scheduler call AFTER predecessor termination and reacquisition.

    A repeated execution_id reopens one launch, never authorizes another submission.
    Transport uncertainty must reconcile this ID with Nextflow, not launch anew.
    """
    validate_context(context)
    if not isinstance(execution_id, str) or not execution_id:
        raise ValueError('execution identity required')
    if not isinstance(quiescence, dict) or set(quiescence) != {
            'predecessor_execution_id', 'worker_id', 'attempt_id', 'exit_observed', 'descendants_quiescent'}:
        raise ValueError('closed predecessor quiescence evidence required')
    if (not quiescence['predecessor_execution_id'] or quiescence['exit_observed'] is not True
            or quiescence['descendants_quiescent'] is not True
            or any(quiescence[k] != context[k] for k in ('worker_id', 'attempt_id'))):
        raise ValueError('predecessor is not proven quiescent on original worker/attempt')
    if not isinstance(reservation, dict) or set(reservation) != {
            'worker_id', 'attempt_id', 'plan_digest', 'lease_id', 'generation'}:
        raise ValueError('closed reacquired reservation identity required')
    if (any(reservation[k] != context[k] for k in ('worker_id', 'attempt_id', 'plan_digest'))
            or not reservation['lease_id'] or type(reservation['generation']) is not int
            or reservation['generation'] < 1):
        raise ValueError('reservation must bind original worker/attempt/plan and lease generation')
    with locked(root) as root:
        receipt = verify(root, read(root / 'receipt.json'))
        if any(context[k] != receipt[k] for k in BINDINGS):
            raise ValueError('continuation cannot change attempt/source/plan/worker')
        state = read(root / 'state.json')
        if state['state'] == 'continuing':
            if state['execution_id'] != execution_id:
                raise ValueError('continuation already claimed by another execution')
            return verify_continuation(root, context, execution_id)
        if state['state'] != 'approved':
            raise ValueError('explicit approved decision required')
        if receipt['stage'] == 'post_rfantibody' and len([
                a for a in receipt['artifacts'] if a['role'] == 'review'
                and Path(a['path']).name == 'region_manifest.json']) != 1:
            raise ValueError('backbone continuation requires sealed region manifest')
        selected_ids = [a['artifact_id'] for a in state['decision']['selected_artifacts']]
        selected = [a for a in receipt['artifacts'] if a['artifact_id'] in selected_ids]
        directory = contained(root, 'selected')
        directory.mkdir(exist_ok=True)
        for stale in directory.glob('.checkpoint-*'):
            stale.unlink()  # interrupted system-owned materialization, under lock
        for artifact in selected:
            target = directory / Path(artifact['path']).name
            if target.exists() and file_hash(target) != artifact['sha256']:
                raise ValueError('selected artifact changed')
            if not target.exists():
                fd, temporary = tempfile.mkstemp(dir=directory, prefix='.checkpoint-')
                try:
                    with os.fdopen(fd, 'wb') as out, contained(root, artifact['path']).open('rb') as source:
                        shutil.copyfileobj(source, out)
                        out.flush()
                        os.fsync(out.fileno())
                    if file_hash(temporary) != artifact['sha256']:
                        raise ValueError('selected artifact changed during materialization')
                    os.replace(temporary, target)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        state.update(state='continuing', execution_id=execution_id, quiescence=quiescence,
                     reservation=reservation, sequence=state['sequence'] + 1)
        atomic_json(root / 'state.json', state)
        return verify_continuation(root, context, execution_id)


def verify_continuation(root, context, execution_id):
    root = Path(root).resolve()
    receipt = verify(root, read(root / 'receipt.json'))
    state = read(root / 'state.json')
    if any(context[k] != receipt[k] for k in BINDINGS):
        raise ValueError('continuation identity mismatch')
    if state['state'] != 'continuing' or state['execution_id'] != execution_id:
        raise ValueError('unclaimed or stale continuation')
    selected = state['decision']['selected_artifacts']
    contained(root, 'selected')
    for artifact in selected:
        target = root / 'selected' / artifact['artifact_id'].split(':', 1)[1]
        if not target.is_file() or target.is_symlink() or file_hash(target) != artifact['sha256']:
            raise ValueError('selected artifact missing/corrupt')
    if {p.name for p in (root / 'selected').iterdir()} != {a['artifact_id'].split(':', 1)[1] for a in selected}:
        raise ValueError('undeclared selected artifacts')
    params: dict = {'interactive_gate_continue': True}
    key = STAGES[receipt['stage']]
    if key:
        params[key] = str(root / 'selected')
    if receipt['stage'] == 'post_rfantibody':
        manifests = [a for a in receipt['artifacts'] if a['role'] == 'review' and Path(a['path']).name == 'region_manifest.json']
        if len(manifests) != 1:
            raise ValueError('backbone continuation requires sealed region manifest')
        params['plr_region_manifest'] = str(contained(root, manifests[0]['path']))
    return {'schema_version': VERSION, 'checkpoint_id': receipt['checkpoint_id'],
            'stage': receipt['stage'], 'execution_id': execution_id, 'params': params,
            'selected_paths': [str(root / 'selected' / a['artifact_id'].split(':', 1)[1]) for a in selected],
            'review_paths': [str(contained(root, a['path'])) for a in receipt['artifacts'] if a['role'] == 'review']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    seal_parser = sub.add_parser('seal')
    seal_parser.add_argument('--request', required=True)
    seal_parser.add_argument('--output', required=True)
    check = sub.add_parser('verify-continuation')
    for name in ('root', 'context', 'execution-id'):
        check.add_argument('--' + name, required=True)
    args = parser.parse_args()
    if args.command == 'seal':
        request = read(args.request)
        result = seal(request['root'], read(request['context']), request['stage'],
                      request['candidates'], request.get('review', []), request.get('metadata'))
        atomic_json(args.output, result)
    else:
        result = verify_continuation(args.root, read(args.context), args.execution_id)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
