"""Verified retained family alignment readers shared by routes and workers."""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath

from fastapi import HTTPException

from experiment_models import (
    ExperimentArtifact,
    ExperimentExternalEntityReceipt,
    ExperimentResource,
)
from experiment_services import ValidationFailure
from services.family_msa.publication import CustodyError, retained_path
from services.family_msa.read_model import alignment_document
from services.global_experiments.result_surfaces import (
    _acknowledgement,
    _validate_persisted_authority,
)


def unavailable(exc):
    return HTTPException(409, detail='Alignment result unavailable or corrupt')


async def project_result_data(session, project_id, receipt_id):
    """Verified retained read for authorized routes and bound owned workers."""
    receipt = await session.get(ExperimentExternalEntityReceipt, receipt_id)
    if not receipt or receipt.workspace_id != project_id or receipt.entity_kind not in ('family_msa_job_result', 'family_msa_import'):
        raise HTTPException(404, detail='Result not found')
    if receipt.entity_kind == 'family_msa_import':
        try:
            metadata = json.loads(receipt.acknowledgement_json)
            entries = metadata['artifacts']
            if metadata['schema'] != 'bms.family-msa.import.v1' or not isinstance(entries, list):
                raise ValueError('invalid import receipt')
        except (ValueError, KeyError, TypeError) as exc:
            raise unavailable(exc) from exc
        async def read_import(ident):
            entry = next((x for x in entries if x['id'] == ident), None)
            if entry is None:
                raise HTTPException(404, detail='Artifact not found')
            resource = await session.get(ExperimentResource, ident)
            artifact = await session.get(ExperimentArtifact, ident)
            if not resource or resource.workspace_id != project_id or resource.lifecycle_owner_id != receipt.id or not artifact or artifact.blob_sha256 != entry['sha256'] or artifact.logical_role != entry['role']:
                raise unavailable(None)
            try:
                return (await retained_path(session, artifact)).read_bytes()
            except (CustodyError, OSError) as exc:
                raise unavailable(exc) from exc
        return entries, read_import
    try:
        ack = _acknowledgement(receipt)
        _validate_persisted_authority(receipt, ack)
        metadata = ack['metadata']
        entries = metadata.get('retained_artifacts')
        if not entries:
            # Manual Save retains resources beside the immutable source receipt.
            # Derive only the canonical custody identities, never source paths.
            from services.family_msa.publication import manual_result_inventory
            entries = manual_result_inventory(receipt.id, project_id, metadata)
            for entry in entries:
                resource = await session.get(ExperimentResource, entry['resource_id'])
                if not resource or resource.workspace_id != project_id or resource.lifecycle_owner_id != receipt.id:
                    raise ValueError('manual result custody unavailable')
        if not isinstance(entries, list) or not entries or len({x['resource_id'] for x in entries}) != len(entries):
            raise ValueError('invalid retained inventory')
        source = {x['id']: x for x in metadata['artifacts']}
        if set(x['source_artifact_id'] for x in entries) != set(source) | {metadata['manifest_artifact_id']}:
            raise ValueError('incomplete retained inventory')
        for entry in entries:
            expected = 'result_manifest' if entry['source_artifact_id'] == metadata['manifest_artifact_id'] else source[entry['source_artifact_id']]['role']
            if entry['role'] != expected:
                raise ValueError('role mismatch')
    except (ValidationFailure, KeyError, TypeError, ValueError) as exc:
        # Do not disclose malformed stored authority to callers.
        raise unavailable(exc) from exc
    async def read(ident):
        entry = next((x for x in entries if x['resource_id'] == ident), None)
        if not entry:
            raise HTTPException(404, detail='Artifact not found')
        resource = await session.get(ExperimentResource, ident)
        artifact = await session.get(ExperimentArtifact, ident)
        if not resource or resource.workspace_id != project_id or not artifact or artifact.logical_role != entry['role']:
            raise HTTPException(404, detail='Artifact not found')
        expected = receipt.content_digest if entry['role'] == 'result_manifest' else source[entry['source_artifact_id']]['sha256']
        if artifact.blob_sha256 != expected:
            raise unavailable(None)
        try:
            path = await retained_path(session, artifact)
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                raise CustodyError('changed during read')
            return data
        except (CustodyError, OSError) as exc:
            raise unavailable(exc) from exc
    return [{'id': x['resource_id'], 'role': x['role'], 'logical_path': source.get(x['source_artifact_id'], {}).get('logical_path')} for x in entries], read


def native_output_check(requested, fasta, row_map):
    from bms_alignment.models import AlignmentRequest
    from bms_alignment.observations import observe_alignment_text, validate_alignment_output
    try:
        native_request = AlignmentRequest.model_validate(requested)
        native_roster = native_request.roster.model_copy(deep=True)
        if row_map is not None:
            if (not isinstance(row_map, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                                    for key, value in row_map.items())
                    or len(set(row_map.values())) != len(row_map)
                    or set(row_map.values()) != {row.row_id for row in native_roster.rows}):
                raise ValueError('row map does not match the input sequences')
            by_source = {value: key for key, value in row_map.items()}
            for row in native_roster.rows:
                row.row_id = by_source[row.row_id]
        observations = observe_alignment_text(fasta, native_roster,
            allow_reverse_complement=native_request.settings.engine == 'mafft' and native_request.settings.direction is not None)
        return validate_alignment_output(native_request, observations)
    except (ValueError, KeyError, TypeError) as exc:
        return {'status': 'unavailable', 'errors': [f'Output check could not run: {exc}']}


async def document(entries, read, artifact_id):
    selected = next((x for x in entries if x['id'] == artifact_id and x['role'] == 'alignment'), None)
    receipts = [x for x in entries if x['role'] == 'receipt']
    if not selected or len(receipts) != 1:
        raise HTTPException(404, detail='Alignment document not found')
    try:
        receipt = json.loads(await read(receipts[0]['id']))
        requested = receipt['requested']
        roster = requested['roster']['rows']
        fasta = (await read(artifact_id)).decode('utf-8')
        parent = PurePosixPath(selected.get('logical_path') or '').parent
        maps = [x for x in entries if x['role'] == 'row_map' and PurePosixPath(x.get('logical_path') or '').parent == parent]
        if len(maps) > 1:
            raise ValueError('ambiguous row map')
        row_map = json.loads(await read(maps[0]['id'])) if maps else None
        settings = requested.get('settings', {})
        if not isinstance(settings, dict):
            raise ValueError('invalid native alignment settings')
        output_check = None
        if 'settings' in requested:
            # Report whether the engine kept every input row and residue. The
            # result stays readable either way; the reader sees the finding.
            output_check = native_output_check(requested, fasta, row_map)
        direction = settings.get('direction') if settings.get('engine', 'mafft') == 'mafft' else None
        projected = alignment_document(result_id=artifact_id, fasta=fasta, roster=roster, row_map=row_map,
                                       molecule=requested['roster'].get('molecule'), mafft_direction=direction).model_dump()
        return {**projected, 'output_check': output_check}
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise unavailable(exc) from exc


async def comparison_roster(entries, read, artifact_id):
    from bms_alignment.models import Row, SequenceSet
    doc = await document(entries, read, artifact_id)
    receipt_entry = next(x for x in entries if x['role'] == 'receipt')
    receipt = json.loads(await read(receipt_entry['id']))
    roster = receipt['requested']['roster']
    original = {row['row_id']: row for row in roster['rows']}
    rows = []
    for aligned in doc['rows']:
        source = original[aligned['row_id']]
        # Reuse the document's verified projection, including explicit native
        # reverse-complement markers, rather than calculating a second map.
        positions = aligned['source_positions']
        rows.append(Row(row_id=aligned['row_id'], header=aligned['label'], sequence=aligned['aligned'],
                        source_member_id=source.get('source_member_id'), occurrence_id=source.get('occurrence_id'),
                        source_offset=source.get('source_offset') if aligned['source_orientation'] == 'forward' and source.get('source_positions') is None else None,
                        source_positions=positions, transformation_provenance=source.get('transformation_provenance')))
    return SequenceSet(molecule=roster['molecule'], rows=rows)
