"""Private pooled metadata around retained native BAM, never an execution worker.

The core fixture's manifest predates canonical API metadata (null group and
canonical encoding). Reclassify its unchanged real BAM through the native
classifier against canonicalized metadata; do not fabricate assignment output.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
from datetime import datetime, timedelta

POOLED_JOB = '4386c154-1d45-435c-a652-aef328080338'

async def seed_pooled(root, factory):
    source_env = os.environ.get('ONT_ACCEPTANCE_POOLED_SOURCE')
    if not source_env:
        return
    from database import Job, NgsReferenceSetManifest, NgsPooledReferenceTarget, MolBioNgsReceipt, ExecutionTarget
    from services.ont_pooled_reference_assignment import canonical_json_bytes, _canonical_manifest_sha256
    from scripts.pooled_ont_reference_assignment import run_classify, run_preflight
    source = Path(source_env).resolve(strict=True)
    snapshot = root / 'inputs/pooled-retained-snapshot'
    output = root / 'state/bms_results' / POOLED_JOB / 'pooled_reference_assignment'
    shutil.copytree(source / 'snapshot', snapshot, dirs_exist_ok=True)
    shutil.copytree(source / 'output', output, dirs_exist_ok=True)
    reads = root / 'inputs/reads.fastq'
    shutil.copyfile(source / 'reads.fastq', reads)
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    for entry in manifest['entries']:
        entry['indistinguishable_group'] = entry.get('indistinguishable_group')
    manifest['manifest_sha256'] = _canonical_manifest_sha256(manifest)
    (snapshot / 'manifest.json').write_bytes(canonical_json_bytes(manifest))
    native = [shutil.which('apptainer'), 'exec', '--cleanenv', '--containall', '--no-home',
              '--bind', f'{root}:{root}', '--bind', f'{source}:{source}',
              os.environ['ONT_ACCEPTANCE_NATIVE_IMAGE'], 'samtools']
    prior = json.loads((output / 'assignment_summary.json').read_text())
    run_preflight(snapshot / 'manifest.json', snapshot, reads, output)
    run_classify(snapshot / 'manifest.json', snapshot, reads,
                 output / 'valid_reads.fastq', output / 'fastq_preflight.json',
                 output / 'pooled_assignment.bam', native, output / 'combined_intended_reference.fasta',
                 output, prior['policy']['min_mapq'], prior['policy']['min_alignment_score_margin'])
    current = json.loads((output / 'assignment_summary.json').read_text())
    for key in ['counts', 'read_assignments', 'disposition_counts', 'accounting', 'targets']:
        assert current[key] == prior[key], key
    assert (output / 'pooled_assignment.bam').read_bytes() == (source / 'output/pooled_assignment.bam').read_bytes()
    now = datetime.utcnow()
    token = os.environ['ONT_ACCEPTANCE_TOKEN']
    async with factory() as session:
        if await session.get(Job, POOLED_JOB):
            return
        session.add(Job(id=POOLED_JOB, name='Retained pooled BAM (synthetic metadata; native reclassification)',
            model_id='nanopore', mode='pooled_reference_assignment', status='completed', queue_status='completed',
            output_dir=str(output.parent), execution_target_id=None,
            params={'fastq_path':str(reads),'fastq_sha256':hashlib.sha256(reads.read_bytes()).hexdigest(),
                'ont_workflow_id':'ont_pooled_reference_assignment', 'scientific_status':'REVIEW',
                'release_state':'awaiting_operator_release','reference_set_manifest_sha256':manifest['manifest_sha256'],
                'pooled_assignment_min_mapq':prior['policy']['min_mapq'],
                'pooled_assignment_min_alignment_score_margin':prior['policy']['min_alignment_score_margin']},
            stage_outputs={'pooled_reference_assignment':[str(p) for p in output.iterdir() if p.is_file()]},
            provenance={'acceptance_fixture':'Synthetic SQL identity; genuine retained BAM reclassified by native owner',
                'alignment_access_scheme':'opaque_job_capability_v1',
                'alignment_access_token_sha256':hashlib.sha256(token.encode()).hexdigest()}))
        await session.flush()
        session.add(NgsReferenceSetManifest(id=manifest['manifest_id'], manifest_schema=manifest['schema'], mode='pooled',
            source_job_id=POOLED_JOB, target_workflow='ont_pooled_reference_assignment', idempotency_key='scratch-import',
            request_fingerprint=hashlib.sha256(b'synthetic metadata import').hexdigest(), manifest_path=str(snapshot/'manifest.json'),
            manifest_sha256=manifest['manifest_sha256'],manifest_json=manifest))
        for entry in manifest['entries']:
            rid='scratch-'+entry['target_id']
            session.add(MolBioNgsReceipt(id=rid,sequence_id=entry['molbio_sequence_id'],revision_id=entry['molbio_revision_id'],
                revision_sha256=entry['revision_sha256'], reference_snapshot_path=str(snapshot/entry['fasta_path']),
                reference_snapshot_sha256=entry['fasta_sha256'],expires_at=now+timedelta(days=1),consumed_at=now))
        await session.flush()
        for entry in manifest['entries']:
            session.add(NgsPooledReferenceTarget(id='scratch-'+entry['target_id'],reference_set_id=manifest['manifest_id'],
                target_id=entry['target_id'],label=entry['label'],sequence_id=entry['molbio_sequence_id'],revision_id=entry['molbio_revision_id'],
                revision_sha256=entry['revision_sha256'],receipt_id='scratch-'+entry['target_id'],fasta_path=entry['fasta_path'],
                fasta_sha256=entry['fasta_sha256'],indistinguishable_group=entry['indistinguishable_group']))
        session.add(ExecutionTarget(id='scratch-selected-target', provider='vast', provider_instance_id='synthetic-never-contact',
            active=True,state='ready',provider_metadata={'inventory':{'checked_at':now.isoformat(),'status':'complete','present':True,'running':True}}))
        await session.commit()
    inventory=[{'path':str(p.relative_to(output.parent)), 'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size_bytes':p.stat().st_size}
               for p in output.iterdir() if p.is_file()]
    (root/f'imported-{POOLED_JOB}.json').write_text(json.dumps({'source':str(source),'artifacts':inventory,
        'retained_bam_reclassified':True,'new_alignment_executed':False,'synthetic_metadata':True},indent=2))
