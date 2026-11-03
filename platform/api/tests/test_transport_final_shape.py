"""Real selected compiler + SQLite service owner; only provider I/O is inert."""
import json
from pathlib import Path
import shutil

import pytest

from component_runtime import SourceIdentity, canonical_bytes
from services import nextflow, model_msa_handoff as handoff, msa_preparation

ROOT = Path(__file__).resolve().parents[3]


def selected_plan(count=1):
    cfg = dict(sequence_policy='auto', sequences_per_backbone=count,
        sequence_engine='proteinmpnn', validator_suite=['boltz2', 'protenix_v2'],
        validator_settings={
            'boltz2': dict(boltz_use_msa=True, msa_provider='neurosnap_api',
                msa_neurosnap_force_uppercase=False, msa_neurosnap_pad_sequences=False,
                msa_neurosnap_coverage_percent=37, colabfold_pairing_mode='unpaired'),
            'protenix_v2': dict(protenix_use_msa=True, protenix_msa_backend='colabfold_api',
                msa_provider='colabfold_api', colabfold_use_filter=False,
                colabfold_use_env=False, colabfold_pairing_mode='unpaired')})
    params = dict(modification_mode='shape_blueprint', shape_request=cfg)
    return nextflow.build_selected_execution_plan(
        model_id='protein_modification_experimental', mode='shape_blueprint',
        entrypoint='workflows/shape_blueprint_design.nf', requested=canonical_bytes(params),
        effective=canonical_bytes(params), native_parameters=canonical_bytes(params),
        source_identity=SourceIdentity('a' * 40, 'b' * 40))


def test_shape_actual_selected_closure():
    plan = selected_plan()
    services = plan.metadata.external_services
    assert {x.logical_id for x in services} == {'shape:boltz2:generated_msa', 'shape:protenix_v2:generated_msa'}
    assert all(handoff.generated_msa_service_supported(x) for x in services)
    assert all(x.input_role_ids for x in services)
    paths = {x.relative_path for x in plan.metadata.dependencies}
    assert {'scripts/shape_blueprint/run_shape_esmfold2.py', 'scripts/run_esmfold2_inference.py',
        'scripts/bms_gpu_run_telemetry.py', 'scripts/lib/portable_inputs.py',
        'scripts/shape_blueprint/generated_msa.py', 'scripts/lib/component_adapter.py',
        'biomodstack_msa_handoff.py', 'biomodstack_boltz_msa.py', 'platform/api/component_runtime.py'} <= paths
    from native_components import PROCESS_CONTRACTS
    assert PROCESS_CONTRACTS['modules/shape_blueprint.nf:RunShapeProteinMPNN'][1][-1] == 'path request_dir'
    assert not selected_plan(0).metadata.external_services


@pytest.mark.asyncio
async def test_shape_component_controller_receiving_distinct_policies(tmp_path, monkeypatch):
    from scripts.lib.component_adapter import runtime_from_environment
    from scripts.shape_blueprint.generated_msa import generated_chain
    plan = selected_plan()
    output = tmp_path / 'worker/results'
    output.mkdir(parents=True)
    context = tmp_path / 'context.json'
    context.write_text(json.dumps(dict(ledger_path=str(tmp_path / 'component.sqlite'),
        artifact_root=str(output), attempt_id='attempt', root_job_id='root',
        target_id='remote-target', lease_id='lease', plan_sha256=plan.plan_sha256,
        source_identity={'revision': 'a' * 40, 'tree': 'b' * 40},
        execution_plan=plan.to_dict(), working_directory=str(ROOT))))
    monkeypatch.setenv('BMS_COMPONENT_CONTEXT', str(context))
    owner = runtime_from_environment(context)
    source = tmp_path / 'provider.a3m'
    source.write_text('>query\nAAAAA\n>INERT_provider_hit\nAAA-A\n')
    observed = []
    def provider_io(*, sequences, params):
        assert sequences == ['AAAAA']
        observed.append(dict(params))
        return dict(provider=params['msa_provider'], request_digest='a' * 64, cache_hit=False,
            provenance={'fixture': 'INERT provider I/O'}, artifacts=[dict(chain_index=0,
                role='unpaired', path=str(source), sha256=handoff.digest(source.read_bytes()))])
    monkeypatch.setattr(msa_preparation, 'prepare_model_msa', provider_io)
    roster = [{'name': 'shape', 'sequences': [{'proteinChain': {'sequence': 'AAAAA', 'count': 1}}]}]
    async def fence():
        pass
    for validator in ('boltz2', 'protenix_v2'):
        logical = 'shape:' + validator + ':generated_msa'
        key = owner.submit_external_service(logical, roster)
        request = next(row for row in owner.pending_external_services() if row['request_id'] == key)
        package = tmp_path / 'controller' / validator
        sha = await handoff.prepare_generated_msa_on_controller(request, package, fence)
        delivered = output / 'external-services' / key / sha
        shutil.copytree(package, delivered)
        handoff.accept_generated_msa(owner, key, sha)
        shutil.rmtree(package)
        # The actual worker adapter reuses its completed SQLite service row.
        chain = generated_chain(validator, 'AAAAA')
        assert Path(chain['unpairedMsaPath']).is_relative_to(delivered)
        assert Path(chain['unpairedMsaPath']).read_bytes() == source.read_bytes()
    assert [x['msa_provider'] for x in observed] == ['neurosnap_api', 'colabfold_api']
    assert observed[0]['msa_neurosnap_force_uppercase'] is False
    assert observed[0]['msa_neurosnap_pad_sequences'] is False
    assert observed[0]['msa_neurosnap_coverage_percent'] == 37
    assert observed[1]['colabfold_use_filter'] is False
    assert runtime_from_environment(context).pending_external_services() == ()


@pytest.mark.parametrize('logical,consumer', [
    ('shape:boltz2:generated_msa', 'RunShapeProtenixValidator'),
    ('shape:protenix_v2:generated_msa', 'RunShapeBoltzValidator')])
def test_shape_service_does_not_cross_peer_authority(logical, consumer):
    assert not handoff.generated_msa_service_supported(dict(logical_id=logical,
        authority='modules/shape_blueprint.nf:' + consumer,
        state='planned_from_generated_candidates', provider='colabfold_api'))

def test_boltz_policy_preserves_flags_without_protenix_only_refusal(tmp_path):
    from biomodstack_msa_policy import apply_msa_policy
    from biomodstack_msa_api import MSAAPIError
    from services.msa_preparation import prepare_protenix_inputs
    settings = dict(msa_provider='neurosnap_api', boltz_use_msa=True,
                    msa_neurosnap_force_uppercase=True, msa_neurosnap_pad_sequences=True)
    assert apply_msa_policy('boltz2', settings)['msa_neurosnap_force_uppercase'] is True
    native = tmp_path / 'input.json'
    native.write_text(json.dumps([{'name':'shape', 'sequences':[
        {'proteinChain':{'sequence':'AAAAAAAAAAAAAAAAAAAA','count':1}}]}]))
    # Shared provider itself has an older A3M capability restriction. Preserve it;
    # do not misreport this as a Shape/Protenix policy refusal or widen provider I/O.
    with pytest.raises(MSAAPIError, match='uppercase/padding transformations'):
        prepare_protenix_inputs(None, native, tmp_path / 'out', settings, policy_model='boltz2')

