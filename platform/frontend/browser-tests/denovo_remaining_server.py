"""Real scratch routers plus inert native producers for remaining journeys."""
import asyncio
import os
from pathlib import Path
os.environ['BMS_FINAL_ALLOW_RESEQUENCE_PREPARE']='1'
import denovo_final_native_server as native
from database import Job, ExecutionTarget, async_session
from component_runtime import ComponentRuntime
from scripts.open_stage_gate import open_component_gate, component_checkpoint_projection

async def seed_remaining():
    await native.seed()
    root=native.ROOT
    if (root/'remaining-seeded').exists(): return
    # A native stage-checkpoint producer, not the retired local-redesign product.
    candidates=root/'results/checkpoint/candidates'; candidates.mkdir(parents=True)
    from test_local_gate_review_rows import PDB
    (candidates/'one.pdb').write_text(PDB)
    runtime=ComponentRuntime(root/'checkpoint-ledger.sqlite',artifact_root=root/'results/checkpoint',attempt_id='inert',root_job_id='checkpoint',target_id='inert-worker',lease_id='inert',source_identity={'revision':'a'*40,'tree':'b'*40})
    open_component_gate(runtime,job_id='checkpoint',stage='post_sequence_design',payload={'label':'NOT INFERENCE'},directories={'candidate':candidates})
    checkpoint=component_checkpoint_projection(runtime)[0]
    import shutil, json
    from component_runtime import ResultReference
    retained=root/'results/checkpoint/.bms-review-import'/checkpoint['checkpoint_sha256']
    for item in checkpoint['artifacts']:
        ref=ResultReference(**item); destination=retained/ref.relative_path
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ref.resolve(runtime.artifact_root),destination)
    (retained/'checkpoint-review.json').write_text(json.dumps(checkpoint))
    async with async_session() as session:
        session.add(ExecutionTarget(id='inert-worker',provider='vast',provider_instance_id='NOT-INFERENCE',host='fixture.invalid',port=22,username='root',remote_root='/inert',name='NOT INFERENCE worker',active=True,state='ready',capabilities={}))
        session.add(Job(id='checkpoint',name='NOT INFERENCE checkpoint',model_id='rfd4_proteina',mode='generate',status='awaiting_input',queue_status='completed',awaiting_input=True,awaiting_stage=checkpoint['stage'],awaiting_payload={'component_checkpoint':checkpoint},execution_target_id='inert-worker',remote_attempt_id='inert',output_dir=str(root/'results/checkpoint'),params={},provenance={},decision_history=[]))
        session.add(Job(id='resequence-clone',name='NOT INFERENCE Resequence Clone',model_id='protein_modification_experimental',mode='region_redesign',status='completed',queue_status='completed',params={'modification_mode':'resequence','seq_method':'mpnn','structure_validators':['protenix_v2'],'seed':0,'seqs_per_design':3,'mpnn_backbone_noise':0,'protenix_use_msa':False,'design_chains':'A','native_sources':{'input':{'path':'bms_results/checkpoint/candidates/one.pdb','output_format':'native'}},'backbone_input_pdbs':str(candidates)},execution_target_id=None,provenance={},decision_history=[]))
        await session.commit()
    (root/'remaining-seeded').write_text('NOT INFERENCE')

if __name__=='__main__':
    asyncio.run(seed_remaining())
    import uvicorn
    uvicorn.run(native.app,host='127.0.0.1',port=18871)
