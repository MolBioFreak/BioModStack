"""Model-scoped source bindings; shared structure custody remains authoritative."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, TypeAdapter
from services.binder_source_materialization import StructureSourceRequest


class NativeFileSource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['file']
    path: str
    expected_sha256: str | None = None


NativeSourceBinding = StructureSourceRequest | NativeFileSource
NativeSources = dict[str, NativeSourceBinding]


def native_sources_schema():
    # The shared schema owner completes StructureSourceRequest's CandidateRef
    # forward reference; discovery may also be called without importing routers.
    import schemas  # noqa: F401
    return TypeAdapter(NativeSources).json_schema()


def source_slot_schema(schema, model):
    """Decorate consumed slot fields; scientific schema/defaults remain unchanged."""
    import copy
    from services import rfd4_proteina, complexa
    from services import laproteina_evaluation
    owner = laproteina_evaluation if model == 'laproteina' else rfd4_proteina if model == 'rfd4_proteina' else complexa
    result = copy.deepcopy(schema)
    def mark(node, kind, container):
        if container:
            if isinstance(node.get(container), dict):
                node[container]['x-native-source-kind'] = kind
        else:
            node['x-native-source-kind'] = kind
        for key in ('anyOf', 'oneOf'):
            for branch in node.get(key, []):
                if branch.get('type') != 'null':
                    mark(branch, kind, container)

    def walk(node):
        if not isinstance(node, dict):
            return
        for name, child in node.get('properties', {}).items():
            if name in owner.SOURCE_SLOT_FIELDS:
                kind, container = owner.SOURCE_SLOT_FIELDS[name]
                mark(child, kind, container)
            walk(child)
        for key in ('items', 'additionalProperties'):
            walk(node.get(key))
        for key in ('oneOf', 'anyOf', 'allOf'):
            for child in node.get(key, []):
                walk(child)
        for child in node.get('$defs', {}).values():
            walk(child)
    walk(result)
    return result


def selected_model(job):
    return (job.params.get('generator') if job.model_id == 'protein_modification_experimental'
            and job.mode == 'de_novo_design' else job.model_id)


def native_review(job):
    model = selected_model(job)
    mode = job.mode if job.model_id == model else 'generate'
    if model == 'rfd4_proteina':
        from services.rfd4_proteina import compile_request, REQUEST_FIELD
        compiled = compile_request(mode, job.params)
        return {'model_id': model, 'requested': job.params[REQUEST_FIELD],
                'native_request': compiled['request'], 'native_overlay': compiled['native_overlay']}
    if model == 'complexa':
        from services.complexa import normalize_job_request, REQUEST_FIELD
        return {'model_id': model, 'requested': job.params[REQUEST_FIELD],
                'native_request': normalize_job_request(mode, job.params[REQUEST_FIELD])}
    return None


async def prepare_native_sources(job, session):
    """Called only by the ordinary preview/Job selected-source preparation seam.

    Never trust supplied runtime closures: rebuild them from the typed bindings.
    Retain originals in the public map; record consumed derivatives separately.
    """
    model = selected_model(job)
    if model == 'laproteina' and (job.model_id != 'laproteina' or job.mode != 'evaluate'):
        return
    if model not in {'rfd4_proteina', 'complexa', 'laproteina'}:
        return
    from paths import resolve_allowed_path, get_allowed_roots, to_allowed_relative
    from routers.files import (materialize_structure, get_governed_ngs_result_roots,
        _sha256_regular_file_nofollow, _under_persisted_ngs_root, _reject_governed_ngs_artifact)
    from services.binder_source_materialization import (resolve_structure_source,
        materialize_source_bytes, structure_suffix)
    from scripts.lib.portable_inputs import _contained
    from services import rfd4_proteina, complexa

    from services import laproteina_evaluation
    owner = laproteina_evaluation if model == 'laproteina' else rfd4_proteina if model == 'rfd4_proteina' else complexa
    mode = job.mode if job.model_id == model else 'generate'
    try:
        slots = owner.source_slots_for_params(mode, job.params)
        if not slots and model != 'rfd4_proteina':
            return
        bindings = TypeAdapter(NativeSources).validate_python(job.params.get('native_sources', {}))
        missing = set(slots) - bindings.keys()
        if missing:
            raise ValueError('Missing native source bindings: ' + ', '.join(sorted(missing)))
        governed = await get_governed_ngs_result_roots(session)
        roots = list(get_allowed_roots().values())
        inputs = resolve_allowed_path('inputs') / 'native-generation'
        inputs.mkdir(parents=True, exist_ok=True)
        resolved, identities, retained = {}, {}, {}
        for slot in slots:
            binding = bindings[slot]
            if (model == 'complexa' and isinstance(binding, NativeFileSource)
                    and slot in complexa.structure_slots_for_params(mode, job.params)):
                binding = StructureSourceRequest(path=binding.path, expected_sha256=binding.expected_sha256)
            if isinstance(binding, NativeFileSource):
                path = resolve_allowed_path(binding.path)
                _contained(path, roots)
                if _under_persisted_ngs_root(path, governed):
                    raise HTTPException(403, 'Access denied to this source')
                _reject_governed_ngs_artifact(path)
                members = []
                def collect(member, relative, ancestors=()):
                    actual = _contained(member, roots)
                    if _under_persisted_ngs_root(actual, governed):
                        raise HTTPException(403, 'Access denied to this source')
                    _reject_governed_ngs_artifact(actual)
                    if actual.is_dir():
                        if actual in ancestors:
                            raise ValueError('Source directory contains a symlink cycle')
                        for child in sorted(actual.iterdir()):
                            collect(child, relative / child.name, (*ancestors, actual))
                    else:
                        digest, size = _sha256_regular_file_nofollow(actual)
                        members.append((relative, actual, digest, size))
                collect(path, Path('.'))
                ledger = [[str(relative), digest, size] for relative, _, digest, size in members]
                digest = (hashlib.sha256(json.dumps(ledger).encode()).hexdigest() if path.is_dir() else ledger[0][1])
                if binding.expected_sha256 is not None and binding.expected_sha256 != digest:
                    raise ValueError('Selected source bytes changed')
                target = inputs / 'files' / digest / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                if path != target:
                    if path.is_dir():
                        target.mkdir(exist_ok=True)
                        for relative, actual, _, _ in members:
                            destination = target / relative
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(actual, destination)
                    else:
                        shutil.copyfile(path, target)
                resolved[slot] = str(target)
                identities[slot] = {'kind': 'file', 'sha256': digest, 'members': ledger}
                retained[slot] = binding.model_copy(update={'path': to_allowed_relative(target),
                    'expected_sha256': digest}).model_dump(mode='json', exclude_none=True)
            else:
                # A PDB-only consumer requests a checked derivative, not renamed CIF.
                source = binding.model_copy(update={'output_format': 'pdb'}) if model == 'complexa' else binding
                path, identity = await resolve_structure_source(source, session)
                if source.path is None or not (path.parent.name.startswith('structure-') and source.expected_sha256):
                    prepared = await materialize_structure(source, session, governed_roots=governed)
                    source = StructureSourceRequest.model_validate(prepared['source_structure'])
                    path, identity = await resolve_structure_source(source, session)
                if _under_persisted_ngs_root(path, governed):
                    raise HTTPException(403, 'Access denied to this structure')
                _reject_governed_ngs_artifact(path)
                digest, _ = _sha256_regular_file_nofollow(path)
                for expected in (identity.get('artifact_sha256'), source.expected_sha256):
                    if expected is not None and digest != expected:
                        raise ValueError('Selected source bytes differ from the exact document digest')
                destination = path.parent / f'native-{source.output_format}-{source.model_number}'
                destination.mkdir(exist_ok=True)
                with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
                    raw = stream.read()
                prepared = materialize_source_bytes(raw, structure_suffix(path), destination,
                    output_format=source.output_format, model_number=source.model_number,
                    verified_original_sha256=digest)
                resolved[slot] = str(resolve_allowed_path(prepared['path']))
                identities[slot] = {'source_identity': identity, 'original_sha256': digest,
                    'native_sha256': prepared['native_sha256'], 'model_number': prepared['model_number'],
                    'consumed_sha256': prepared['sha256'], 'consumed_format': prepared['format'],
                    'derivative': prepared}
                retained[slot] = source.model_dump(mode='json', exclude_none=True)
        # Preserve extra authoring bindings without promoting them into runtime inputs.
        job.params['native_sources'] = {**{k: v.model_dump(mode='json', exclude_none=True)
            for k, v in bindings.items()}, **retained}
        normalized_request = (owner.compile_request(mode, job.params)['request'] if model == 'rfd4_proteina'
            else owner.normalize_job_request(mode, job.params[owner.REQUEST_FIELD]))
        key = hashlib.sha256(json.dumps({'model': model, 'mode': mode,
            'sources': retained, 'request': normalized_request}, sort_keys=True).encode()).hexdigest()
        directory = inputs / key
        job.params = owner.prepare_for_job(mode, job.params, directory,
            resolved_sources=resolved, allowed_roots=roots, source_identities=identities)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
