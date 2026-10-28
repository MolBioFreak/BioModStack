import json
import shutil

import pytest

from bms_alignment.bundle import inspect_project_bundle, make_bundle


def test_typed_inventory_verified_after_relocation(tmp_path):
    src = tmp_path / 'source'
    src.mkdir()
    (src / 'alignment').write_bytes(b'>a\nAC-\n')
    inventory = {'schema': 'bms.family-msa.project-export.v1', 'project_id': 'source',
                 'receipt_id': 'receipt', 'artifacts': [
                     {'id': 'a', 'role': 'alignment', 'logical_path': 'a.fa', 'path': 'alignment'}],
                 'shared_states': []}
    (src / 'project-export.json').write_text(json.dumps(inventory))
    bundle = tmp_path / 'bundle'
    make_bundle(src, bundle, ['project-export.json', 'alignment'])
    relocated = tmp_path / 'relocated'
    shutil.move(bundle, relocated)
    shutil.rmtree(src)
    assert inspect_project_bundle(relocated)['inventory'] == inventory
    (relocated / 'alignment').write_bytes(b'changed')
    with pytest.raises(ValueError, match='verification failed'):
        inspect_project_bundle(relocated)
