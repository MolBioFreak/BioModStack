"""Family MSA execution inside the existing owned workflow runner."""
import asyncio
import hashlib
import json
import os
import signal
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

from bms_alignment import AlignmentRequest
from bms_alignment.native import compile_mafft


def compile_command(payload: dict, executable: str, input_path: str) -> list[str]:
    request = AlignmentRequest.model_validate({key: value for key, value in payload.items() if key != '_family_msa_requested'})
    if request.settings.engine != 'mafft':
        raise ValueError('compile_command is MAFFT-only; use the native engine adapter')
    return compile_mafft(request, executable, input_path)


async def run_owned_job(job_id: str, payload: dict, output_dir: str, session_factory) -> None:
    """Run MAFFT in the transient unit; the unit owns and cancels its children."""
    from database import Job, JobArtifact
    from services.execution_ownership import cancellation_intent_requested, latest_execution_attempt
    from sqlalchemy import text

    async with session_factory() as session:
        job = await session.get(Job, job_id)
        if not job or job.status != 'running' or cancellation_intent_requested(job):
            return
        attempt = job.retry_count or 0
        run_identity = os.environ.get('BMS_TRANSIENT_WORKFLOW_UNIT_NAME') or job.nextflow_run_id
        nonce = os.environ.get('BMS_TRANSIENT_WORKFLOW_OWNER_NONCE', '')
        invocation = os.environ.get('INVOCATION_ID', '')
        authority = latest_execution_attempt(job.params)
        if invocation and (not authority or authority.get('unit') != run_identity
                           or authority.get('owner_nonce') != nonce
                           or authority.get('invocation_id') != invocation
                           or authority.get('state') != 'started'):
            return
        original_run_id = job.nextflow_run_id
        original_retry = job.retry_count
    # A unique directory also isolates re-entry after an interrupted attempt.
    work = Path(output_dir).resolve() / 'family_msa' / f'attempt-{attempt}-{uuid.uuid4().hex}'
    work.mkdir(parents=True)
    input_file = work / 'input.fasta'
    output = work / 'alignment.fasta'
    log = work / 'engine.log'
    receipt_path = work / 'receipt.json'
    code = None
    error = None
    argv = []
    runtime = None
    try:
        request = AlignmentRequest.model_validate({key: payload[key] for key in ('roster', 'settings')})
        input_file.write_text(''.join(f'>{r.row_id}\n{r.sequence}\n' for r in request.roster.rows), encoding='utf8')
        from services.family_msa.runtime import select_runtime
        image, selection = select_runtime()
        if image:
            runtime = {'kind': 'image', 'path': image, **selection}
            try:
                digest = hashlib.sha256()
                with open(image, 'rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                runtime['sha256'] = digest.hexdigest()
            except OSError as exc:
                runtime['identity_error'] = str(exc)
        else:
            runtime = {'kind': 'host', 'executable': shutil.which(request.settings.engine), **selection}
        if request.settings.engine == 'mafft':
            if image:
                argv = ['apptainer', 'exec', '--cleanenv', '--no-home', '--bind', f'{work}:{work}', image]
            argv += compile_mafft(request, 'mafft', str(input_file))
        else:
            (work / 'request.json').write_text(request.model_dump_json(), encoding='utf8')
            argv = [sys.executable, str(Path(__file__).with_name('worker.py')), str(work), image]
        with output.open('wb') as stdout, log.open('wb') as stderr:
            process = await asyncio.create_subprocess_exec(*argv, cwd=str(work), stdout=stdout, stderr=stderr,
                                                           start_new_session=True)
            try:
                code = await process.wait()
            except asyncio.CancelledError:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()
                raise
        if code:
            error = f'{request.settings.engine} exited with status {code}'
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        with log.open('a', encoding='utf8') as stream:
            stream.write(error + '\n')
    receipt_path.write_text(json.dumps({'requested': payload.get('_family_msa_requested', payload), 'argv': argv, 'returncode': code,
        'effective_settings': request.settings.model_dump(mode='json') if 'request' in locals() else payload.get('settings'), 'runtime': runtime, 'error': error}, indent=2) + '\n', encoding='utf8')
    files = [(input_file, 'input', 'text/x-fasta'),
             (log, 'engine_log', 'text/plain'), (receipt_path, 'receipt', 'application/json')]
    if payload.get('settings', {}).get('engine', 'mafft') == 'mafft':
        files += [(output, 'alignment', 'text/x-fasta'),
                  (Path(str(input_file) + '.tree'), 'guide_tree', 'text/plain')]
    else:
        files.extend([(work / 'request.json', 'native_request', 'application/json'),
                      (output, 'engine_stdout', 'text/plain'),
                      (work / 'worker-result.json', 'native_receipt', 'application/json')])
        for path in (work / 'native').rglob('*'):
            if not path.is_file():
                continue
            name = path.name
            role = ('guide_preparation' if 'guide-preparation' in path.parts and not name.endswith(('.nwk', '.tree'))
                    else 'alignment' if name == 'alignment.fasta' or name.startswith('ensemble.') and name.endswith('.fasta')
                    else 'guide_tree' if name.endswith(('.nwk', '.tree'))
                    else 'row_map' if name == 'row-map.json'
                    else 'native_receipt' if name == 'receipt.json'
                    else 'engine_log' if name == 'engine.log' else 'native_artifact')
            media = 'text/x-fasta' if name.endswith('.fasta') else 'application/json' if name.endswith('.json') else 'text/plain'
            files.append((path, role, media))
    async with session_factory() as session:
        # Serialize the authority read and terminal write with cancellation.
        await session.execute(text('BEGIN IMMEDIATE'))
        job = await session.get(Job, job_id)
        if (not job or job.status != 'running' or cancellation_intent_requested(job)
                or job.retry_count != original_retry
                or latest_execution_attempt(job.params) != authority
                or (job.nextflow_run_id != original_run_id and
                    not (authority and original_run_id is None and job.nextflow_run_id == run_identity))):
            return
        job.status = 'failed' if error else 'completed'
        job.queue_status = job.status
        job.completed_at = datetime.utcnow()
        job.error_message = error
        job.assigned_gpu = None
        artifacts = []
        for path, role, media_type in files:
            if path.is_file():
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                artifact = JobArtifact(id=str(uuid.uuid4()), owner_job_id=job_id, attempt=attempt,
                    logical_path=str(path.relative_to(Path(output_dir).resolve())), storage_path=str(path),
                    sha256=digest.hexdigest(), bytes=path.stat().st_size, media_type=media_type,
                    provenance={'role': role, 'run_identity': run_identity})
                artifacts.append(artifact)
                session.add(artifact)
        from services.family_msa.result_manifest import write_result_manifest
        try:
            manifest_path, manifest_data = write_result_manifest(
                work, job_id=job_id, attempt=attempt, run_identity=run_identity,
                artifacts=artifacts, status='failed' if error else 'completed')
        except Exception:
            # Publication is observational: it must not change Job outcome.
            import logging
            logging.getLogger(__name__).exception('family MSA result manifest unavailable for %s', job_id)
        else:
            session.add(JobArtifact(id=str(uuid.uuid4()), owner_job_id=job_id, attempt=attempt,
                logical_path=str(manifest_path.relative_to(Path(output_dir).resolve())),
                storage_path=str(manifest_path), sha256=hashlib.sha256(manifest_data).hexdigest(),
                bytes=len(manifest_data), media_type='application/json',
                provenance={'role': 'result_manifest', 'run_identity': run_identity}))
        await session.commit()
