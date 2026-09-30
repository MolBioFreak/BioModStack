"""Download-only wire semantics and retained operation compatibility."""
import json

import pytest
from pydantic import ValidationError

from services.remote_execution.contracts import PreloadProgress, ProvisionPreview


def preview_wire():
    return dict(selection={'kind': 'image', 'model_id': 'protenix'},
                preview_sha256='a' * 64, artifacts=[], total_bytes=0)


def test_new_preview_promises_downloads_only():
    wire = ProvisionPreview.model_validate(preview_wire()).model_dump(mode='json')
    assert wire['scope'] == 'download_only'
    assert wire['scientific_ready'] is False


@pytest.mark.parametrize('scope', ['download_only', 'managed_asset_activation'])
def test_strict_preview_reader_preserves_supported_historical_scope(scope):
    wire = dict(preview_wire(), scope=scope)
    parsed = ProvisionPreview.model_validate_json(json.dumps(wire))
    assert parsed.model_dump(mode='json')['scope'] == scope
    assert parsed.scientific_ready is False


@pytest.mark.parametrize('extra', [dict(scope='unknown'), dict(scientific_ready=True), dict(certified=True)])
def test_preview_does_not_accept_unknown_contract_or_certification(extra):
    with pytest.raises(ValidationError):
        ProvisionPreview.model_validate(dict(preview_wire(), **extra))


def test_historical_download_completion_keeps_terminal_protocol_and_timestamp():
    wire = dict(operation_id='old', source_revision='a' * 40, source_tree='b' * 40,
                request_sha256='c' * 64, phase='source_download_ready', message='Historical completion',
                started_at='2026-01-01T00:00:00Z', updated_at='2026-01-01T01:00:00Z')
    parsed = PreloadProgress.model_validate(wire)
    assert parsed.phase == 'source_download_ready'
    assert parsed.model_dump(mode='json')['updated_at'] == '2026-01-01T01:00:00Z'
