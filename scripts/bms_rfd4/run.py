"""One-process official generation/evaluation adapter; no sampler or CLI shell."""
from __future__ import annotations
import argparse
import importlib.metadata
import json
from pathlib import Path
import os
import traceback
from lib.rfd4_proteina_contract import normalize_request, native_overlay, request_identity, SOURCE_REVISION
from bms_rfd4.inputs import prepare_inputs
from bms_rfd4.publication import NativeObserver, publish


def compose_native(request, config_dir, *, inputs, output, bundle=None, bindings=None):
    """Native Hydra owns group inheritance; explicit leaves are applied last."""
    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf, open_dict
    request=normalize_request(request)
    groups=[]
    for group,name in request.get('groups',{}).items():
        groups.append(('+' if group=='guidance' else '')+group+'='+name)
    for section,selection in request.get('providers',{}).items():
        groups.append('metrics/'+section+'='+selection['profile'])
    with initialize_config_dir(config_dir=str(Path(config_dir).resolve()),version_base='1.3'):
        cfg=compose(config_name='inference' if request['mode']=='generate' else 'evaluate',overrides=groups)
    native_tables={'checkpoints','rewards','save_criteria','continuation_criteria','metric_thresholds',
                   'frac_under_thresholds','input_kwarg_names','term_cutoffs','terms'}
    def apply(node,values):
        for k,v in values.items():
            if isinstance(v,dict) and k not in native_tables and k in node and OmegaConf.is_dict(node[k]) and not (
                '_target_' in v and node[k].get('_target_')!=v['_target_']):apply(node[k],v)
            else:node[k]=v
    with open_dict(cfg):
        apply(cfg,native_overlay(request,bindings=bindings))
        cfg.inputs=str(Path(inputs).resolve())
        cfg.out_dir=str(Path(output).resolve())
        if request['mode']=='generate':
            cfg.hf_ckpt_path=str(Path(bundle).resolve())
            cfg.hf_ckpt_revision=None  # local single paired layout, not a second HF download
            for slot,key in [('resume_slot','search.resume_from'),('adapter_slot','adapter_path')]:
                if request.get(slot) is not None:OmegaConf.update(cfg,key,str(Path((bindings or {})[request[slot]]).resolve()),force_add=True)
    return cfg


def run(request,bindings,output,*,bundle=None,runtime_identity=None,prepared_inputs=None,config_dir=None):
    import rfproteina
    from omegaconf import OmegaConf
    request=normalize_request(request)
    root=Path(output).resolve(); root.mkdir(parents=True,exist_ok=True)
    observer=NativeObserver()
    receipt={'request_identity':request_identity(request),'reviewed_source_revision':SOURCE_REVISION,
             'runtime_identity':runtime_identity or {},'execution_status':'running',
             'native_package_version':importlib.metadata.version('rfd4-proteina')}
    cfg=None
    try:
        if prepared_inputs is None:
            inputs,identity=prepare_inputs(request,bindings,root/'prepared')
            receipt['input_identity']=identity
        else:
            inputs=Path(prepared_inputs).resolve()
        if config_dir is None:
            import rootutils
            config_dir=rootutils.find_root(rfproteina.__file__,indicator='.project-root')/'configs'
        cfg=compose_native(request,config_dir,inputs=inputs,output=root/'native',bundle=bundle,bindings=bindings)
        (root/'requested.json').write_text(json.dumps(request,indent=2)+'\n')
        with observer.installed():
            if request['mode']=='generate':
                from rfproteina.generate import run_generation
                run_generation(cfg,job_id=0)
            else:
                from rfproteina.evaluate import run_evaluate
                run_evaluate(cfg)
        receipt['execution_status']='completed'
    except BaseException as exc:
        receipt.update(execution_status='failed',error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        if cfg is not None:
            # Includes the seed resolved/mutated by run_generation, even metrics-off.
            try:effective=OmegaConf.to_container(cfg,resolve=True)
            except Exception as exc:
                effective=OmegaConf.to_container(cfg,resolve=False)
                receipt['config_resolution_error']=str(exc)
            (root/'effective_config.json').write_text(json.dumps(effective,indent=2)+'\n')
            receipt['effective_config']='effective_config.json'
            receipt['resolved_seed']=effective.get('seed')
        (root/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
        publish(root,request,receipt,observer)
    return receipt


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--request',required=True)
    parser.add_argument('--bindings',required=True)
    parser.add_argument('--bundle')
    parser.add_argument('--output',required=True)
    parser.add_argument('--prepared-inputs')
    args=parser.parse_args()
    payload=json.loads(Path(args.request).read_text())
    base=Path(args.bindings).resolve().parent
    bindings={slot:str((base/path).resolve()) if not Path(path).is_absolute() else path
              for slot,path in json.loads(Path(args.bindings).read_text()).items()}
    run(payload.get('request',payload),bindings,args.output,bundle=args.bundle,
        runtime_identity=payload.get('runtime_identity'),prepared_inputs=args.prepared_inputs)

if __name__=='__main__':main()
