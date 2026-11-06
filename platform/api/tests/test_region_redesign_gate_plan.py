"""A region_redesign plan declares the review gate the workflow profile actually runs.

The profile defaults interactive_gating to true and the job params do not carry it. The plan used to omit
OpenInteractiveGate (and its component_adapter dependency) unless a Protenix MSA happened to add the adapter,
so Boltz-2 / ESMFold2 only runs launched on a worker without a component context and the gate task fell back
to the controller-HTTP path, which needs `requests` on the worker.
"""
from types import SimpleNamespace

import pytest

from services.nextflow import needs_component_runtime
from test_denovo_remote_dependencies import plan  # noqa: F401  (also provides the assets fixture)


def _gate(selected):
    names = [component.authority.rsplit(':', 1)[-1]
             for component in (*selected.metadata.static_components, *selected.metadata.dynamic_templates)]
    adapter = any(d.kind == 'support_tool' and d.relative_path == 'scripts/lib/component_adapter.py'
                  for d in selected.metadata.dependencies)
    return 'OpenInteractiveGate' in names, adapter


@pytest.mark.parametrize('designer', ['fampnn', 'mpnn'])
@pytest.mark.parametrize('validators', [['boltz2'], ['esmfold2'], ['protenix_v2'], ['boltz2', 'esmfold2']])
def test_default_gate_is_declared_for_every_validator_choice(designer, validators):
    selected = plan({'plr_seq_method': designer, 'plr_structure_validators': validators},
                    mode='region_redesign', entrypoint='protein_local_redesign')
    declared, adapter = _gate(selected)
    assert declared and adapter
    assert needs_component_runtime(SimpleNamespace(execution_plan=SimpleNamespace(metadata=selected.metadata)))


def test_explicit_no_gate_and_continuation_declare_no_gate():
    base = {'plr_seq_method': 'fampnn', 'plr_structure_validators': ['boltz2']}
    assert _gate(plan(dict(base, interactive_gating=False), mode='region_redesign',
                      entrypoint='protein_local_redesign'))[0] is False
    assert _gate(plan(dict(base, interactive_gate_continue=True), mode='region_redesign',
                      entrypoint='protein_local_redesign'))[0] is False
