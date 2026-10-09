"""Retained-byte receiving fixture, NOT scientific submission/admission evidence.

SQL identities, run metadata and profile approval records are synthetic controls.
Native bytes/receipts are copied unchanged. RNA's missing RG is never repaired.
Run only via ont_signal_acceptance.py in its private loopback namespace.
"""
import asyncio
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

REPO = Path(__file__).resolve().parents[3]
ROOT = Path(os.environ['ONT_SIGNAL_ROOT']).resolve()
SOURCE = Path(os.environ['ONT_SIGNAL_NATIVE']).resolve(strict=True)
assert not ROOT.is_relative_to(REPO) and ROOT != SOURCE
for key, relative in {
    'HOME': 'home', 'XDG_CONFIG_HOME': 'config', 'BMS_DATA': 'state',
    'BMS_INPUTS': 'inputs', 'BMS_RESULTS_DIR': 'results', 'BMS_WORK': 'work',
    'BMS_ANALYSIS_CACHE': 'cache', 'BMS_DB_PATH': 'jobs.db',
    'BMS_EXPERIMENT_DB_PATH': 'experiments.db', 'BMS_MOLBIO_NGS_DB_PATH': 'molbio.db',
    'BMS_MOLBIO_NGS_REFERENCE_ROOT': 'references', 'TMPDIR': 'tmp',
}.items():
    path = ROOT / relative
    os.environ[key] = str(path)
    (path.parent if key.endswith('_PATH') else path).mkdir(parents=True, exist_ok=True)
os.environ['BMS_HOME'] = str(REPO)
os.environ['BMS_RUNTIME_MODE'] = 'dev'
for key in ['DATABASE_URL', 'BMS_DATABASE_URL', 'BMS_EXPERIMENT_DATABASE_URL', 'BMS_MOLBIO_NGS_DATABASE_URL']:
    os.environ.pop(key, None)
sys.path[:0] = [str(REPO / 'platform/api'), str(REPO)]
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from database import (Base, engine, async_session, Job, InputFile, OntInstrumentRun,
    OntInstrumentRunEvent, OntRawSignalRepresentation, OntRawSignalLookup,
    OntMoveTableSource, OntSignalCalibrationArtifact, OntSignalMappingProfile,
    OntSignalMappingJob, OntSignalMappingArtifact, OntSquigualiserViewJob,
    OntSignalViewerSession, OntSignalComparisonJob, OntSignalComparisonArtifact)
from routers import ont_runs, ont_signal_workbench
from services import ont_signal_workbench as service

TOKEN = 'synthetic-ont-ui-only'
NOTE = 'Synthetic receiving metadata for retained native own-read identity controls; not genomic placement, calibration approval or model admission evidence'
app = FastAPI()
@app.middleware('http')
async def isolation(request: Request, call_next):
    if request.headers.get('x-ont-acceptance') != TOKEN and request.cookies.get('ont-acceptance') != TOKEN:
        return JSONResponse({'detail': 'synthetic fixture auth required'}, 401)
    request.state.authenticated_principal = {'subject': 'synthetic-signal-operator', 'roles': ['operator']}
    # This fixture has no worker/lifespan. Only retained receiving mutations.
    if request.method not in {'GET', 'HEAD'} and not (
        '/raw-signal/waveforms' in request.url.path or
        '/viewer-sessions/' in request.url.path or request.url.path.endswith('/reviews')
    ):
        return JSONResponse({'detail': 'Fixture does not launch science'}, 405)
    return await call_next(request)
app.include_router(ont_runs.router, prefix='/api/ont')
app.include_router(ont_signal_workbench.router, prefix='/api/ont/signal-workbench')
@app.get('/acceptance-health')
def health():
    return {'root': str(ROOT), 'source': str(SOURCE), 'metadata': NOTE, 'science_executed': False}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
def descriptor(p):
    st, parent = p.stat(), p.parent.stat()
    return {'path': str(p), 'sha256': sha(p), 'bytes': st.st_size,
        'governed_root_path': str(p.parent), 'governed_relative_path': p.name,
        'governed_root_device': parent.st_dev, 'governed_root_inode': parent.st_ino,
        'device': st.st_dev, 'inode': st.st_ino, 'mtime_ns': st.st_mtime_ns, 'ctime_ns': st.st_ctime_ns}

def load(path):
    return json.loads(path.read_text())

async def seed():
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    target = ROOT / 'results/ont_signal_workbench/imported'
    shutil.copytree(SOURCE, target, dirs_exist_ok=True)
    inventory = [{'path': str(p.relative_to(target)), 'sha256': sha(p), 'size_bytes': p.stat().st_size}
                 for p in sorted(target.rglob('*')) if p.is_file()]
    assert all(sha(SOURCE / x['path']) == x['sha256'] for x in inventory)
    (ROOT / 'imported-native.json').write_text(json.dumps({'source': str(SOURCE), 'metadata': NOTE, 'files': inventory}, indent=2))
    now = datetime.utcnow()
    render = ont_signal_workbench.RenderParams(base_limit=100, signal_sample_limit=10000).model_dump()
    igv = {'alignment_display_mode': 'FULL', 'alignment_color_by': 'strand', 'alignment_group_by': 'none', 'reads_track_loaded': False}
    async with async_session() as s:
        for case in ['raw', 'dna', 'rna']:
            run = 'signal-run-' + case
            rawid = 'signal-raw-' + case
            work = target / case
            blow = work / ('dna.blow5' if case == 'raw' else 'real.blow5')
            manifest = {'artifacts': [{'kind': kind, **descriptor(p)}
                for kind, p in [('blow5', blow), ('blow5_index', Path(str(blow) + '.idx'))]]}
            s.add(OntInstrumentRun(id=run, position_id='synthetic-position', minknow_run_id=run, state='completed', observed_at=now,
                observed_generation=1, output_directories={'reads': str(work)}, output_files={}, last_minknow_payload={'acceptance_fixture': NOTE}))
            await s.flush()
            s.add(OntInstrumentRunEvent(id=run+'-event', run_id=run, event_type='completed', state='completed', observed_at=now, observed_generation=1, output_files={}))
            s.add(OntRawSignalRepresentation(id=rawid, run_id=run, observed_generation=1, role='derived', source_kind='pod5_conversion' if case == 'raw' else 'imported_blow5',
                format='blow5', source_fidelity='lossless_signal', state='ready', reason_code='retained_native_control', artifact_manifest=manifest,
                manifest_sha256=digest(manifest), parent_representation_ids=[], parent_manifest_sha256s=[], compression={}, runtime_identity={'acceptance_fixture': NOTE},
                validation_receipts={'adjacent_index': True, 'acceptance_fixture': NOTE},
                read_count=load(work/'raw-verification.json')['blow5']['reads'] if case=='raw' else len((work/'inventory.txt').read_text().splitlines()),
                published_at=now, retention_pinned_at=now))
            await s.flush()
            if case == 'raw':
                waveform = load(work / 'waveform.json')
                assert sha(blow) == waveform['source_identity']['blow5']['sha256']
                assert sha(Path(str(blow)+'.idx')) == waveform['source_identity']['index']['sha256']
                s.add(OntRawSignalLookup(id='signal-waveform', run_id=run, observed_generation=1, representation_id=rawid,
                    read_id=waveform['read_id'], state='ready', reason_code='retained_native_fd_lookup', sample_count=waveform['sample_count'],
                    samples=waveform['samples'], receipt=waveform, completed_at=now))
                s.add(OntSignalViewerSession(id='signal-viewer-raw', dataset_id='synthetic-retained-raw', run_id=run, observed_generation=1,
                    raw_representation_id=rawid, selected_read_id=waveform['read_id'], igv_state=igv,
                    signal_state={'mode': 'raw_waveform', 'render_params': render, 'view_job_id': None, 'read_mapping_job_id': None, 'reference_mapping_job_id': None}))
                continue
            rid = (work / 'inventory.txt').read_text().strip()
            moves = load(work / 'moves.json') if (work / 'moves.json').exists() else {}
            model = moves.get('basecall_model_id', 'legacy_unknown_missing_RG')
            mid, pid, cal = [f'signal-{kind}-{case}' for kind in ['moves', 'profile', 'calibration']]
            s.add(Job(id='signal-job-'+case, name=NOTE, model_id='nanopore', mode='basecall', status='completed', params={}, output_dir=str(work), provenance={'acceptance_fixture': NOTE}))
            s.add(InputFile(id='signal-input-'+case, filename='filtered.bam', file_type='bam', directory=str(work), size_bytes=(work/'filtered.bam').stat().st_size))
            await s.flush()
            s.add(OntMoveTableSource(id=mid, run_id=run, observed_generation=1, raw_representation_id=rawid, input_file_id='signal-input-'+case,
                source_job_id='signal-job-'+case, artifact_sha256=sha(work/'filtered.bam'), artifact_size_bytes=(work/'filtered.bam').stat().st_size,
                basecall_model_id=model, molecule_type=case, source_runtime_identity={'acceptance_fixture': NOTE},
                read_inventory_sha256=sha(work/'inventory.txt'), validation_state='ready' if moves else 'failed', reason_code='retained_native_validation' if moves else 'legacy_missing_RG_not_admitted',
                validation_receipt={**moves, 'acceptance_fixture': NOTE}, record_count=1, unique_read_count=1))
            await s.flush()
            # Explicit synthetic profile metadata for the direct native k=1,m=0 control.
            # Never presented as a genuine calibration or used to admit a new job.
            s.add(OntSignalCalibrationArtifact(id=cal, raw_representation_id=rawid, move_source_id=mid, basecall_model_id=model,
                sample_selection={'acceptance_fixture': NOTE}, recommended_kmer_length=1, recommended_signal_move_offset=0,
                score_evidence={'acceptance_fixture': NOTE}, runtime_identity={}, parent_sha256s={}, artifact_sha256=digest({'case':case,'note':NOTE})))
            await s.flush()
            s.add(OntSignalMappingProfile(id=pid, name=NOTE, molecule_type=case, basecall_model_id=model, kmer_length=1,
                signal_move_offset=0, parameter_source='approved_calibration', calibration_artifact_id=cal, approval_receipt={'acceptance_fixture':NOTE, 'base_shift_value':0}, approved_at=now, approved_by='synthetic-fixture'))
            await s.flush()
            for mode, name, filename, kind in [('signal_to_read','read','reform.paf','reform_paf'),('signal_to_reference','reference','realigned.paf.gz','realign_paf')]:
                mapping = f'signal-{name}-{case}'
                s.add(OntSignalMappingJob(id=mapping, mode=mode, run_id=run, observed_generation=1, raw_representation_id=rawid,
                    move_source_id=mid, mapping_profile_id=pid, reference_revision_id='own-read-'+case if name=='reference' else None,
                    parent_mapping_job_id=f'signal-read-{case}' if name=='reference' else None,
                    state='ready', reason_code='retained_native_identity_control', request_fingerprint=digest(mapping), stage_receipts={'retained_native': load(work/('reform.json' if name=='read' else 'realign.json'))}))
                await s.flush()
                p=work/filename
                s.add(OntSignalMappingArtifact(id=mapping+'-artifact', mapping_job_id=mapping, kind=kind,
                    managed_relative_path=str(p.relative_to(ROOT/'results/ont_signal_workbench')), media_type='application/octet-stream',
                    sha256=sha(p), size_bytes=p.stat().st_size, parent_identities={'acceptance_fixture':NOTE}, runtime_identity={}, validation_receipt=load(work/('reform.json' if name=='read' else 'realign.json'))))
                await s.flush()
            html = next((work/'render').glob('*.html'))
            html_descriptor = {'artifact_id':'signal-html-'+case, 'media_type':'text/html', 'managed_relative_path':str(html.relative_to(ROOT/'results/ont_signal_workbench')), 'sha256':sha(html), 'size_bytes':html.stat().st_size}
            s.add(OntSquigualiserViewJob(id='signal-render-'+case, mapping_artifact_id=f'signal-read-{case}-artifact', mode='read', read_id=rid,
                render_params=render, request_fingerprint=digest(html_descriptor), state='ready', reason_code='retained_native_render', output_manifest={'artifacts':[html_descriptor]}, render_receipt=load(work/'render.json')))
            comp_render = ont_signal_workbench.ComparisonRenderParams(base_limit=100,signal_sample_limit=10000).model_dump(mode='json')
            effective = service.compile_ideal_comparison_settings({'profile_id':case+'-r9-min','seed':1},comp_render)
            from types import SimpleNamespace
            compatibility = service._derive_ideal_comparison_compatibility(simulated_profile=effective['profile'], mapping_profile=SimpleNamespace(molecule_type=case, basecall_model_id=model), move_source=SimpleNamespace(basecall_model_id=model),raw_header={},run_receipt={})
            effective.update(compatibility_disposition=compatibility['disposition'],compatibility_evidence=compatibility)
            preview = digest({'native_control':case})
            settings={'simulation_settings':{'profile_id':case+'-r9-min','seed':1},'render_params':comp_render}
            base_state={'mode':'read','render_params':render,'view_job_id':'signal-render-'+case,'read_mapping_job_id':'signal-read-'+case,'reference_mapping_job_id':None}
            for suffix, state in [('',base_state),('-comparison',{**base_state,'mode':'ideal_comparison','view_job_id':None,'reference_mapping_job_id':'signal-reference-'+case,'comparison_job_id':'signal-comparison-'+case,'comparison_preview_digest':preview,'comparison_settings':settings,'comparison_review_id':None})]:
                s.add(OntSignalViewerSession(id='signal-viewer-'+case+suffix,dataset_id='synthetic-retained-'+case,run_id=run,observed_generation=1,
                    raw_representation_id=rawid,move_source_id=mid,mapping_profile_id=pid,reference_revision_id='own-read-'+case,
                    selected_read_id=rid,contig=rid,locus_start=1,locus_end=100,igv_state=igv,signal_state=state))
            await s.flush()
            native_manifest=load(work/'comparison/comparison_manifest.json')
            s.add(OntSignalComparisonJob(id='signal-comparison-'+case,viewer_session_id='signal-viewer-'+case+'-comparison',viewer_session_revision=1,
                run_id=run,observed_generation=1,raw_representation_id=rawid,mapping_artifact_id=f'signal-reference-{case}-artifact',reference_revision_id='own-read-'+case,
                selected_read_id=rid,reference_contig=rid,reference_start=1,reference_end=100,simulation_orientation='forward',simulation_settings=effective,
                render_params=comp_render,preview_digest=preview,request_fingerprint=preview,state='ready',reason_code='retained_native_control',
                generated_read_id=native_manifest['renderer']['generated_read_id'],output_manifest={'producer':native_manifest['producer'],'renderer':native_manifest['renderer']}))
            await s.flush()
            parents=native_manifest['parents']
            html=work/'comparison/comparison.html'
            s.add(OntSignalComparisonArtifact(id='signal-comparison-html-'+case,comparison_job_id='signal-comparison-'+case,kind='comparison_html',authority_class='comparison_derived',
                managed_relative_path=str(html.relative_to(ROOT/'results/ont_signal_workbench')),media_type='text/html',sha256=sha(html),size_bytes=html.stat().st_size,
                parent_identities={'reference_fasta_sha256':parents['reference_fasta_sha256'],'mapping_sha256':parents['real_mapping_sha256'],
                    'mapping_index_sha256':parents['real_mapping_index_sha256'],'real_blow5':{'routing_sha256':None,'blow5':[{'sha256':parents['real_blow5_sha256'],'index_sha256':parents['real_blow5_index_sha256']}]},
                    'real_moves_sha256':parents['real_moves_sha256'],'raw_manifest_sha256':digest(manifest),'run_id':run,'observed_generation':1,'selected_read_id':rid},
                validation_receipt=native_manifest['renderer']))
        await s.commit()

if __name__ == '__main__':
    asyncio.run(seed())
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=18761)
