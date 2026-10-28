"""Standalone HTTP wire coverage, without importing the BMS application."""
import io
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError, TypeAdapter
from bms_alignment import http, cli


class Transport:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        value = self.response if isinstance(self.response, bytes) else json.dumps(self.response).encode()
        return io.BytesIO(value)


def client(response):
    sdk = http.AlignmentClient('https://example.test/base', headers={'Authorization': 'Bearer test'})
    transport = Transport(response)
    sdk._opener = transport
    return sdk, transport


def state():
    return http.SharedState(marks=[http.Mark(id='m', label='source', coordinate=http.SourcePositions(scope='source_positions', source_member_id='member', occurrence_id='repeat', start=0, end=1))],
        views=[http.View(id='v', name='View', artifact_id='a', selected_column=0, hidden_row_ids=[], selected_row_id='row', viewer='biojs')],
        comparisons=[http.Comparison(id='c', name='comparison', left_artifact_id='a', right_artifact_id='b', method='source', report={'rows': []})])


def revision():
    return dict(id='shared', project_id='p', head_generation=0, revision_id='v', revision_number=1, payload_sha256='hash', state=state().model_dump(by_alias=True))


def test_closed_requests_and_nested_state():
    for contract, payload in [(http.FreezeRequest, {'ordinals': [0]}), (http.BrowserExport, {}), (http.SharedSave, {'expected_head_generation': 0, 'state': {}})]:
        with pytest.raises(ValidationError):
            contract.model_validate(dict(payload, unsupported=True))
    with pytest.raises(ValidationError):
        http.SharedSave(expected_head_generation=False, state=state())
    with pytest.raises(ValidationError):
        http.View(id='v', name='v', artifact_id='a', unsupported=True)
    with pytest.raises(ValidationError):
        TypeAdapter(http.ManagedOperation).validate_python({'operation': 'export', 'molecule': 'dna', 'domain_experiment_id': 'd', 'inputs': {'left_receipt_id': 'r'}})


def test_shared_revision_read_create_save_preserves_zero_and_anchors():
    sdk, wire = client(revision())
    created = sdk.create_shared_state('p/a', http.SharedCreate(name='state', domain_experiment_id='domain', state=state()))
    assert created.state.views[0].selected_column == 0
    body = json.loads(wire.requests[-1].data)
    assert body['state']['schema'] == 'bms.family-msa.shared-state.v1'
    assert body['state']['marks'][0]['coordinate']['occurrence_id'] == 'repeat'
    assert wire.requests[-1].full_url.endswith('/projects/p%2Fa/shared-state')
    sdk.save_shared_state('p', 'dataset/id', http.SharedSave(expected_head_generation=0, state=state()))
    assert wire.requests[-1].method == 'PUT'
    assert json.loads(wire.requests[-1].data)['expected_head_generation'] == 0
    sdk.shared_state('p', 'dataset/id', revision_id='rev?x')
    assert parse_qs(urlsplit(wire.requests[-1].full_url).query) == {'revision_id': ['rev?x']}


def test_resources_freeze_resolve_preserves_unknown_and_explicit_empty():
    base = dict(project_id='p', dataset_id='d', revision_id='r', revision_sha256='h')
    sdk, wire = client(dict(base, offset=0, limit=50, total=1, next_offset=None, items=[{'ancestry': {'exact': True}}]))
    assert sdk.resources('p', 'd', 'r').items[0]['ancestry'] == {'exact': True}
    wire.response = dict(base, members=[])
    sdk.freeze('p', 'd', 'r', http.FreezeRequest(ordinals=[]))
    assert json.loads(wire.requests[-1].data) == {'ordinals': []}
    sdk.freeze('p', 'd', 'r', http.FreezeRequest(ordinals=None))
    assert json.loads(wire.requests[-1].data) == {'ordinals': None}
    wire.response = dict(base, ordinal=0, status='unresolved', reason='source unavailable', sequence=None)
    result = sdk.resolve('p', 'd', 'r', http.ResolveRequest(kind='design', ordinal=0, start=0, chain='A/B'))
    assert result.sequence is None and result.reason == 'source unavailable'
    assert parse_qs(urlsplit(wire.requests[-1].full_url).query) == {'start': ['0'], 'chain': ['A/B']}


def test_import_result_document_and_binary_export():
    raw = b'>duplicate\nAC-D\n>duplicate\nACED\n'
    sdk, wire = client({'receipt_id': 'r', 'replayed': False, 'shared_state': 'none'})
    sdk.import_alignment('p', raw, http.AlignmentImport(format='fasta', molecule='protein', replay_key='same/key'))
    assert wire.requests[-1].data == raw
    assert parse_qs(urlsplit(wire.requests[-1].full_url).query)['replay_key'] == ['same/key']
    wire.response = {'items': [{'receipt_id': 'r', 'entity_kind': 'family_msa_import', 'availability': 'available'}], 'next_cursor': 'next'}
    assert sdk.receipts('p', after='cursor/a').next_cursor == 'next'
    wire.response = {'artifacts': [{'id': 'a', 'role': 'alignment'}]}
    assert sdk.result('p', 'r').artifacts[0].id == 'a'
    assert sdk.job_result('j').artifacts[0].id == 'a'
    wire.response = {'result_id': 'a', 'rows': [{'row_id': 'row', 'label': 'duplicate', 'aligned': 'AC-D', 'source_positions': [0, 1, None, 2]}]}
    assert sdk.document('p', 'r', 'a').rows[0].source_positions == [0, 1, None, 2]
    assert sdk.job_document('j', 'a').result_id == 'a'
    wire.response = raw
    assert sdk.artifact('p', 'r', 'a') == raw
    assert sdk.job_artifact('j', 'a') == raw
    assert sdk.export_project_zip('p', 'r', http.BrowserExport(shared_state_ids=['s'])) == raw
    assert json.loads(wire.requests[-1].data) == {'shared_state_ids': ['s']}
    assert wire.requests[-1].get_header('Authorization') == 'Bearer test'


def test_managed_operation_typed_submit_poll_and_artifact():
    status = dict(job_id='j', project_id='p', operation='compare', status='queued', queue_status='queued', request_dataset_id='d', result_url='/api/status', artifact_url=None, error_message=None)
    sdk, wire = client(status)
    payload = http.CompareOperation(molecule='protein', domain_experiment_id='d', inputs=http.ComparisonInput(left_receipt_id='l', left_artifact_id='a', right_receipt_id='r', right_artifact_id='b'))
    assert sdk.submit_operation('p', payload).job_id == 'j'
    assert wire.requests[-1].full_url.endswith('/projects/p/operations')
    assert json.loads(wire.requests[-1].data)['operation'] == 'compare'
    assert sdk.operation('p', 'j').status == 'queued'
    wire.response = b'artifact bytes'
    assert sdk.operation_artifact('p', 'j') == b'artifact bytes'


def test_binary_errors_are_typed_and_do_not_redirect():
    sdk, wire = client(HTTPError('https://example.test', 409, 'conflict', {}, io.BytesIO(b'stale head')))
    with pytest.raises(http.BmsHttpError) as failure:
        sdk.artifact('p', 'r', 'a')
    assert failure.value.status == 409 and failure.value.body == b'stale head'
    assert http._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.test') is None


def test_cli_typed_workflow_and_binary_file(tmp_path, monkeypatch, capsys):
    wire = Transport(revision())
    monkeypatch.setattr(http, 'build_opener', lambda *_: wire)
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'expected_head_generation': 0, 'state': state().model_dump(by_alias=True)}))
    monkeypatch.setattr('sys.argv', ['bms-alignment', 'api', '--url', 'https://example.test', 'save-shared-state', 'p', 's', str(request)])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)['state']['views'][0]['selected_column'] == 0
    wire.response = b'\x00ZIP\xff'
    destination = tmp_path / 'bundle.zip'
    monkeypatch.setattr('sys.argv', ['bms-alignment', 'api', '--url', 'https://example.test', 'operation-artifact', 'p', 'j', str(destination)])
    assert cli.main() == 0
    assert destination.read_bytes() == b'\x00ZIP\xff'
