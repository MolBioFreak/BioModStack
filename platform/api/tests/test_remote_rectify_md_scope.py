"""Scope disposition: preserve the pre-bridge native MD control behavior."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_bms_md_low_level import _materialized_gromacs, _materialized_openmm
from test_md_typed_launch import _intent, _profile, CATALOG_DIGEST


@pytest.mark.parametrize('stage', ['nvt', 'npt', 'production'])
@pytest.mark.parametrize('requested_timestep', [2.0, 4.0])
def test_prepared_gromacs_keeps_prebridge_fixed_dt(tmp_path, stage, requested_timestep):
    from scripts.bms_md.runner import render_mdp
    _, config = _materialized_gromacs(tmp_path)
    config['stages']['production']['timestep_fs'] = requested_timestep
    mdp = render_mdp(stage, config, 0)
    values = {key.strip(): value.strip() for key, value in
              (line.split('=', 1) for line in mdp.splitlines() if '=' in line)}
    assert values['dt'] == '0.002'
    assert config['stages']['production']['timestep_fs'] == requested_timestep


@pytest.mark.parametrize('neutralize', [True, False])
def test_typed_preview_preserves_prebridge_neutralize_request_and_profile_guards(neutralize):
    from services.md import starting_structures as native
    intent = _intent(native)
    intent.requested_settings.neutralize = neutralize
    resolved = native.resolve_product_source(intent.source_ref)
    preview = native.compile_launch_preview(intent=intent, resolved=resolved,
        profile=_profile(), current_catalog_digest=CATALOG_DIGEST)
    assert preview.requested_settings.neutralize is neutralize
    assert preview.effective_request.preparation.neutralize is neutralize
    intent.requested_settings.timestep_fs = 4.0
    with pytest.raises(native.StartingStructureError, match='fixed by the selected chemistry profile'):
        native.compile_launch_preview(intent=intent, resolved=resolved,
            profile=_profile(), current_catalog_digest=CATALOG_DIGEST)


def test_openmm_native_controls_keep_prebridge_cadence_and_resume(tmp_path, monkeypatch):
    """Fake OpenMM binding only; real verified inputs, runner and stage ledger."""
    from scripts.bms_md import openmm_pipeline as native
    config_path, config = _materialized_openmm(tmp_path)
    config['stages']['production']['timestep_fs'] = 4.0
    config_path.write_text(json.dumps(config))
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', 'GPU-fixture')
    monkeypatch.setenv('OPENMM_GROMACS_INCLUDE_DIR', str(tmp_path))
    recorded = []

    class Integrator:
        def __init__(self, temperature, friction, dt):
            recorded.append(('dt', dt))
        def setRandomNumberSeed(self, seed):
            recorded.append(('seed', seed))

    class Simulation:
        def __init__(self, *args):
            self.reporters = []
            self.currentStep = 0
            self.context = SimpleNamespace(setPositions=lambda p: None,
                setVelocitiesToTemperature=lambda *a: None,
                getState=lambda **kw: SimpleNamespace(getPositions=lambda: []))
        def step(self, count):
            recorded.append(('step', count))
            self.currentStep += count
            for path, _interval in self.reporters:
                Path(path).write_bytes(b'fake scientific output')
        def saveCheckpoint(self, path):
            Path(path).write_text(str(self.currentStep))
        def loadCheckpoint(self, path):
            self.currentStep = int(Path(path).read_text())
        def saveState(self, path):
            Path(path).write_text('fake state')

    def checkpoint_reporter(path, interval):
        recorded.append(('checkpoint_interval', interval))
        return path, interval

    app = SimpleNamespace(PME='PME', HBonds='HBonds',
        GromacsGroFile=lambda p: SimpleNamespace(positions=[], getPeriodicBoxVectors=lambda: []),
        GromacsTopFile=lambda *a, **kw: SimpleNamespace(topology=[],
            createSystem=lambda **kw: SimpleNamespace(addForce=lambda force: None)),
        Simulation=Simulation, DCDReporter=lambda p, n, **kw: (p, n),
        StateDataReporter=lambda p, n, **kw: (p, n), CheckpointReporter=checkpoint_reporter,
        PDBFile=SimpleNamespace(writeFile=lambda top, pos, handle: handle.write('END\n')))
    monkeypatch.setattr(native, '_require_openmm_cuda', lambda: (None, app,
        SimpleNamespace(bar=1, kelvin=1, picosecond=1, picoseconds=1),
        SimpleNamespace(getPlatformByName=lambda name: name), Integrator, lambda *a: None, 'fixture'))

    class ProductionObserved(Exception):
        pass

    # Stop after the real runner seals production, before fake bytes reach analysis.
    monkeypatch.setattr(native, 'write_atom_order_manifest',
        lambda *args: (_ for _ in ()).throw(ProductionObserved()))
    output = tmp_path / 'output'
    for _ in range(2):
        with pytest.raises(ProductionObserved):
            native.run_openmm_job(config_path, output)
    assert [value for key, value in recorded if key == 'dt'] == [0.002, 0.002]
    assert [value for key, value in recorded if key == 'step'] == [5000]
    assert [value for key, value in recorded if key == 'checkpoint_interval'] == [100, 100]
    assert json.loads((output / 'stage_state.json').read_text())['stages']['production']['status'] == 'completed'
