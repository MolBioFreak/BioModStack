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
SOURCE_SLOT_FIELDS = {
    'slots': ('structure', 'items'), 'slot': ('structure', None),
    'resume_slot': ('file', None), 'adapter_slot': ('file', None),
    'archive_slot': ('file', None), 'ligand_params_slots': ('file', 'items'),
    'off_target_slots': ('structure', 'additionalProperties'),
}


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


def selected_oracle_assets(mode, params):
    """Selected native dependencies, not the workflow-pack family union.

    These logical names belong to the existing image/weight/runtime-data owners.
    In particular OF3 is the *ported* AF3-runtime layout, never openfold3/*.pt.
    No resolution, acquisition, or filesystem inspection occurs here.
    """
    from scripts.lib.rfd4_proteina_contract import _PROFILE
    request = normalize_request(params[REQUEST_FIELD], mode)
    dependencies = selected_dependencies(request)
    families = {('af2' if profile == 'af2' else
                 dependencies.get('oracle_parameter_family') or profile)
                for profile in dependencies['active_oracle_profiles']}
    rows = []
    for family in sorted(families):
        prefix = 'AF2' if family == 'af2' else 'AF3'
        image = 'af2-open.sif' if family == 'af2' else 'af3-open.sif'
        rows.extend((
            {'kind': 'image', 'relative_path': image, 'family': family, 'environment': prefix + '_IMAGE'},
            {'kind': 'weights', 'relative_path': ('alphafold/params' if family == 'af2' else 'rfd4-oracles/' + family),
             'family': family, 'environment': prefix + '_MODEL_DIR', 'bind_parent': family == 'af2'},
        ))
    # Apply the installed metric tag rules to selected Foldseek consumers too.
    tags = set(request.get('settings', {}).get('metrics_tags', []))
    explicit = {name for checkpoint in (dependencies['search'].get('checkpoints') or {}).values()
                for name in checkpoint.get('metrics', [])}
    metrics = []
    defaults = {'inline_metrics': 'all_inline_metrics'}
    for section, selection in {**{k: {'profile': v} for k, v in defaults.items()},
                               **request.get('providers', {})}.items():
        declarations = _PROFILE['groups']['metrics/' + section][selection['profile']]['schema']['properties']
        for name, declaration in declarations.items():
            values = {k: v.get('default') for k, v in declaration.get('properties', {}).items()}
            values.update(selection.get('overrides', {}).get(name, {}))
            metrics.append((name, values.get('_target_', ''), values))
    metrics.extend((e['name'], e['implementation'], e['kwargs']) for e in request.get('metric_extensions', []))
    foldseek = cath = False
    for name, implementation, values in metrics:
        groups = [set(values.get(key) or []) for key in ('required_tags_all', 'required_tags_any', 'prohibited_tags')]
        if name in explicit:
            groups = [{t for t in group if t.startswith(('is_refold:', 'exists_refold:', 'oracle:'))} for group in groups]
        all_tags, any_tags, prohibited = groups
        if not tags.issuperset(all_tags) or (any_tags and not tags.intersection(any_tags)) or tags.intersection(prohibited):
            continue
        if implementation.endswith('.FoldseekNoveltyMetric'):
            foldseek = True
        if implementation.endswith('.CathMetric') and values.get('annotator') != 'proteina_classifier':
            foldseek = cath = True
    if foldseek:
        rows.append({'kind': 'runtime_data', 'relative_path': 'rfd4-oracles/foldseek/bin/foldseek',
                     'family': 'foldseek', 'environment': 'FOLDSEEK_EXEC'})
    if cath:
        # A prefix is consumed by Foldseek; retain its complete companion tree.
        rows.extend((
            {'kind': 'reference_database', 'relative_path': 'rfd4-oracles/cath',
             'family': 'foldseek', 'environment': 'CATH_FOLDSEEK_DB', 'member': 'cath_v4_4_0_s95_db'},
            {'kind': 'runtime_data', 'relative_path': 'rfd4-oracles/CathDomainList.S95.v4.4.0',
             'family': 'foldseek', 'environment': 'CATH_DOMAIN_LIST'},
        ))
    return tuple(rows)


def resolve_oracle_runtime(mode, params, *, resolved_assets=None, image_environment=None):
    """Bind only selected assets through existing owners (never browser paths).

    resolved_assets maps bundle logical paths to worker immutable destinations.
    With no mapping the existing local managed image and storage owners resolve
    the same declarations. Mutable caches are deliberately NOT retained here.
    """
    rows = selected_oracle_assets(mode, params)
    if not rows:
        return {}
    prefixes = {'image': 'containers', 'weights': 'weights', 'runtime_data': 'data',
                'reference_database': 'data'}
    if resolved_assets is None:
        from paths import get_container_dir, get_weights_root, get_data_root
        from services.remote_execution.images import image_reference, image_environment_key
        roots = {'weights': get_weights_root(), 'runtime_data': get_data_root(),
                 'reference_database': get_data_root()}
    result = {}
    for row in rows:
        logical = prefixes[row['kind']] + '/' + row['relative_path']
        if resolved_assets is not None:
            path = Path(resolved_assets[logical])
        elif row['kind'] == 'image':
            if image_environment is not None and image_environment_key(row['relative_path']) in image_environment:
                path = Path(image_environment[image_environment_key(row['relative_path'])])
            else:
                path, _ = image_reference(row['relative_path'], get_container_dir(), {})
        else:
            path = roots[row['kind']] / row['relative_path']
        if row.get('bind_parent'):
            # Official AF2 calls get_model_haiku_params(data_dir=...), which
            # appends params/. Reuse the existing AlphaFold parameter tree.
            path = path.parent
        if row.get('member'):
            path = path / row['member']
        result.setdefault(row['family'], {})[row['environment']] = str(path)
    return result


def retained_oracle_runtime(mode, params, runtime=None):
    if runtime is None:
        return resolve_oracle_runtime(mode, params)
    result = {}
    for row in selected_oracle_assets(mode, params):
        family, key = row['family'], row['environment']
        if key in runtime.get(family, {}):
            result.setdefault(family, {})[key] = str(runtime[family][key])
    return result


def prepare_for_job(mode,params,directory,*,resolved_sources,allowed_roots,runtime_identity=None,source_identities=None,oracle_runtime=None):
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
             'runtime_identity':{**copy.deepcopy(runtime_identity or {}),'sources':compiled['sources']},
             'oracle_runtime':retained_oracle_runtime(mode, params, oracle_runtime)}
    (dest/'request.json').write_text(json.dumps(payload,indent=2)+'\n')
    (dest/'bindings.json').write_text(json.dumps(bindings,indent=2)+'\n')
    return {**normalize_params(mode,params),PREPARED_FIELD:str(dest/'request.json'),BINDINGS_FIELD:str(dest/'bindings.json'),
            'rfd4_input_closure':str(dest)}


def bind_for_job(mode, params, directory, *, oracle_runtime=None):
    """Pure generated inputs; retained source closures stay with prepare_for_job."""
    from component_runtime import GeneratedInput
    compiled = compile_request(mode, params)
    result = normalize_params(mode, params)
    if result.get('rfd4_input_closure'):
        return result, ()
    relative = Path(DIRECTORY) / compiled['request_identity']
    payload = {'request': compiled['request'], 'request_identity': compiled['request_identity'],
               'runtime_identity': {'sources': compiled['sources']},
               'oracle_runtime': retained_oracle_runtime(mode, params, oracle_runtime)}
    generated = tuple(GeneratedInput((relative / name).as_posix(), (json.dumps(value, indent=2) + '\n').encode())
                      for name, value in (('request.json', payload), ('bindings.json', {})))
    result.update({PREPARED_FIELD: str(Path(directory) / relative / 'request.json'),
                   BINDINGS_FIELD: str(Path(directory) / relative / 'bindings.json'),
                   'rfd4_input_closure': str(Path(directory) / relative)})
    return result, generated


def source_slots_for_params(mode, params):
    return source_slots(normalize_request(params[REQUEST_FIELD], mode))


def generation_settings(mode='generate'):
    from services.native_generation_sources import native_sources_schema, source_slot_schema
    from scripts.lib.rfd4_proteina_contract import _PROFILE
    # Readback only: never insert inherited values into the sparse request.
    profiles = {group: {name: copy.deepcopy(row['effective_generation'])
                        for name, row in _PROFILE['groups'][group].items()
                        if 'effective_generation' in row}
                for group in ('generation', 'recycle_strategy')}
    return {'native_profile_defaults': {
                'authority': 'selected image Hydra composition',
                'runtime_image_sha256': _PROFILE.get('runtime_image_sha256'),
                'reviewed_source_revision': _PROFILE.get('reviewed_source_revision'),
                'source_revision': _PROFILE.get('source_revision'),
                'default_generation': copy.deepcopy(_PROFILE['default_effective_generation']),
                'generation_by_group': profiles,
                'scope': 'Each entry changes only its named group; combined selections and explicit overrides are resolved by native Hydra at preparation.',
            } if mode == 'generate' else None,
            'native_sources_schema': native_sources_schema(),
            'model_id':MODEL_ID,'mode':mode,'parameter_schema':source_slot_schema(parameter_schema(mode), MODEL_ID),
            'request_field':REQUEST_FIELD,'contract':CONTRACT}
