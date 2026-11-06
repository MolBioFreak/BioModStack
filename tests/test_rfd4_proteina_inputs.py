"""CPU native-input checks against explicit pinned official specimens."""
import os
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))


def runtime():
    source=os.environ.get('BMS_TEST_RFD4_SOURCE')
    specimens=os.environ.get('BMS_TEST_RFD4_SPECIMENS')
    if not source or not specimens:pytest.skip('Explicit pinned native source/specimens required')
    sys.path.insert(0,str(Path(source)/'src'))
    import rfproteina.conditions
    return Path(specimens)


def test_native_masked_multichain_ranges_and_retained_bytes(tmp_path):
    specimens=runtime()
    from bms_rfd4.inputs import prepare_inputs
    from atomworks.io import parse
    from rfproteina.utils.parse_presets import ANNOTATED_CIF
    from atomworks.io.utils.standard_annotations import S_SEGMIN,S_SEGMAX
    from atomworks.ml.conditions import C_SEQ,C_CRD
    import numpy as np
    r={'input':{'kind':'unconditional','name':'two','chains':[
        {'chain_id':'A','min_length':12,'max_length':19},{'chain_id':'B','min_length':8,'max_length':8}]}}
    native,_=prepare_inputs(r,{},tmp_path/'first',sentinel=specimens/'monomer-short.cif.zst')
    a=parse(native/'two.cif',config=ANNOTATED_CIF)['asym_unit'][0]
    assert S_SEGMIN.annotation(a).tolist()==[12,8]
    assert S_SEGMAX.annotation(a).tolist()==[19,8]
    assert not C_SEQ.mask(a).any() and not C_CRD.mask(a).any()
    assert np.isnan(a.coord).all()
    source=specimens/'2o5n_ppi_3d_conditioned.cif.zst'
    r={'input':{'kind':'annotated','slots':['prior']}}
    native,identity=prepare_inputs(r,{'prior':str(source)},tmp_path/'retained')
    retained=Path(identity[0]['native'])
    assert retained.read_bytes()==source.read_bytes()
    assert parse(retained,config=ANNOTATED_CIF)['asym_unit'][0].array_length()>0


def test_native_condition_spec_preserves_saved_coordinate_payload(tmp_path):
    specimens=runtime()
    from bms_rfd4.inputs import prepare_inputs
    from rfproteina.inference_utils.condition_spec import build_annotated_atom_array_from_condition_spec
    from atomworks.io import parse
    from rfproteina.utils.parse_presets import ANNOTATED_CIF
    import numpy as np
    from rfproteina.conditions import C_INTCRD
    from rfproteina.utils.standard_annotations import SA_CWI
    from atomworks.io.utils.io_utils import to_cif_file
    original=specimens/'2o5n_ppi_3d_conditioned.cif.zst'
    fixture=parse(original,config=ANNOTATED_CIF)['asym_unit'][0]
    # A transport fixture, not generated science: distinct saved targets must not
    # be rebuilt from the current pose when a retained annotation is re-authored.
    targets=fixture.coord.copy()
    SA_CWI.set_annotation(fixture,targets)
    C_INTCRD.set_annotation(fixture,targets)
    fixture.coord=fixture.coord+1.0
    source=tmp_path/'saved_targets.cif.zst'
    to_cif_file(fixture,source,save_standard_annotations=True)
    r={'input':{'kind':'condition_spec','examples':[{'name':'retarget','sources':[{'slot':'prior','input_as_annotated_cif':True}]}]}}
    native,_=prepare_inputs(r,{'prior':str(source)},tmp_path/'wrapped')
    direct=build_annotated_atom_array_from_condition_spec({'input':[str(source)],'input_as_annotated_cif':True},spec_dir=source.parent,validate=False)['atom_array']
    wrapped=parse(native/'retarget.cif',config=ANNOTATED_CIF)['asym_unit'][0]
    assert len(wrapped)==len(direct)
    np.testing.assert_allclose(wrapped.coord,direct.coord,equal_nan=True,atol=0.001)
    # Compare every native condition/standard annotation present in both representations.
    common=set(wrapped.get_annotation_categories())&set(direct.get_annotation_categories())
    checked=[]
    for name in sorted(common):
        if not name.startswith('condition_') and name not in (SA_CWI.full_name,C_INTCRD.full_name):continue
        a=wrapped.get_annotation(name);b=direct.get_annotation(name)
        if np.issubdtype(a.dtype,np.number):np.testing.assert_allclose(a,b,equal_nan=True,atol=0.001)
        else:np.testing.assert_array_equal(a,b)
        checked.append(name)
    assert SA_CWI.full_name in checked and C_INTCRD.full_name in checked
    np.testing.assert_allclose(SA_CWI.annotation(wrapped),targets,equal_nan=True,atol=0.001)
    np.testing.assert_allclose(C_INTCRD.annotation(wrapped),targets,equal_nan=True,atol=0.001)
