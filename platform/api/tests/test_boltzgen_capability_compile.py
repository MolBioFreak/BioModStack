"""Compile public requests through the real model-owned command owner, CPU only."""
import json
from pathlib import Path
import pytest
from services.boltzgen_request_compatibility import BOLTZGEN_GENERATION_PROTOCOLS, normalize_boltzgen_generation_request
from services.nextflow import compile_nextflow_invocation

@pytest.mark.parametrize('mode,protocol', BOLTZGEN_GENERATION_PROTOCOLS.items())
def test_capability_compile(mode, protocol, tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_HOME', str(Path(__file__).resolve().parents[3]))
    params = normalize_boltzgen_generation_request(mode, {'alpha': 0, 'filter_biased': False, 'min_plddt': None})
    from scripts.lib.boltzgen_inputs import materialize_generation_input
    from component_runtime import SourceIdentity
    params.update(materialize_generation_input(params, tmp_path / 'prepared', allowed_input_roots=[tmp_path]))
    command = compile_nextflow_invocation('boltzgen', mode, params, str(tmp_path), job_id='offline-capability',
                                    source_identity=SourceIdentity('a' * 40, 'b' * 40))
    assert command.entrypoint == 'workflows/boltzgen_generation.nf'
    native = json.loads(next(item.payload for item in command.generated_inputs
                             if item.relative_path == '.boltzgen-generation-settings.json'))
    assert native['boltzgen_protocol'] == protocol
    assert native['boltzgen_generation_mode'] == mode
    assert native['boltzgen_alpha'] == 0
    assert native['boltzgen_filter_biased'] is False
    assert native['boltzgen_min_plddt'] is None
