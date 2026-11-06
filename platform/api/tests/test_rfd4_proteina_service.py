"""Model API leaf contract/materialization, without Job/router substitutions."""
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('rfd4_service',ROOT/'platform/api/services/rfd4_proteina.py')
service=importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


def test_pure_compile_and_retained_source_closure(tmp_path):
    source=tmp_path/'native.cif.zst'; source.write_bytes(b'fixture bytes: original compressed custody')
    params={'rfd4_request':{'mode':'evaluate','input':{'kind':'annotated','slots':['selected-document']},
                           'settings':{'metrics_tags':[],'delete_failing_pre_oracle':False}},'placement':None}
    compiled=service.compile_request('evaluate',params,source_identities={'selected-document':{'document_id':'exact-alternate'}})
    assert compiled['sources']['selected-document']['document_id']=='exact-alternate'
    before=set(tmp_path.iterdir())
    assert service.compile_request('evaluate',params)['request_identity']==compiled['request_identity']
    assert set(tmp_path.iterdir())==before
    prepared=service.prepare_for_job('evaluate',params,tmp_path/'job',resolved_sources={'selected-document':source},allowed_roots=[tmp_path])
    payload=json.loads(Path(prepared['rfd4_prepared_request']).read_text())
    bindings=json.loads(Path(prepared['rfd4_bindings']).read_text())
    retained=Path(prepared['rfd4_input_closure'])/bindings['selected-document']
    assert retained.read_bytes()==source.read_bytes()
    assert payload['request']==compiled['request']
    assert prepared['placement'] is None


def test_schema_is_same_owner_not_rfd3_projection():
    from scripts.lib.rfd4_proteina_contract import parameter_schema, MODEL_ID
    from services.native_generation_sources import source_slot_schema
    # The shared owner annotates typed source-slot controls, not scientific defaults.
    for mode in ('generate', 'evaluate'):
        assert service.generation_settings(mode)['parameter_schema'] == source_slot_schema(parameter_schema(mode), MODEL_ID)
