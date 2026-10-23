"""Model-owned RFD4 scientific request; no model imports or filesystem mutation.

Explicit leaves overlay official Hydra groups *after* composition. Omitted leaves
inherit their selected native group, not defaults from an alternative preset.
"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path

MODEL_ID = 'rfd4_proteina'
CONTRACT = 'rfd4_request_v1'
RESULT_CONTRACT = 'rfd4_proteina_v1'
SOURCE_REVISION = '8f7f702e46f944713fbe8ff5c566221aaa54eb0b'
HF_REVISION = '48377e45eb25a4fce8d2aff56c1a659b726d2106'
_PROFILE = json.loads((Path(__file__).parents[1]/'bms_rfd4/schema_profile.json').read_text())

def obj(props, required=()):
    return {'type':'object','additionalProperties':False,'properties':props,'required':list(required)}

def array(items): return {'type':'array','items':items,'x-ui-control':'table'}
def choice(*values):return {'type':'string','enum':list(values),'x-ui-control':'select'}
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def table(item):return {'type':'object','additionalProperties':item,'x-ui-control':'table'}
S={'type':'string'}
N={'type':'number'}
I={'type':'integer'}
B={'type':'boolean'}
# Official PassCriterionMetric consumes both inclusive and strict bounds;
# None explicitly clears a bound just as in its native thresholds.get() path.
THRESHOLD=obj({**{key:nullable(N) for key in ('min','max','min_exclusive','max_exclusive')},
               'min_of':array(S),'max_of':array(S),'mean_of':array(S)})
REWARD=obj({'weight':{**N,'default':1.0},'normalize_by':{**N,'default':1.0},'clip':obj({'min':N,'max':N})})
CHECKPOINT=obj({'metrics':array(S),'rewards':table(REWARD),'n_branch':I,'use_clean_prediction':B,'continuation_criteria':table(THRESHOLD)})
SELECTION=obj({'select':{**S,'x-ui-control':'region'},'group':S},['select'])
CONDITIONS=obj({name:table(array(SELECTION)) for name in ('C_CRD C_SEQ C_IDX C_HOT C_RAS C_HB C_PISTACK C_PICATION C_SALT C_HPHOB C_MCOORD C_SEC C_CATH C_CENTROID C_CRDGRP C_NA_SS_STATE'.split()+['C_SEQ_PROB_'+t for t in 'ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL A C G U DA DC DG DT'.split()])})
SOURCE=obj({'slot':{**S,'x-ui-control':'file'},'input_as_annotated_cif':{**B,'default':False}} ,['slot'])
EXAMPLE=obj({'name':S,'sources':array(SOURCE),'contig':S,'conditions':CONDITIONS,
    'pair_conditions':array(obj({'left':S,'right':S,'value':choice('UNSPECIFIED','PAIR','LOOP')},['left','right','value']))},['name','sources'])
CHAIN=obj({'chain_id':S,'min_length':{**I,'minimum':1},'max_length':{**I,'minimum':1}},['chain_id','min_length','max_length'])
INPUT={'oneOf':[
    obj({'kind':{'const':'unconditional'},'name':S,'chains':{**array(CHAIN),'minItems':1}},['kind','name','chains']),
    obj({'kind':{'const':'condition_spec'},'examples':{**array(EXAMPLE),'minItems':1},'global_args':obj({'input_as_annotated_cif':B,'contig':S,'conditions':CONDITIONS})},['kind','examples']),
    obj({'kind':{'const':'annotated'},'slots':{**array(S),'minItems':1}},['kind','slots'])]}

def _settings(mode):
    s=copy.deepcopy(_PROFILE['settings'])
    p=s['properties']
    p['generation']['properties']['n_recycle_schedule']['default']=copy.deepcopy(_PROFILE['default_effective_generation']['n_recycle_schedule'])
    p['generation']['properties']['n_recycle_schedule']['x-default-applicability']='omitted groups: inference_base + thinking_medium'
    p['subset_to_examples']={'anyOf':[S,array(S),{'type':'null'}],'x-ui-control':'table','default':None}
    p['search']=obj({'checkpoints':{**nullable(table(CHECKPOINT)),'default':None},'beam_width':{**I,'default':4},'n_branch':{**I,'default':4},
        'save_rollouts':{**B,'default':False},'save_criteria':{**nullable(table(THRESHOLD)),'default':None},
        'save_states':{**B,'default':False},'resume_seed':{**nullable(I),'default':None}})
    p['generation']['properties']['dataset']=obj({'transform':obj({
        'path_length_search_cutoff':nullable(I),'residue_names_supporting_local_latents':nullable(array(S)),
        'precompute_atom_permutation':B})})
    for modality in ('bb_ca','local_latents'):
        mp=p['generation']['properties']['model']['properties'][modality]['properties']
        mp['schedule']['properties']['mode']['enum']=['uniform','power','log']
        mp['gt']['properties']['mode']['enum']=['1-t/t','tan','1/t']
        mp['simulation_step_params']['properties']['sampling_mode']['enum']=['vf','sc','vf_tsr']
        mp['simulation_step_params']['properties']['sc_scale_noise']['minimum']=0
    if mode=='evaluate':
        s=obj({k:p[k] for k in ('verbose','compress_outputs','metrics_tags','delete_failing_pre_oracle')})
        s['properties']['verbose']['default']=True
        s['properties']['delete_failing_pre_oracle']['default']=False
    return s

TRANSFORM={'oneOf':[
    obj({'kind':{'const':'apo'},'save_id':S},['kind']),
    obj({'kind':{'const':'crop'},'crop_size':I,'crop_center_cutoff_distance':N,'crop_center_selection':choice('random','centroid'),'save_id':S},['kind']),
    obj({'kind':{'const':'off_target'},'off_target_slots':table(S)},['kind','off_target_slots'])]}


def _constructor_schema(target):
    result=copy.deepcopy(_PROFILE['constructors'][target])
    p=result['properties']
    # Filesystem paths are bound selected native artifacts, never browser paths.
    for name in list(p):
        if name.endswith(('_path','_dir','_paths')) or name in ('parse_config','device'):p.pop(name)
    if 'ligand_params_files' in p:
        p.pop('ligand_params_files')
        p['ligand_params_slots']=nullable(array({**S,'x-ui-control':'file'}))
    if 'pre_oracle_transform' in p:p['pre_oracle_transform']=nullable(TRANSFORM)
    if 'metric_thresholds' in p:p['metric_thresholds']=table(THRESHOLD)
    if target.endswith('.StructureComparisonMetric'):
        # The shipped compute_rmsd accepts but does not consume additional kwargs.
        p['metric_kwargs']=nullable(obj({'rmsd':obj({})}))
    if target.endswith('.AtomPairDistanceMetric'):
        p['constraints']=array(obj({'name':S,'atom_a':{'anyOf':[S,array(S)]},'atom_b':{'anyOf':[S,array(S)]},'max_distance_angstrom':N},['atom_a','atom_b','max_distance_angstrom']))
    return result


def _provider_schema(group):
    # Each alternative has its own typed constructor/config. Targets are profile constants.
    alternatives=[]
    for name, declaration in _PROFILE['groups'][group].items():
        config=copy.deepcopy(declaration.get('composed_schema',declaration['schema']))
        for provider in config.get('properties',{}).values():
            pp=provider.get('properties',{})
            target=pp.get('_target_',{}).get('const')
            if target in _PROFILE['constructors']:
                inherited=copy.deepcopy(_constructor_schema(target)['properties'])
                for key,spec in pp.items():
                    if key.endswith(('_path','_dir','_paths')) or key in ('parse_config','device','ligand_params_files'):continue
                    if key in inherited:
                        if 'default' in spec:inherited[key]['default']=copy.deepcopy(spec['default'])
                        inherited[key]['x-profile-declaration']=copy.deepcopy(spec)
                    else:inherited[key]=spec
                provider['properties']=inherited
        alternatives.append(obj({'profile':{'const':name},'overrides':config},['profile']))
    return {'oneOf':alternatives}

def parameter_schema(mode='generate'):
    if mode not in ('generate','evaluate'):raise ValueError('RFD4 mode must be generate or evaluate')
    props={'version':{'const':CONTRACT,'default':CONTRACT},'mode':{'const':mode,'default':mode},
        'input':INPUT,'settings':_settings(mode),'oracle_parameter_family':choice('af3','of3'),
        'providers':obj({g.split('/')[-1]:_provider_schema(g)
            for g in _PROFILE['groups'] if g.startswith('metrics/')}),
        'resume_slot':nullable({**S,'x-ui-control':'file'}),
        'adapter_slot':nullable({**S,'x-ui-control':'file'}),
        'metric_extensions':array({'oneOf':[obj({'name':S,'section':choice('inline_metrics','refolding_oracles','refolding_metrics','pre_oracle_filters','pass_criteria'),
            'implementation':{'const':target},'kwargs':_constructor_schema(target),
            'archive_slot':nullable(S)},['name','section','implementation','kwargs'])
            for target in _PROFILE['constructors'] if not target.endswith(('Transform','.Metric','.OracleMetric'))]})}
    if mode=='generate':
        props['groups']=obj({g:choice(*_PROFILE['groups'][g]) for g in ('recycle_strategy','generation','guidance')})
        schedule={'oneOf':[obj({'kind':{'const':'constant'},'w':N},['kind','w']),obj({'kind':{'const':'window'},'w':N,'t_start':N,'t_end':N},['kind','w','t_start','t_end'])]}
        props['guidance']=obj({'metrics':array(choice('contract_ca')),'terms':table(obj({'bb_ca':schedule,'local_latents':schedule}))})
    if mode=='evaluate':
        props['input']=copy.deepcopy(INPUT['oneOf'][2])
        props['evaluation_tasks']=array(obj({'slot':{**S,'x-ui-control':'file'},'task':S},['slot','task']))
        props.pop('resume_slot');props.pop('adapter_slot')
    result=obj(props,['input'])
    result.update({'$schema':'https://json-schema.org/draft/2020-12/schema','$id':CONTRACT+'/'+mode,
        'x-reviewed-source-revision':SOURCE_REVISION,'x-runtime-image-sha256':_PROFILE.get('runtime_image_sha256'),
        'x-default-authority':'selected official image Hydra group',
        'x-native-profiles':copy.deepcopy(_PROFILE['groups']),
        'x-selected-default-groups':{'generation':'inference_base','recycle_strategy':'thinking_medium','metrics/refolding_oracles':'af3'} if mode=='generate' else {'metrics/refolding_oracles':'af3'},
        'x-profile':{'hf_revision':HF_REVISION,'training_and_architecture':'paired checkpoint; not editable'}})
    def annotate(node,path=''):
        node.setdefault('title',path.rsplit('.',1)[-1].replace('_',' ').capitalize() or 'RFD4 request')
        node.setdefault('x-authority','profile' if 'const' in node else 'operator')
        node.setdefault('x-applicability',mode)
        node.setdefault('x-reproducibility',True)
        node.setdefault('x-units',None)
        node.setdefault('x-native-mapping',path.removeprefix('settings.'))
        kind=node.get('type')
        if isinstance(kind,list):kind=next((k for k in kind if k!='null'),None)
        node.setdefault('x-ui-control','select' if 'enum' in node else
            'table' if kind in ('array','object') else 'checkbox' if kind=='boolean' else
            'number' if kind in ('number','integer') else 'conditional' if 'oneOf' in node or 'anyOf' in node else 'text')
        for key,child in list(node.get('properties',{}).items()):
            node['properties'][key]=copy.deepcopy(child)
            annotate(node['properties'][key],(path+'.'+key).strip('.'))
        for key in ('oneOf','anyOf','prefixItems'):
            for index,child in enumerate(node.get(key,[])):
                node[key][index]=copy.deepcopy(child);annotate(node[key][index],path)
        for key in ('items','additionalProperties'):
            if isinstance(node.get(key),dict):
                node[key]=copy.deepcopy(node[key]);annotate(node[key],path+'[]')
    annotate(result)
    return result

def _validate(value,schema,path='rfd4_request'):
    for union in ('anyOf','oneOf'):
        if union in schema:
            successes=0
            for branch in schema[union]:
                try:_validate(value,branch,path);successes+=1
                except ValueError:pass
            if successes==0 or (union=='oneOf' and successes!=1):raise ValueError(f'{path}: no unique supported typed variant')
    types=schema.get('type',[])
    if isinstance(types,str):types=[types]
    matches={'null':value is None,'boolean':type(value) is bool,'integer':type(value) is int,
             'number':type(value) in (int,float),'string':isinstance(value,str),'array':isinstance(value,list),'object':isinstance(value,dict)}
    if types and not any(matches[t] for t in types):raise ValueError(f'{path}: expected {types}')
    if 'const' in schema and value!=schema['const']:raise ValueError(f'{path}: profile constant differs')
    if 'enum' in schema and value not in schema['enum']:raise ValueError(f'{path}: unsupported value')
    if type(value) in (int,float):
        for key,violates in [('minimum',lambda n:value<n),('maximum',lambda n:value>n),('exclusiveMinimum',lambda n:value<=n),('exclusiveMaximum',lambda n:value>=n)]:
            if key in schema and violates(schema[key]):raise ValueError(f'{path}: outside native {key}')
    if isinstance(value,list):
        if len(value)<schema.get('minItems',0) or len(value)>schema.get('maxItems',float('inf')):raise ValueError(f'{path}: invalid row count')
        for i,item in enumerate(value):
            prefix=schema.get('prefixItems',[])
            _validate(item,prefix[i] if i<len(prefix) else schema.get('items',{}),f'{path}[{i}]')
    if isinstance(value,dict):
        props=schema.get('properties',{})
        if set(schema.get('required',[]))-set(value):raise ValueError(f'{path}: missing required fields')
        extra=schema.get('additionalProperties',True)
        for key,item in value.items():
            if key not in props and extra is False:raise ValueError(f'{path}: unknown field {key}')
            _validate(item,props.get(key,extra if isinstance(extra,dict) else {}),f'{path}.{key}')


def normalize_request(request, mode=None):
    value=copy.deepcopy(request)
    mode=mode or value.get('mode','generate')
    value.setdefault('version',CONTRACT)
    value.setdefault('mode',mode)
    _validate(value,parameter_schema(mode))
    json.dumps(value,allow_nan=False)
    def reject_interpolation(v):
        if isinstance(v,str) and '${' in v:raise ValueError('Native settings accept literal values, not Hydra resolver expressions')
        if isinstance(v,dict):
            for child in v.values():reject_interpolation(child)
        elif isinstance(v,list):
            for child in v:reject_interpolation(child)
    reject_interpolation(value)
    # No truthiness defaults: false, zero, empty tables and null remain requested.
    return value

def request_identity(request):
    # Ordered declarations have last-write semantics, so preserve mapping order.
    return hashlib.sha256(json.dumps(request,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def native_overlay(request, bindings=None):
    """Return native values; unresolved source slots are labelled in pure previews."""
    r=normalize_request(request)
    bindings=bindings or {}
    def bound(slot):return str(bindings.get(slot,'slot:'+slot))
    def transform(kwargs):
        kwargs=copy.deepcopy(kwargs)
        if 'ligand_params_slots' in kwargs:
            slots=kwargs.pop('ligand_params_slots')
            kwargs['ligand_params_files']=None if slots is None else [bound(slot) for slot in slots]
        t=kwargs.get('pre_oracle_transform')
        if isinstance(t,dict) and 'kind' in t:
            kind=t.pop('kind')
            t['_target_']='rfproteina.metrics.oracles.pre_oracle_transform.'+{'apo':'ApoOnlyTransform','crop':'BinderContextCropTransform','off_target':'OffTargetSubstitutionTransform'}[kind]
            if kind=='off_target':t['off_target_paths']={name:bound(slot) for name,slot in t.pop('off_target_slots').items()}
        return kwargs
    result=copy.deepcopy(r.get('settings',{}))
    for section, selected in r.get('providers',{}).items():
        result.setdefault('metrics',{})[section]={name:transform(kwargs) for name,kwargs in selected.get('overrides',{}).items()}
    for extension in r.get('metric_extensions',[]):
        kwargs=transform(extension['kwargs'])
        kwargs['_target_']=extension['implementation']
        if extension.get('archive_slot') is not None:kwargs['archive_dir']=bound(extension['archive_slot'])
        result.setdefault('metrics',{}).setdefault(extension['section'],{})[extension['name']]=kwargs
    if 'guidance' in r:
        guide=copy.deepcopy(r['guidance'])
        # Metric implementation is a registered official example, never a user _target_.
        if 'metrics' in guide:
            guide['metrics']={name:{'_target_':'rfproteina.metrics.contract_ca_metric.ContractCAMetric'} for name in guide['metrics']}
        for modalities in guide.get('terms',{}).values():
            for schedule in modalities.values():
                schedule['_target_']='rfproteina.guidance.schedules.'+schedule.pop('kind')
                schedule['_partial_']=True
        result['guidance']=guide
    return result

def selected_dependencies(request):
    r=normalize_request(request)
    tags=set(r.get('settings',{}).get('metrics_tags',[]))
    selected=r.get('providers',{}).get('refolding_oracles',{'profile':'af3','overrides':{}})
    oracle_profiles=_PROFILE['groups']['metrics/refolding_oracles']
    declarations=oracle_profiles[selected['profile']]['schema']['properties']
    search=r.get('settings',{}).get('search') or {}
    explicit={name for checkpoint in (search.get('checkpoints') or {}).values() for name in checkpoint.get('metrics',[])}
    active=[]
    for name,declaration in declarations.items():
        defaults={k:v.get('default') for k,v in declaration.get('properties',{}).items()}
        defaults.update(selected.get('overrides',{}).get(name,{}))
        groups=[set(defaults.get(k) or []) for k in ('required_tags_all','required_tags_any','prohibited_tags')]
        if name in explicit:groups=[{t for t in g if t.startswith(('is_refold:','exists_refold:','oracle:'))} for g in groups]
        required_all,required_any,prohibited=groups
        if (not required_all or tags.issuperset(required_all)) and (not required_any or tags.intersection(required_any)) and not tags.intersection(prohibited):active.append(name)
    active_profiles=[selected['profile']] if active else []
    for extension in r.get('metric_extensions',[]):
        family={'AF2Oracle':'af2','AF3Oracle':'af3'}.get(extension['implementation'].rsplit('.',1)[-1])
        if family is None:continue
        kwargs=extension['kwargs']
        all_tags=set(kwargs.get('required_tags_all') or [])
        any_tags=set(kwargs.get('required_tags_any') or [])
        prohibited=set(kwargs.get('prohibited_tags') or [])
        if extension['name'] in explicit:
            all_tags={t for t in all_tags if t.startswith(('is_refold:','exists_refold:','oracle:'))}
            any_tags={t for t in any_tags if t.startswith(('is_refold:','exists_refold:','oracle:'))}
            prohibited={t for t in prohibited if t.startswith(('is_refold:','exists_refold:','oracle:'))}
        if tags.issuperset(all_tags) and (not any_tags or tags.intersection(any_tags)) and not tags.intersection(prohibited):
            active_profiles.append(family);active.append(extension['name'])
    return {'primary':'rfd4_proteina','providers':{k:v['profile'] for k,v in r.get('providers',{}).items()},
            'oracle_parameter_family':r.get('oracle_parameter_family', selected['profile'] if selected['profile'] in ('af3','of3') else None),
            'active_oracle_profiles':list(dict.fromkeys(active_profiles)),'active_oracle_names':active,
            'metric_extensions':[e['implementation'] for e in r.get('metric_extensions',[])],
            'metrics_tags':r.get('settings',{}).get('metrics_tags',[]),'search':search,'source_slots':source_slots(r)}

def source_slots(request):
    i=request['input']; slots=[]
    if i['kind']=='annotated':slots.extend(i['slots'])
    if i['kind']=='condition_spec':
        for e in i['examples']: slots.extend(s['slot'] for s in e['sources'])
    for key in ('resume_slot','adapter_slot'):
        if request.get(key) is not None:slots.append(request[key])
    kwargs=[k for p in request.get('providers',{}).values() for k in p.get('overrides',{}).values()]
    for extension in request.get('metric_extensions',[]):
        kwargs.append(extension['kwargs'])
        if extension.get('archive_slot') is not None:slots.append(extension['archive_slot'])
    for k in kwargs:
        slots.extend(k.get('ligand_params_slots') or [])
        t=k.get('pre_oracle_transform') or {}
        slots.extend(t.get('off_target_slots',{}).values())
    return list(dict.fromkeys(slots))
