"""Family image provision plumbing with segregated inert transport fixtures."""
import json
from pathlib import Path

import pytest

from test_provision_cli import cli, identity


@pytest.mark.parametrize('cli', ['family_msa_protein', 'family_msa_nucleic'], indirect=True)
def test_family_image_provision_resume_and_registered_selection(cli, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    from publish_runtime_images import publish_references
    from lib.runtime_image_lifecycle import load_state

    run, handler, root, env, fixture = cli
    data = json.loads(fixture.read_text())
    data['entries'] = data['entries'][:1]
    data['entries'][0]['dependency']['relative_path'] = 'msa_engines.sif'
    fixture.write_text(json.dumps(data))
    args = identity(run)
    code, report = run('provision', *args)
    assert code == 0 and not report['ready'] and not report['registered']
    artifact = report['models'][0]['receipt']['artifacts'][0]
    image = Path(artifact['path'])
    assert image.read_bytes() == handler.payload
    requests = list(handler.requests)
    assert run('resume', *args)[0] == 0
    assert handler.requests == requests
    # The transport segregates test-only bytes; no production store is touched.
    store = root / 'images/.image-store/test-fixtures-not-scientific-assets'
    selector = 'BMS_RUNTIME_IMAGE_MSA_ENGINES_SIF'
    publish_references(store, 'production', {selector: {
        'source': str(image), 'sha256': artifact['verification']['sha256']}})
    state = load_state(store)
    selected = state['releases'][state['current']['production']]['images'][selector]
    assert selected['path'] == str(image)
    assert selected['sha256'] == artifact['verification']['sha256']
