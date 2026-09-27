"""Chain roles and positional transport; native CPU parsing, no inference."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from test_sequence_source_correspondence import CALIBY_INERT, ROOT, full_backbone
sys.path.insert(0, str(ROOT / 'scripts'))
from caliby_runtime import remap_constraint_dataframe_to_cleaned_paths


def pair(sc, sn, oc, on):
    return dict(source=dict(chain_id=sc, auth_seq_id=sn, insertion_code=''),
                output=dict(chain_id=oc, auth_seq_id=on, insertion_code=''))


def test_same_chain_set_swap_and_gapped_renumbering(tmp_path):
    original = tmp_path/'original'/'x.pdb'; original.parent.mkdir()
    cleaned = tmp_path/'cleaned'/'x.pdb'; cleaned.parent.mkdir()
    original.write_text(full_backbone()); cleaned.write_text(full_backbone())
    mapping = [pair('B',10,'A',1), pair('B',30,'A',2), pair('A',10,'B',1), pair('A',30,'B',2)]
    df = pd.DataFrame([dict(pdb_key='x', fixed_pos_seq='B10-30,A30', fixed_pos_scn='A',
                           fixed_pos_override_seq='B10:G', pos_restrict_aatype='A30:VG',
                           symmetry_pos='B10,A10|B30,A30')])
    result = remap_constraint_dataframe_to_cleaned_paths(df, original_paths=[str(original)],
                cleaned_paths=[str(cleaned)], sources={'x': {'source_residue_mapping': mapping}})
    assert result.iloc[0].to_dict() == dict(pdb_key='x', fixed_pos_seq='A1,A2,B2', fixed_pos_scn='B1,B2',
                 fixed_pos_override_seq='A1:G', pos_restrict_aatype='B2:VG', symmetry_pos='A1,B1|A2,B2')
    assert df.iloc[0].fixed_pos_seq == 'B10-30,A30'
    # Missing evidence keeps the old same-ID behavior, not a new refusal.
    pd.testing.assert_frame_equal(df, remap_constraint_dataframe_to_cleaned_paths(df,
                       original_paths=[str(original)], cleaned_paths=[str(cleaned)]))


@pytest.mark.parametrize('binder,target', [('B','A'), ('Z','B')])
def test_native_cleaner_constraints_and_output_roles(tmp_path, binder, target):
    source = tmp_path/'inputs'/'selected.pdb'; source.parent.mkdir()
    source.write_text(''.join(l[:21]+{'B':binder,'A':target}[l[21]]+l[22:] if l.startswith('ATOM') else l
                              for l in full_backbone().splitlines(keepends=True)))
    constraints = tmp_path/'constraints.csv'
    pd.DataFrame([dict(pdb_key='selected', fixed_pos_seq=f'{target}10-34,{binder}10-12',
                       fixed_pos_scn=f'{target}10-34')]).to_csv(constraints,index=False)
    inert = tmp_path/'inert.py'; inert.write_text(CALIBY_INERT)
    out = tmp_path/'out'
    result = subprocess.run(['apptainer','exec','--bind',f'{tmp_path}:/cache',os.environ['BMS_TEST_CALIBY_IMAGE'],
            'python',str(inert),str(ROOT/'scripts'),'--input-dir',str(source.parent),'--output-dir',str(out),
            '--binder-chains',binder,'--target-chains',target,'--pos-constraint-csv',str(constraints),
            '--clean-num-workers','1'],text=True,capture_output=True,timeout=180)
    assert result.returncode == 0, result.stdout+result.stderr
    row = json.loads((out/'generator_caliby_0001.json').read_text())
    mapping = row['source_residue_mapping']
    assert len(mapping) == 20
    projected = lambda chain: sorted({r['output']['chain_id'] for r in mapping if r['source']['chain_id']==chain})
    assert row['binder_chains'] == projected(binder)
    assert row['target_chains'] == projected(target)
    assert row['input_binder_chains'] == [binder]
    assert row['input_target_chains'] == [target]
    assert row['chain_roles_namespace'] == 'output'
    assert row['designed_chain_sequences'] == {c:'G'*10 for c in projected(binder)}
    observed = json.loads((out/'observed_constraints.json').read_text())
    for column in ('fixed_pos_seq','fixed_pos_scn'):
        expected = {(r['output']['chain_id'],r['output']['auth_seq_id']) for r in mapping
                    if r['source']['chain_id']==target or (column=='fixed_pos_seq' and
                       r['source']['chain_id']==binder and 10<=r['source']['auth_seq_id']<=12)}
        assert set(map(tuple,observed[column])) == expected
