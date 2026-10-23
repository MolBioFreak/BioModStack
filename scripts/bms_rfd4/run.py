"""One-process official generation/evaluation adapter; no sampler or CLI shell."""
from __future__ import annotations
import argparse
import importlib.metadata
import json
from pathlib import Path
import os
import traceback
from lib.rfd4_proteina_contract import normalize_request, native_overlay, request_identity, selected_dependencies, SOURCE_REVISION
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
    def apply(node,values,path=()):
        for k,v in values.items():
            # Guidance metrics are an explicit selected-name table, not partial
            # constructor overrides. An empty list must clear inherited metrics.
            replace_table=k in native_tables or (*path,k)==('guidance','metrics')
            if isinstance(v,dict) and not replace_table and k in node and OmegaConf.is_dict(node[k]) and not (
                '_target_' in v and node[k].get('_target_')!=v['_target_']):apply(node[k],v,(*path,k))
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


def write_evaluation_tasks(request, identity, destination):
    """Compile typed slot labels into native task_csv using retained exact paths."""
    import csv
    if 'evaluation_tasks' not in request:return None
    paths={row['slot']:row['native'] for row in identity if 'slot' in row}
    path=Path(destination)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['designed_structure_path','task'])
        writer.writeheader()
        for row in request['evaluation_tasks']:
            writer.writerow({'designed_structure_path':paths[row['slot']],'task':row['task']})
    return path.resolve()


def oracle_environment(request, runtime_bindings):
    """Select only the requested official oracle family, through system-owned paths.

    OF3 uses AF3Oracle and the official converted-weight provenance reader; BMS
    does not convert weights or emulate its inference configuration.
    """
    deps=selected_dependencies(request)
    active=set(deps['active_oracle_profiles'])
    selected={profile for profile in active if profile=='af2'}
    for profile in active.intersection({'af3','of3'}):
        selected.add(request.get('oracle_parameter_family',profile))
    result={}
    for family in selected:
        if family not in runtime_bindings:
            if request.get('oracle_parameter_family')==family:
                raise ValueError(f'Explicit {family.upper()} oracle requires its selected runtime path binding')
            continue  # Preserve official environment defaults when not explicitly rebound.
        prefix='AF2' if family=='af2' else 'AF3'
        for suffix in ('IMAGE','JAX_CACHE_DIR','MODEL_DIR'):
            key=prefix+'_'+suffix
            result[key]=str(runtime_bindings[family][key])
    return result


def run(request,bindings,output,*,bundle=None,runtime_identity=None,prepared_inputs=None,config_dir=None,oracle_runtime=None):
    import rfproteina
    from omegaconf import OmegaConf
    request=normalize_request(request)
    root=Path(output).resolve(); root.mkdir(parents=True,exist_ok=True)
    observer=NativeObserver()
    receipt={'request_identity':request_identity(request),'reviewed_source_revision':SOURCE_REVISION,
             'runtime_identity':runtime_identity or {},'execution_status':'running',
             'native_package_version':importlib.metadata.version('rfd4-proteina')}
    cfg=None
    previous_environment={}
    try:
        environment=oracle_environment(request,oracle_runtime or {})
        previous_environment={key:os.environ.get(key) for key in environment}
        os.environ.update(environment)
        receipt['oracle_environment']=environment
        if prepared_inputs is None:
            inputs,identity=prepare_inputs(request,bindings,root/'prepared')
            receipt['input_identity']=identity
        else:
            inputs=Path(prepared_inputs).resolve()
            identity=json.loads((inputs.parent/'input_identity.json').read_text()) if 'evaluation_tasks' in request else []
        if config_dir is None:
            import rootutils
            config_dir=rootutils.find_root(rfproteina.__file__,indicator='.project-root')/'configs'
        cfg=compose_native(request,config_dir,inputs=inputs,output=root/'native',bundle=bundle,bindings=bindings)
        if request['mode']=='evaluate':
            task_csv=write_evaluation_tasks(request,identity,root/'prepared/task_mapping.csv')
            if task_csv is not None:cfg.task_csv=str(task_csv)
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
        for key,value in previous_environment.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
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
        runtime_identity=payload.get('runtime_identity'),prepared_inputs=args.prepared_inputs,
        oracle_runtime=payload.get('oracle_runtime'))

if __name__=='__main__':main()
