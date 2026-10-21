"""Indexed RFD4 readback; never reparse coordinates for scalar pages."""
from __future__ import annotations
import copy
import json
from pathlib import Path

RESULT_CONTRACT='rfd4_proteina_v1'
MANIFEST_RELATIVE_PATH='collected/rfd4_proteina/manifest.json'


def read_manifest(path):
    from services.ligandmpnn_interface_publication import regular_bytes
    document=json.loads(regular_bytes(Path(path)))
    if document.get('contract')!=RESULT_CONTRACT:raise ValueError('Not an RFD4 native publication')
    return document


def page_records(manifest,*,offset=0,limit=100,population=None):
    if offset<0 or limit<1:raise ValueError('Invalid result page')
    records=[r for r in manifest['records'] if population is None or r['population']==population]
    return {'contract':RESULT_CONTRACT,'records':copy.deepcopy(records[offset:offset+limit]),
            'offset':offset,'limit':limit,'total':len(records),'has_more':offset+limit<len(records),
            'population':population,'population_completeness':manifest['population_completeness'],
            'execution_status':manifest['execution_status'],'observation_errors':manifest.get('observation_errors',[])}


def artifact_members(manifest):
    """Existing ingester registers these exact relative paths with JobArtifact once."""
    for member in manifest['artifacts']:
        path=Path(member['path'])
        if path.is_absolute() or '..' in path.parts:raise ValueError('Publication member escapes native root')
        yield copy.deepcopy(member)


def exact_document_record(manifest,example_id,document):
    matches=[r for r in manifest['records'] if r['example_id']==example_id and r['document']==document]
    if len(matches)!=1:raise ValueError('Native example/document does not identify exactly one record')
    return copy.deepcopy(matches[0])
