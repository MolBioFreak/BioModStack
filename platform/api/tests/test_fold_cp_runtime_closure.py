"""Fold-CP (boltz_cp_experimental) composed runtime closure.

Real registry, real compiler and real asset reader over a controlled installation;
fixture bytes are not scientific weights and no GPU/inference is exercised.
"""
import hashlib
import json
from types import SimpleNamespace

import pytest

from services.remote_execution import bundle, cache
from services.remote_execution.contracts import ProvisionSelection, WorkflowProvisionSelection

MODEL = 'boltz_cp_experimental'
IMAGE = 'fold-cp.sif'
SHARED_WEIGHTS = 'boltz'
WEIGHTS_FIXTURE = 'fixture-weight'
IMAGE_BYTES = b'offline Fold-CP image fixture, not executable'
BOLTZ2_BYTES = b'offline Boltz-2 image fixture, not executable'
WEIGHTS_BYTES = b'fixture, not scientific weights'


@pytest.fixture
def installation(tmp_path, monkeypatch):
    """Bind the same installation roots the Fold-CP image selector already uses."""
    import paths
    containers, weights, store = (tmp_path / name for name in ('containers', 'weights', 'store'))
    containers.mkdir()
    (weights / SHARED_WEIGHTS).mkdir(parents=True)
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(store))
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_LANE', 'development')
    monkeypatch.delenv('BMS_FOLD_CP_CONTAINER_PATH', raising=False)
    # The native compiler resolves its container dir/weights root from these, while
    # the provisioning reader looks them up through paths/bundle.
    monkeypatch.setenv('BMS_CONTAINER_DIR', str(containers))
    monkeypatch.setenv('BMS_WEIGHTS', str(weights))
    for owner in (paths, bundle):
        monkeypatch.setattr(owner, 'get_container_dir', lambda: containers)
        monkeypatch.setattr(owner, 'get_weights_root', lambda: weights)
    (containers / IMAGE).write_bytes(IMAGE_BYTES)
    (containers / 'boltz2.sif').write_bytes(BOLTZ2_BYTES)
    (weights / SHARED_WEIGHTS / WEIGHTS_FIXTURE).write_bytes(WEIGHTS_BYTES)
    monkeypatch.setattr(cache, 'current_source_identity', lambda: ('a' * 40, 'b' * 40))
    return containers, weights, store


def target():
    return SimpleNamespace(id='one', host='worker', port=22, username='root',
                           remote_root='/worker', host_key_sha256='c' * 64)


def model_preview(model_id):
    return cache.independent_preview(ProvisionSelection(kind='model', model_id=model_id), target())


def test_fold_cp_closure_is_its_launcher_image_and_the_shared_boltz_tree():
    from model_registry import model_runtime_dependencies, workflow_pack_weight_groups
    from native_components import LABEL_ASSETS
    assert LABEL_ASSETS['BoltzCP'] == (IMAGE, 'bcp_container_path', SHARED_WEIGHTS, 'boltz_models')
    assert workflow_pack_weight_groups('structure_prediction')['fold_cp'] == (SHARED_WEIGHTS,)
    assert [(ref.kind, ref.relative_path) for ref in model_runtime_dependencies(MODEL)] == [
        ('image', IMAGE), ('weights', SHARED_WEIGHTS)]


def test_fold_cp_image_scope_still_works_without_weights(installation):
    (installation[1] / SHARED_WEIGHTS / WEIGHTS_FIXTURE).unlink()
    (installation[1] / SHARED_WEIGHTS).rmdir()
    preview, entries = cache.independent_preview(
        ProvisionSelection(kind='image', model_id=MODEL), target())
    assert preview.blockers == []
    assert {row.name for row in preview.artifacts} == {'containers/' + IMAGE}
    assert [entry.remote_destination for entry in entries] == ['containers/' + IMAGE]


def test_fold_cp_model_scope_preview_reports_image_and_weights_not_a_blocker(installation):
    preview, entries = model_preview(MODEL)
    assert preview.blockers == []
    assert preview.estimates_complete is True
    # Dependency reporting replaces the previous binding_unavailable refusal.
    assert sorted(row.name for row in preview.dependencies) == [
        'containers/' + IMAGE, 'weights/' + SHARED_WEIGHTS]
    assert {row.name for row in preview.artifacts} == {
        'containers/' + IMAGE, 'weights/' + SHARED_WEIGHTS + '/' + WEIGHTS_FIXTURE}
    assert preview.total_bytes == len(IMAGE_BYTES) + len(WEIGHTS_BYTES)
    assert preview.total_bytes > 0
    assert {entry.role for entry in entries} == {'image', 'runtime'}
    # Download evidence is never scientific acceptance.
    assert preview.scientific_ready is False


def test_fold_cp_reuses_the_declared_weight_source_rather_than_a_new_set(installation):
    fold_cp, _ = model_preview(MODEL)
    boltz2, _ = model_preview('boltz2')
    def weights_by_sha(preview):
        return {row.name: row.sha256 for row in preview.artifacts if row.name.startswith('weights/')}
    digest = hashlib.sha256(WEIGHTS_BYTES).hexdigest()
    assert weights_by_sha(fold_cp) == weights_by_sha(boltz2) == {
        'weights/' + SHARED_WEIGHTS + '/' + WEIGHTS_FIXTURE: digest}


def test_boltz2_composed_closure_and_preview_are_unchanged(installation):
    from model_registry import model_runtime_dependencies
    # Literal expectation captured before the Fold-CP binding: boltz2 keeps one
    # image and one weights tree, with no extra artifact.
    assert [(ref.kind, ref.relative_path) for ref in model_runtime_dependencies('boltz2')] == [
        ('image', 'boltz2.sif'), ('weights', SHARED_WEIGHTS)]
    boltz2, entries = model_preview('boltz2')
    assert boltz2.blockers == []
    assert {row.name for row in boltz2.artifacts} == {
        'containers/boltz2.sif', 'weights/' + SHARED_WEIGHTS + '/' + WEIGHTS_FIXTURE}
    assert boltz2.total_bytes == len(BOLTZ2_BYTES) + len(WEIGHTS_BYTES)
    assert [entry.remote_destination for entry in entries] == [
        'containers/boltz2.sif', 'weights/' + SHARED_WEIGHTS + '/' + WEIGHTS_FIXTURE]


@pytest.mark.asyncio
async def test_every_managed_predictor_the_structure_workflow_offers_has_a_closure(monkeypatch):
    from model_registry import model_runtime_dependencies
    from routers.execution_targets import provision_catalog
    monkeypatch.delenv('BMS_FEATURE_MOLECULAR_DYNAMICS', raising=False)
    catalog = {row.model_id for row in await provision_catalog() if row.kind == 'model'}
    # Structure Prediction's selected predictors are boltz/fold_cp/protenix/esmfold2;
    # each must enumerate its own selection instead of refusing a composed closure.
    for method, model_id in (('boltz', 'boltz2'), ('fold_cp', MODEL),
                             ('protenix', 'protenix'), ('esmfold2', 'esmfold2')):
        assert model_id in catalog, method
        assert len(model_runtime_dependencies(model_id)) >= 1


def test_fold_cp_workflow_scope_provision_enumerates_image_and_weights(installation, monkeypatch):
    from schemas import JobCreate
    from component_runtime import SourceIdentity
    from paths import get_code_root
    # The workflow scope compiles on the real committed source, so rebind the
    # fixture's placeholder identity to the checkout the compiler will report.
    identity = SourceIdentity.from_checkout(get_code_root())
    monkeypatch.setattr(cache, 'current_source_identity',
                        lambda: (identity.revision, identity.tree))
    request = JobCreate(name='fold-cp closure', model_id=MODEL, mode='design', params={
        'sequence': 'MKTIIALSYIFCLVFADYKDDDDA', 'sequence_name': 'closure',
        'pinned_gpus': [0, 1, 2, 3], 'boltz_use_msa': False, 'run_frustrampnn': False})
    selection = WorkflowProvisionSelection(kind='workflow', workflow_request=request)
    preview, entries = cache.independent_preview(selection, target())
    assert preview.blockers == [], preview.blockers
    assert {'containers/' + IMAGE, 'weights/' + SHARED_WEIGHTS} <= {
        row.name for row in preview.dependencies}
    # The compiler's materialized container selection is the same installed image
    # the provisioning reader binds, so both surfaces agree on one object.
    compiled = json.loads(cache.workflow_plan(selection)[1].native_parameters_json)
    assert compiled['bcp_container_path'] == str(installation[0] / IMAGE)
    assert {row.name for row in preview.artifacts} >= {
        'containers/' + IMAGE, 'weights/' + SHARED_WEIGHTS + '/' + WEIGHTS_FIXTURE}
    assert preview.total_bytes > 0
    assert [entry.remote_destination for entry in entries if entry.role == 'image'] == [
        'containers/' + IMAGE]
