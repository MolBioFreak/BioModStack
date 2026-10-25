"""Read-only observation at pinned native return/writer seams and BMS index.

No sampling/writer replacement. In particular final membership comes from actual
BeamSearchStrategy.generate return locals, never from a '_bsfinal_' filename.
"""
from __future__ import annotations
import contextlib
import csv
import hashlib
import json
from pathlib import Path
import sys
import math
import os


def scalar_fields(result):
    fields=('example_id','orig_batch_idx','reward','search_checkpoint','search_seed','is_intermediate','raw_batch_id')
    return {k:({'value':None,'native_nonfinite':str(result[k])} if isinstance(result[k],float) and not math.isfinite(result[k]) else result[k])
            for k in fields if k in result and isinstance(result[k],(str,int,float,bool,type(None)))}


class NativeObserver:
    def __init__(self):
        self.events=[]
        self.membership={}
        self.errors=[]

    def observe(self,frame,event,value):
        if event!='return':return
        file=frame.f_code.co_filename.replace('\\','/')
        name=frame.f_code.co_name
        loc=frame.f_locals
        try:
            caller = getattr(frame, 'f_back', None)
            caller_file = caller.f_code.co_filename.replace('\\\\', '/') if caller else ''
            caller_name = caller.f_code.co_name if caller else ''
            if file.endswith('/torch/serialization.py') and name == 'save' and caller_file.endswith('/rfproteina/search/beam_search.py') and caller_name == 'generate':
                native = caller.f_locals
                rank, count = native['rank'], native['nsamples']
                indices = native['select_indices'][rank * count:(rank + 1) * count].tolist()
                self.events.append({'event': 'native_relation', 'role': 'sampling_state',
                    'path': str(native['path']), 'producer_field': 'beam_search.generate:torch.save',
                    'native_example_ids': [native['checkpoint_design_ids'][int(i)] for i in indices],
                    'input_ids': list(native['batch_ids']), 'step': native['end_step'], 'beam_rank': rank,
                    'scope': 'checkpoint_batch'})
            elif file.endswith('/atomworks/io/utils/io_utils.py') and name == 'to_cif_file' and caller_file.endswith('/rfproteina/metrics/utils/metrics_utils.py') and caller_name in {'_write_input_cif', '_write_trajectories_if_present'}:
                native = caller.f_locals
                self.events.append({'event': 'native_relation',
                    'role': 'input' if caller_name == '_write_input_cif' else 'trajectory',
                    'path': str(loc['path']), 'example_id': native['example_id'],
                    'producer_field': caller_name, 'trajectory_key': native.get('traj_key')})
            elif file.endswith('/biotite/structure/io/trajfile.py') and name == 'write' and caller_file.endswith('/rfproteina/metrics/utils/metrics_utils.py') and caller_name == '_write_trajectories_if_present':
                native = caller.f_locals
                self.events.append({'event': 'native_relation', 'role': 'trajectory',
                    'path': str(loc['file_name']), 'example_id': native['example_id'],
                    'producer_field': '_write_trajectories_if_present:XTCFile.write',
                    'trajectory_key': native.get('traj_key'), 'format': 'xtc',
                    'time_semantics': 'native_step_labels_not_physical_time'})
            elif file.endswith('/rfproteina/search/beam_search.py') and name=='generate' and isinstance(value,list):
                # Empty/early-stop path has no final_results and is valid-empty final beam.
                finals={id(x) for x in loc.get('final_results',[])}
                for result in value:
                    eid=result.get('example_id')
                    role='final_beam' if id(result) in finals else 'completed_rollout'
                    self.membership[eid]=role
                    self.events.append({'event':'search_return','population':role,**scalar_fields(result)})
                self.events.append({'event':'final_population','count':len(finals),'observed':True})
            elif file.endswith('/rfproteina/search/beam_search.py') and name=='_simulate_and_compute_reward' and isinstance(value,tuple):
                for result in value[1]:
                    self.events.append({'event':'search_scoring','population':'completed_rollout' if loc.get('rollout',True) else 'ranking_intermediate',
                        'checkpoint':loc.get('current_step'),**scalar_fields(result)})
            elif file.endswith('/rfproteina/nn/bioir_full.py') and name=='predict_step' and isinstance(value,list):
                for result in value:
                    role='completed_rollout' if result.get('is_intermediate',False) else ('final_beam' if loc['self'].search is not None else 'generated')
                    self.membership[result.get('example_id')]=role
                    self.events.append({'event':'bioir_return','population':role,**scalar_fields(result)})
            elif file.endswith('/rfproteina/proteina.py') and name=='_build_staged_payload' and isinstance(value,dict):
                sample=loc['sample']; eid=value['example_id']
                a=sample['generated_atom_array']
                # Preserve modified tokens, chains and exact native positions; no lossy alphabet filter.
                positions=[]; last=None
                for i in range(len(a)):
                    key=(str(a.chain_id[i]),int(a.res_id[i]),str(a.ins_code[i]) if hasattr(a,'ins_code') else '')
                    if key!=last:
                        positions.append({'chain_id':key[0],'res_id':key[1],'ins_code':key[2],'component_id':str(a.res_name[i]),
                                          'chain_type':int(a.chain_type[i]) if hasattr(a,'chain_type') else None})
                        last=key
                sequences=[]
                from atomworks.enums import ChainType
                from atomworks.io.utils.sequence import convert_to_one_letter_sequences
                for chain in dict.fromkeys(p['chain_id'] for p in positions):
                    residues=[p for p in positions if p['chain_id']==chain]
                    chain_type=residues[0]['chain_type']
                    if chain_type is None:continue
                    kind=ChainType(chain_type)
                    if not (kind.is_protein() or kind.is_nucleic_acid()):continue
                    sequence,canonical=convert_to_one_letter_sequences([p['component_id'] for p in residues],kind)
                    sequences.append({'chain_id':chain,'chain_type':kind.name,'sequence':sequence,
                                      'canonical_sequence':canonical,'positions':residues})
                self.events.append({'event':'native_writer','example_id':eid,'native_example_id':sample.get('example_id'),
                    'population':self.membership.get(sample.get('example_id'),'generated'),
                    'path':value['generated_output_path'],'dataset_name':value.get('dataset_name'),'task':value.get('task'),
                    'positions':positions,'sequences':sequences,**{k:v for k,v in scalar_fields(sample).items() if k!='example_id'}})
        except Exception as exc:
            # Observation failure does not become a new execution gate or fake zero.
            self.errors.append(f'{name}: {type(exc).__name__}: {exc}')

    @contextlib.contextmanager
    def installed(self):
        previous=sys.getprofile()
        def profile(frame,event,arg):
            if previous is not None:previous(frame,event,arg)
            self.observe(frame,event,arg)
        sys.setprofile(profile)
        try:yield self
        finally:sys.setprofile(previous)


def native_path_relations(root, metrics, producer_root=None):
    """Pinned clean_metrics metadata fields, not basename/candidate guesses.

    rfproteina.proteina.clean_metrics preserves input paths and oracle-qualified
    refold/confidence paths. Keep external/missing references as observations.
    """
    relations = []
    for field, value in metrics.items():
        role = ('input' if field in ('input_cif_path', 'input_json_path') else
                'refold' if field.endswith('refolded_structure_path') else
                'refold_confidence' if field.endswith('confidence_json_path') else None)
        if role is None or not isinstance(value, str) or not value:
            continue
        path = Path(value)
        path = path if path.is_absolute() else root / path
        # Lexical containment preserves the actual producer document identity;
        # custody registration separately verifies any retained symlink bytes.
        try:
            relative = path.relative_to(producer_root or root).as_posix()
            if '..' in Path(relative).parts:
                raise ValueError('outside publication')
        except ValueError:
            relative = None
        relations.append({'role': role, 'producer_field': field, 'native_path': value,
                          'document': relative,
                          'availability': 'stored' if relative is not None and (root / relative).is_file() else 'not_retained'})
    return relations


def publish(root,request,receipt,observer,*,producer_root=None):
    root=Path(root).resolve()
    # Native evaluation writes absolute links into its own retained inputs. Make
    # those same links portable, without rewriting or copying the native CIF.
    for p in root.rglob('*'):
        if p.is_symlink():
            target=p.resolve()
            if target.is_relative_to(root):
                relative=os.path.relpath(target,p.parent)
                p.unlink();p.symlink_to(relative)
    records=[]
    for event in observer.events:
        if event['event']!='native_writer':continue
        record=dict(event)
        original_path=Path(record.pop('path'))
        record['document']=str(original_path.relative_to(producer_root or root))
        p=root / record['document']
        record['emitted']=p.is_file()
        record['availability']='stored' if p.is_file() else 'not_retained'
        records.append(record)
    # Evaluation has native exact IDs/paths in its CSV, not a generated-design fiction.
    if request['mode']=='evaluate':
        for p in sorted(root.glob('native/evaluation_metrics/per_example_metrics*.csv')):
            with p.open(newline='') as f:
                for row in csv.DictReader(f):
                    path=Path(row['designed_structure_path'])
                    relative=path.relative_to(producer_root or root)
                    records.append({'example_id':row['example_id'],'population':'evaluated','document':str(relative),
                                    'emitted':(root / relative).is_file(),'availability':'stored' if (root / relative).is_file() else 'missing','metrics':row})
    metrics_by_id={}
    for p in sorted(root.glob('native/inference_metrics/**/per_example_metrics*.csv')):
        with p.open(newline='') as f:
            for row in csv.DictReader(f):
                if row.get('example_id'):
                    metrics_by_id.setdefault(row['example_id'],[]).append(row)
    for record in records:
        candidates = metrics_by_id.get(record['example_id'], [])
        # Native design keys include dataset/task. Never merge independent rows
        # sharing an example_id or let filesystem ordering choose the winner.
        candidates = [row for row in candidates if all(
            not record.get(key) or row.get(key) == record[key]
            for key in ('dataset_name', 'task'))]
        if len(candidates) == 1:
            record['metrics'] = candidates[0]
        elif candidates:
            record['metric_association'] = {'state': 'ambiguous', 'native_rows': candidates}
        record['relations'] = native_path_relations(root, record.get('metrics', {}), producer_root)
        from .metric_definitions import descriptors
        record['metric_descriptors'] = descriptors(record.get('metrics', {}))
        for event in observer.events:
            if event.get('event') != 'native_relation':
                continue
            exact = event.get('example_id') == record['example_id'] or (
                record.get('native_example_id') is not None and
                record['native_example_id'] in event.get('native_example_ids', []))
            if exact:
                path = Path(event['path'])
                try:
                    relative = path.relative_to(producer_root or root).as_posix()
                except ValueError:
                    relative = None
                record['relations'].append({**event, 'document': relative,
                    'availability': 'stored' if relative is not None and (root / relative).is_file() else 'not_retained'})
        record['relation_observation'] = {role: ('observed' if any(item['role'] == role for item in record['relations']) else 'not_observed_in_publication')
            for role in ('input', 'refold', 'trajectory', 'sampling_state')}
    artifacts=[]
    for p in sorted(root.rglob('*')):
        if not p.is_file() or p.name=='manifest.json':continue
        relative=str(p.relative_to(root))
        role=('metrics' if '/inference_metrics/' in '/'+relative or '/evaluation_metrics/' in '/'+relative else
              'sampling_state' if '/sampling_states/' in '/'+relative else
              'input' if relative.startswith('prepared/') else 'native_artifact')
        relations = [event for event in observer.events if event.get('event') == 'native_relation'
                     and Path(event['path']) == Path(producer_root or root) / relative]
        artifacts.append({'path':relative,'role':relations[0]['role'] if relations else role,
                          'relations':relations, 'size_bytes':p.stat().st_size})
    document={'contract':'rfd4_proteina_v1','producer':'BMS','model_id':'rfd4_proteina','mode':request['mode'],
              'producer_root':str(producer_root or root),
              'request':request,'receipt':receipt,'records':records,'artifacts':artifacts,'events':observer.events,
              'observation_errors':observer.errors,'population_completeness':('partial' if observer.errors else
                  'observed_native_returns' if observer.events or records else 'unobserved'),
              'execution_status':receipt['execution_status']}
    (root/'manifest.json').write_text(json.dumps(document,indent=2,allow_nan=False)+'\n')
    return document
