"""Regenerate model-local typed config projection from the pinned official source.

Development tool, not a runtime config composer. Hydra remains the only composer.
Usage: python -m bms_rfd4.build_schema /path/to/pinned/source
"""
from pathlib import Path
import ast
import json
import sys
import yaml

REVISION = '8f7f702e46f944713fbe8ff5c566221aaa54eb0b'

def obj(properties):
    return {'type': 'object', 'additionalProperties': False, 'properties': properties}

def schema(value, path=''):
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, dict):
        return obj({k: schema(v, f'{path}.{k}'.strip('.')) for k, v in value.items() if k != 'defaults'})
    kind = {bool:'boolean', int:'integer', float:'number', str:'string', list:'array', type(None):'null'}[type(value)]
    s = {'type':kind, 'default':value}
    if kind == 'array':
        s['items'] = schema(value[0], path+'[]') if value else {'type':'string'}
    if path.endswith('._target_'):
        s.update(const=value, **{'x-authority':'profile','readOnly':True})
    return s

def main(source, image_sha256=None):
    root = Path(source)
    def load(p): return yaml.safe_load((root/'configs'/p).read_text())
    generation = load('generation/inference_base.yaml')
    base = load('inference.yaml')
    allowed = 'verbose num_replicates compile_lengths nn precision batch_size subset_to_examples prevalidate_inputs seed torch_deterministic skip_existing save_chain_of_interest compress_outputs metrics_tags search num_workers inference_backend bioir delete_failing_pre_oracle'.split()
    settings = schema({k:base[k] for k in allowed})
    settings['properties']['generation'] = schema(generation,'generation')
    # Null is an actual clear, not an untyped arbitrary JSON value.
    nullable = {'seed':'integer','subset_to_examples':'array','compile_lengths':'array',
        'bioir.attention_backend':'string','bioir.cuda_graph_max_tokens':'integer','bioir.max_graphs':'integer',
        'nn.torch_compile_mode':'string','nn.torch_compile_fullgraph':'boolean','nn.tri_mult_backend':'string','nn.attention_type':'string',
        'generation.n_recycle_schedule':'array','generation.val_set_name_override':'string',
        'generation.args.partial_trajectory_fraction_steps':'number','generation.args.partial_t_backbone':'number',
        'generation.args.partial_t_latent':'number','generation.model.bb_ca.gt.clamp_val':'number',
        'generation.model.local_latents.gt.clamp_val':'number','search.resume_seed':'integer'}
    for path, typ in nullable.items():
        node=settings
        for part in path.split('.'): node=node['properties'][part]
        node['type']=[typ,'null']
        if typ=='array': node['items']={'type':'integer'}
    settings['properties']['compile_lengths']['type']=['array','null']
    settings['properties']['generation']['properties']['n_recycle_schedule']['items']={'type':'array','items':{'type':'integer'},'minItems':2,'maxItems':2}
    for path, choices in {'inference_backend':['native','bioir'], 'generation.args.inverse_folding_method':['resampled_forward_marginal']}.items():
        node=settings
        for part in path.split('.'):node=node['properties'][part]
        node['enum']=choices
    for name in ('partial_trajectory_fraction_steps','partial_t_backbone','partial_t_latent'):
        settings['properties']['generation']['properties']['args']['properties'][name].update(
            **({'exclusiveMinimum':0,'maximum':1} if name.endswith('steps') else {'exclusiveMaximum':1}))
    # Keep selected provider alternatives separate; never merge AF2 defaults into AF3.
    groups={}
    for directory in ['recycle_strategy','generation','metrics/inline_metrics','metrics/refolding_oracles','metrics/refolding_metrics','metrics/pre_oracle_filters','metrics/pass_criteria','guidance']:
        groups[directory]={}
        for p in sorted((root/'configs'/directory).rglob('*.yaml')):
            name=str(p.relative_to(root/'configs'/directory).with_suffix(''))
            text=p.read_text()
            package=next((line.split('@package',1)[1].strip() for line in text.splitlines() if line.startswith('# @package')),directory.replace('/','.'))
            groups[directory][name]={'schema':schema(yaml.safe_load(text) or {}, package), 'package':package,'source':str(p.relative_to(root))}
    # Resolve each *selected* metrics group through official Hydra defaults. This
    # includes all_inline_metrics' inherited providers without mixing alternatives.
    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf
    with initialize_config_dir(config_dir=str((root/'configs').resolve()),version_base='1.3'):
        default_cfg=compose(config_name='inference')
        default_generation=OmegaConf.to_container(default_cfg.generation,resolve=False)
        for group,profiles in groups.items():
            if group in ('generation','recycle_strategy'):
                for name,declaration in profiles.items():
                    cfg=compose(config_name='inference',overrides=[group+'='+name])
                    declaration['effective_generation']=OmegaConf.to_container(cfg.generation,resolve=False)
                continue
            if not group.startswith('metrics/'):continue
            for name,declaration in profiles.items():
                cfg=compose(config_name='inference',overrides=[group+'='+name])
                section=OmegaConf.select(cfg,group.replace('/','.'))
                declaration['schema']=schema(OmegaConf.to_container(section,resolve=False),group.replace('/','.'))
                declaration['default_scope']='selected Hydra group composition'
    # Constructor fields are added from actual AST, never from imported arbitrary targets.
    def annotation(node):
        if node is None:return None
        if isinstance(node,ast.Constant) and node.value is None:return {'type':'null'}
        if isinstance(node,ast.Name):
            if node.id == 'SymmetryLevel':
                return {'type':'string', 'enum':['atom','subunit']}
            typ={'str':'string','int':'integer','float':'number','bool':'boolean','Path':'string'}.get(node.id)
            return {'type':typ} if typ else None
        if isinstance(node,ast.BinOp) and isinstance(node.op,ast.BitOr):
            alternatives=[annotation(node.left),annotation(node.right)]
            return {'anyOf':alternatives} if all(alternatives) else None
        if isinstance(node,ast.Subscript):
            base=ast.unparse(node.value)
            if base=='Literal':
                values=node.slice.elts if isinstance(node.slice,ast.Tuple) else [node.slice]
                return {'enum':[ast.literal_eval(v) for v in values]}
            if base=='tuple' and isinstance(node.slice,ast.Tuple):
                elements=node.slice.elts
                if isinstance(elements[-1],ast.Constant) and elements[-1].value is Ellipsis:
                    child=annotation(elements[0])
                    return {'type':'array','items':child} if child else None
                children=[annotation(e) for e in elements]
                return {'type':'array','prefixItems':children,'minItems':len(children),'maxItems':len(children)} if all(children) else None
            if base in ('list','set','tuple','Sequence'):
                child=annotation(node.slice)
                return {'type':'array','items':child} if child else None
            if base in ('dict','Mapping') and isinstance(node.slice,ast.Tuple):
                child=annotation(node.slice.elts[1])
                return {'type':'object','additionalProperties':child} if child else None
        return None
    constructors={}
    bases={}
    simple_names={}
    for p in (root/'src/rfproteina/metrics').rglob('*.py'):
        for cls in ast.parse(p.read_text()).body:
            if not isinstance(cls,ast.ClassDef): continue
            init=next((n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='__init__'),None)
            fields={}
            args=(init.args.args[1:]+init.args.kwonlyargs) if init else []
            defaults=([None]*(len(init.args.args[1:])-len(init.args.defaults))+list(init.args.defaults)+list(init.args.kw_defaults)) if init else []
            for arg, default in zip(args,defaults):
                typed=annotation(arg.annotation)
                try: val=ast.literal_eval(default) if default is not None else None
                except (ValueError,TypeError):val=None
                if not isinstance(val,(str,int,float,bool,list,tuple,dict,type(None))):continue
                s=schema(val,arg.arg)
                if typed:
                    s={k:v for k,v in s.items() if k not in ('type','items','properties','additionalProperties')}
                    s.update(typed)
                if default is None:s.pop('default',None)
                hint=ast.unparse(arg.annotation) if arg.annotation else ''
                s['x-ui-control']=('table' if any(t in hint for t in ('list','dict','tuple','set','Sequence','Mapping')) else
                                     'select' if 'Literal' in hint else 'checkbox' if 'bool' in hint else
                                     'number' if 'float' in hint or 'int' in hint else 'text')
                fields[arg.arg]=s
            target='.'.join(p.relative_to(root/'src').with_suffix('').parts)+'.'+cls.name
            constructors[target]=obj(fields)
            bases[target]=[ast.unparse(b) for b in cls.bases]
            simple_names[cls.name]=target
    def inherited(target,visited=()):
        if target in visited:return {}
        fields={}
        for base in bases[target]:
            if base in simple_names:fields.update(inherited(simple_names[base],(*visited,target)))
        fields.update(constructors[target]['properties'])
        return fields
    for target in constructors:
        constructors[target]['properties']=inherited(target)
    # Do not expose unrelated neural-network or job-manager constructors as science.
    def is_metric(target,seen=()):
        if target in seen:return False
        return target.endswith('.Metric') or target.endswith('Transform') or any(
            b=='Metric' or (b in simple_names and is_metric(simple_names[b],(*seen,target))) for b in bases[target])
    constructors={k:v for k,v in constructors.items() if is_metric(k)}
    output={'reviewed_source_revision':REVISION,'runtime_image_sha256':image_sha256,
            'source_revision':None if image_sha256 else REVISION,
            'settings':settings,'groups':groups,'constructors':constructors,'default_effective_generation':default_generation}
    (Path(__file__).parent/'schema_profile.json').write_text(json.dumps(output,indent=2)+'\n')

if __name__=='__main__':main(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else None)
