"""TEST ONLY subprocess harness; never imported by production entrypoints.

Installs an isolated registry in this process and enables the transport library's
segregated loopback fixture namespace. Does not weaken licenses or approvals.
The normal shell/manager parser and orchestration then execute unchanged.
"""
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'platform/api'), str(ROOT / 'scripts')]
fixture = json.loads(Path(sys.argv[1]).read_text())
import model_registry
from model_registry import RuntimeAcquisitionArtifact
from lib import pinned_acquisition as acquisition, pinned_weight_layout as layout
from services import runtime_acquisition as service

model_id = fixture.get('model_id', 'esmfold2')
original = model_registry.get_registry().get_model(model_id)
isolated = original.model_copy(update={'acquisition': [RuntimeAcquisitionArtifact(**e) for e in fixture['entries']]})
model_registry.get_registry = lambda: SimpleNamespace(get_model=lambda name: isolated if name == model_id else None)
validate = acquisition.Artifact.validate
acquisition.Artifact.validate = lambda self, **kwargs: validate(self, test_only=True)
acquire = acquisition.acquire
materialize = layout.materialize_weights

def fixture_acquire(*args, **kwargs):
    kwargs.update(test_only=True, attempts=1, timeout=2)
    return acquire(*args, **kwargs)

def fixture_materialize(*args, **kwargs):
    kwargs.update(test_only=True, attempts=1, timeout=2)
    return materialize(*args, **kwargs)

service.acquire = layout.acquire = fixture_acquire
service.materialize_weights = fixture_materialize
revalidate = service.revalidate_model_receipt
def fixture_revalidate(*args, **kwargs):
    return revalidate(*args, **kwargs, test_only=True)
service.revalidate_model_receipt = fixture_revalidate
args = sys.argv[2:]
if args[0] == '-B':
    args.pop(0)
assert Path(args[0]).resolve() == ROOT / 'scripts/manage_desktop_services.py'
sys.argv = args
runpy.run_path(str(ROOT / 'scripts/manage_desktop_services.py'), run_name='__main__')
