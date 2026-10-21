"""Pure RFD4 API binding plus existing-owner retained-file materialization.

JobCreate, placement, CandidateRef resolution and preview ownership stay shared.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import shutil
from scripts.lib.rfd4_proteina_contract import (
    CONTRACT, MODEL_ID, RESULT_CONTRACT, parameter_schema, normalize_request,
    native_overlay, request_identity, selected_dependencies, source_slots,
)

MODES=frozenset({'generate','evaluate'})
REQUEST_FIELD='rfd4_request'
PREPARED_FIELD='rfd4_prepared_request'
BINDINGS_FIELD='rfd4_bindings'
DIRECTORY='rfd4_proteina'


def normalize_params(mode,params):
    """Shared envelope fields remain at their existing owner, not copied into science."""
    result=copy.deepcopy(params)
    result[REQUEST_FIELD]=normalize_request(result[REQUEST_FIELD],mode)
    return result


def compile_request(mode,params,*,source_identities=None):
    request=normalize_request(params[REQUEST_FIELD],mode)
    identities=source_identities or {}
    slots=source_slots(request)
    return {'contract':CONTRACT,'model_id':MODEL_ID,'mode':mode,'request':request,
            'request_identity':request_identity(request),'native_overlay':native_overlay(request),
            'sources':{slot:copy.deepcopy(identities.get(slot)) for slot in slots},
            'dependencies':selected_dependencies(request),'result_contract':RESULT_CONTRACT}


def prepare_for_job(mode,params,directory,*,resolved_sources,allowed_roots,runtime_identity=None,source_identities=None):
    """Retain source closure, not native science; runner prepares once in selected image.

    resolved_sources is slot -> exact materialized path from the shared native-document
    owner. It is never a second CandidateRef resolver or a primary-document fallback.
    """
    from scripts.lib.portable_inputs import _contained
    compiled=compile_request(mode,params,source_identities=source_identities)
    dest=Path(directory)/DIRECTORY/compiled['request_identity']
    dest.mkdir(parents=True,exist_ok=True)
    sources=dest/'sources'; sources.mkdir(exist_ok=True)
    roots=[Path(p).resolve() for p in allowed_roots]
    bindings={}
    for index,slot in enumerate(source_slots(compiled['request'])):
        src=_contained(Path(resolved_sources[slot]),roots)
        target=sources/f'{index:04d}_{src.name}'
        if src.is_dir():
            for member in src.rglob('*'):
                if member.is_file():_contained(member,roots)
            shutil.copytree(src,target,dirs_exist_ok=True)
        else:
            shutil.copyfile(src,target)
        # Relative to the staged closure. Runtime resolves these once before native work.
        bindings[slot]=str(target.relative_to(dest))
    payload={'request':compiled['request'],'request_identity':compiled['request_identity'],
             'runtime_identity':{**copy.deepcopy(runtime_identity or {}),'sources':compiled['sources']}}
    (dest/'request.json').write_text(json.dumps(payload,indent=2)+'\n')
    (dest/'bindings.json').write_text(json.dumps(bindings,indent=2)+'\n')
    return {**normalize_params(mode,params),PREPARED_FIELD:str(dest/'request.json'),BINDINGS_FIELD:str(dest/'bindings.json'),
            'rfd4_input_closure':str(dest)}


def generation_settings(mode='generate'):
    return {'model_id':MODEL_ID,'mode':mode,'parameter_schema':parameter_schema(mode),
            'request_field':REQUEST_FIELD,'contract':CONTRACT}
