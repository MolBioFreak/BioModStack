"""Family MSA execution inside the existing owned workflow runner."""
import asyncio
import hashlib
import json
import os
import signal
import uuid
from datetime import datetime
from pathlib import Path

from bms_alignment import AlignmentRequest
from bms_alignment.native import compile_mafft


def compile_command(payload: dict, executable: str, input_path: str) -> list[str]:
    return compile_mafft(AlignmentRequest.model_validate(payload), executable, input_path)


async def run_owned_job(job_id: str, payload: dict, output_dir: str, session_factory) -> None:
    """Run MAFFT in the transient unit; the unit owns and cancels its children."""
    from database import Job, JobArtifact
    from sqlalchemy import or_, update

    async with session_factory() as session:
        job = await session.get(Job, job_id)
        if not job or job.status != 'running' or job.queue_status == 'cancelling':
            return
        attempt = job.retry_count or 0
        run_identity = os.environ.get('BMS_TRANSIENT_WORKFLOW_UNIT_NAME') or job.nextflow_run_id
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
    try:
        request = AlignmentRequest.model_validate({key: payload[key] for key in ('roster', 'settings')})
        input_file.write_text(''.join(f'>{r.row_id}\n{r.sequence}\n' for r in request.roster.rows), encoding='utf8')
        image = os.environ.get('BMS_FAMILY_MSA_SIF', '').strip()
        if image:
            argv = ['apptainer', 'exec', '--cleanenv', '--no-home', '--bind', f'{work}:{work}', image]
        argv += compile_mafft(request, 'mafft', str(input_file))
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
            error = f'MAFFT exited with status {code}'
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        with log.open('a', encoding='utf8') as stream:
            stream.write(error + '\n')
    receipt_path.write_text(json.dumps({'requested': payload, 'argv': argv, 'returncode': code,
        'effective_settings': payload.get('settings'), 'error': error}, indent=2) + '\n', encoding='utf8')
    files = [(input_file, 'input', 'text/x-fasta'), (output, 'alignment', 'text/x-fasta'),
             (log, 'engine_log', 'text/plain'), (receipt_path, 'receipt', 'application/json'),
             (Path(str(input_file) + '.tree'), 'guide_tree', 'text/plain')]
    async with session_factory() as session:
        # Cancellation and a successor attempt must never be overwritten.
        result = await session.execute(update(Job).where(Job.id == job_id, Job.status == 'running',
            Job.queue_status != 'cancelling', or_(Job.nextflow_run_id == run_identity,
                                                  Job.nextflow_run_id.is_(None))).values(
            status='failed' if error else 'completed', queue_status='failed' if error else 'completed',
            completed_at=datetime.utcnow(), error_message=error, assigned_gpu=None))
        if result.rowcount:
            for path, role, media_type in files:
                if path.is_file():
                    digest = hashlib.sha256()
                    with path.open('rb') as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                            digest.update(chunk)
                    session.add(JobArtifact(id=str(uuid.uuid4()), owner_job_id=job_id, attempt=attempt,
                        logical_path=str(path.relative_to(Path(output_dir).resolve())), storage_path=str(path),
                        sha256=digest.hexdigest(), bytes=path.stat().st_size, media_type=media_type,
                        provenance={'role': role, 'run_identity': run_identity}))
        await session.commit()
