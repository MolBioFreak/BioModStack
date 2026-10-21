"""Thin official input authoring. Annotation-preserving sources are never cleaned."""
from __future__ import annotations
import json
import shutil
from pathlib import Path


def prepare_inputs(request, bindings, destination, *, sentinel=None):
    # Registration must precede every annotated parse, including refinement.
    import rfproteina.conditions  # noqa: F401
    import numpy as np
    import rfproteina
    from atomworks.io import parse
    from atomworks.io.utils.io_utils import to_cif_file
    from atomworks.io.utils.standard_annotations import S_SEGMIN, S_SEGMAX
    from atomworks.ml.utils.expandable_segment import insert_expandable_segment
    from rfproteina.utils.parse_presets import ANNOTATED_CIF
    from rfproteina.utils.common import validate_design_id

    dest=Path(destination); dest.mkdir(parents=True,exist_ok=True)
    originals=dest/'originals'; originals.mkdir(exist_ok=True)
    native=dest/'native'; native.mkdir(exist_ok=True)
    paths={}
    # Only selected input slots; adapters/states are separately bound by the runner.
    inp=request['input']
    slots=inp.get('slots',[]) if inp['kind']=='annotated' else [s['slot'] for e in inp.get('examples',[]) for s in e['sources']]
    for n,slot in enumerate(dict.fromkeys(slots)):
        src=Path(bindings[slot]); target=originals/f'{n:04d}_{src.name}'
        shutil.copyfile(src,target)
        paths[slot]=target.resolve()
    mapping=[]
    if inp['kind']=='annotated':
        for n,slot in enumerate(inp['slots']):
            src=paths[slot]; target=native/f'{n:04d}_{src.name}'
            shutil.copyfile(src,target)  # SA_CWI and C_INTCRD loops byte-identical.
            mapping.append({'slot':slot,'original':str(src),'native':str(target.resolve()),'representation':'unchanged_annotated'})
    elif inp['kind']=='unconditional':
        validate_design_id(inp['name'])
        if sentinel is None:
            package=Path(rfproteina.__file__).parent
            import rootutils
            roots=[package,rootutils.find_root(rfproteina.__file__,indicator='.project-root')]
            candidates=[root/('data/benchmarks/monomer/monomer-short'+suffix) for root in roots for suffix in ('.cif.zst','.cif')]
            sentinel=next((p for p in candidates if p.is_file()),candidates[0])
        a=parse(sentinel,config=ANNOTATED_CIF)['asym_unit'][0]
        first,*rest=inp['chains']
        # Native benchmark sentinel: masked sequence/unknown coordinates, never an anchor.
        S_SEGMIN.set_annotation(a,np.full(len(a),first['min_length'],dtype=int))
        S_SEGMAX.set_annotation(a,np.full(len(a),first['max_length'],dtype=int))
        a.chain_id[:]=first['chain_id']
        for chain in rest:
            a=insert_expandable_segment(a,chain['min_length'],chain['max_length'],new_chain=True,
                                       new_chain_id=chain['chain_id'],new_chain_type='polypeptide(L)')
        target=native/(inp['name']+'.cif')
        to_cif_file(a,target,save_standard_annotations=True,chain_disambiguation='chain_iid')
        mapping.append({'native':str(target.resolve()),'representation':'official_unconditional_sentinel','template':str(sentinel)})
    else:
        from rfproteina.inference_utils.condition_spec import build_annotated_atom_array_from_condition_spec
        for example in inp['examples']:
            validate_design_id(example['name'])
            spec=dict(inp.get('global_args',{}))
            spec['input']=[str(paths[s['slot']]) for s in example['sources']]
            # condition_spec has one parse policy per example, not one per source.
            policies={s.get('input_as_annotated_cif',spec.get('input_as_annotated_cif',False)) for s in example['sources']}
            if len(policies)!=1:raise ValueError('Native condition_spec requires one input_as_annotated_cif policy per example')
            spec['input_as_annotated_cif']=policies.pop()
            for key in ('contig','conditions'):
                if key in example:spec[key]=example[key]
            a=build_annotated_atom_array_from_condition_spec(spec,spec_dir=originals,example_id=example['name'],validate=False)['atom_array']
            if example.get('pair_conditions'):
                from rfproteina.conditions import C_BP, BasePairEnum
                from rfproteina.utils.mask_spec import resolve_mask
                existing=C_BP.annotation(a)
                entries={tuple(int(i) for i in pair):int(value) for pair,value in zip(existing.pairs,existing.values)}
                for row in example['pair_conditions']:
                    left=np.flatnonzero(resolve_mask(a,row['left'])); right=np.flatnonzero(resolve_mask(a,row['right']))
                    # Pair rows select exact representative atoms, not an inferred cross-product.
                    if len(left)!=1 or len(right)!=1:raise ValueError('Pair selectors must each identify one representative atom')
                    pair=(int(left[0]),int(right[0]));value=BasePairEnum[row['value']].value
                    entries[pair]=value;entries[pair[::-1]]=value
                C_BP.set_annotation(a,pairs=np.asarray(list(entries),dtype=np.int32).reshape(-1,2),values=np.asarray(list(entries.values()),dtype=np.int64))
            target=native/(example['name']+'.cif')
            to_cif_file(a,target,save_standard_annotations=True,chain_disambiguation='chain_iid')
            # Native source/copy annotations remain authoritative; retain their exact values.
            ids={k:a.get_annotation(k).tolist() for k in a.get_annotation_categories()
                 if k in ('chain_id','res_id','ins_code','atom_name','res_name','chain_iid','molecule_iid','transformation_id') or k.startswith(('auth_','label_')) or 'source' in k.lower() or 'src' in k.lower()}
            mapping.append({'example_id':example['name'],'sources':[s['slot'] for s in example['sources']],
                'native':str(target.resolve()),'representation':'annotated_derivative' if spec['input_as_annotated_cif'] else 'fresh_cleaned_derivative',
                'native_atom_identity':ids})
    (dest/'input_identity.json').write_text(json.dumps(mapping,indent=2)+'\n')
    return native.resolve(),mapping
