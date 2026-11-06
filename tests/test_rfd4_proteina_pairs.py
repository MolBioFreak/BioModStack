"""Native sparse pair edit uses existing official DNA specimen, no inference."""
import os
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))


def test_native_pair_edits_keep_unmentioned_pairs(tmp_path):
    source_root=os.environ.get('BMS_TEST_RFD4_SOURCE')
    specimens=os.environ.get('BMS_TEST_RFD4_SPECIMENS')
    if not source_root or not specimens:pytest.skip('Explicit pinned native source/specimens required')
    sys.path.insert(0,str(Path(source_root)/'src'))
    import numpy as np
    import rfproteina.conditions
    from rfproteina.conditions import C_BP,BasePairEnum
    from atomworks.enums import ChainType
    from atomworks.io import parse
    from atomworks.io.utils.io_utils import to_cif_file
    from rfproteina.utils.parse_presets import ANNOTATED_CIF
    from bms_rfd4.inputs import prepare_inputs
    a=parse(Path(specimens)/'7m5w_structure_context.cif.zst',config=ANNOTATED_CIF)['asym_unit'][0]
    indices=[];seen=set()
    for i in range(len(a)):
        key=(a.chain_id[i],a.res_id[i])
        if ChainType(int(a.chain_type[i])).is_nucleic_acid() and key not in seen:
            indices.append(i);seen.add(key)
    i,j,k,l=indices[:4]
    # Transport fixture: the first pair is pre-existing, the second an explicit edit.
    C_BP.set_annotation(a,pairs=np.asarray([[i,j]]),values=np.asarray([BasePairEnum.PAIR.value]))
    src=tmp_path/'pairs.cif'
    to_cif_file(a,src,save_standard_annotations=True)
    def query(index):
        return f"chain_id == {str(a.chain_id[index])!r} and res_id == {int(a.res_id[index])} and atom_name == {str(a.atom_name[index])!r}"
    request={'input':{'kind':'condition_spec','examples':[{'name':'pairs','sources':[{'slot':'dna','input_as_annotated_cif':True}],
              'pair_conditions':[{'left':query(k),'right':query(l),'value':'PAIR'}]}]}}
    native,_=prepare_inputs(request,{'dna':str(src)},tmp_path/'prepared')
    b=parse(native/'pairs.cif',config=ANNOTATED_CIF)['asym_unit'][0]
    stored=C_BP.annotation(b)
    pairs={tuple(row):int(value) for row,value in zip(stored.pairs,stored.values)}
    assert pairs[(i,j)]==pairs[(j,i)]==pairs[(k,l)]==pairs[(l,k)]==BasePairEnum.PAIR.value
