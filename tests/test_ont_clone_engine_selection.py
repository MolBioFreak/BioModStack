"""Clone engine placement contracts; native startup is a separate offline audit.

No inference, live Job, image copies, or installed runtime is needed by these
regressions. The compiler tests exercise its real selected-dependency producer.
"""
from pathlib import Path

import pytest

from test_ngs_native_components import native


HOME_KEYS = ('BMS_WF_CLONE_NXF_HOME', 'NXF_HOME', 'BMS_NEXTFLOW_HOME', 'BMS_DATA')


@pytest.fixture
def homes(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    for key in HOME_KEYS:
        monkeypatch.delenv(key, raising=False)
    return tmp_path


@pytest.mark.parametrize('winner', range(6))
def test_engine_home_precedence_and_dependency_binding(native, homes, monkeypatch, winner):
    from scripts.validate_wf_clone_runtime import resolve_nextflow_home

    values = [homes / name for name in ('explicit', 'clone', 'launcher', 'managed', 'data')]
    for index, key in enumerate(HOME_KEYS, 1):
        if index >= winner:
            monkeypatch.setenv(key, str(values[index]))
    selected = str(values[0]) if winner == 0 else None
    expected = (values[winner] if winner < 4 else
                values[4] / 'nextflow' if winner == 4 else homes / 'home/.nextflow')
    assert resolve_nextflow_home(selected) == expected
    params = {'wf_clone_nxf_home': selected} if selected else {}
    _, paths = native.ngs_clone_runtime_dependencies(params)
    jars = [path for key, path in paths.items() if key.endswith('-one.jar')]
    assert jars == [expected / 'framework/25.10.0/nextflow-25.10.0-one.jar']


def test_explicit_relocated_home_beats_inherited_engine(native, homes, monkeypatch):
    from scripts.validate_wf_clone_runtime import resolve_nextflow_home

    monkeypatch.setenv('NXF_HOME', str(homes / 'outer'))
    monkeypatch.setenv('NXF_VER', '25.10.1')
    selected = homes / 'relocated runtime/nxf-home'
    assert resolve_nextflow_home(str(selected)) == selected
    _, paths = native.ngs_clone_runtime_dependencies({'wf_clone_nxf_home': str(selected)})
    jar = next(path for key, path in paths.items() if key.endswith('-one.jar'))
    assert jar == selected / 'framework/25.10.0/nextflow-25.10.0-one.jar'
    assert not selected.exists()  # Resolution neither installs nor creates a cache.


@pytest.mark.parametrize('assembly', [True, False])
@pytest.mark.parametrize('explicit', [True, False])
def test_normal_compiler_binds_only_selected_clone_engine(native, homes, monkeypatch, assembly, explicit):
    from services.nextflow import compile_nextflow_invocation

    monkeypatch.setenv('NXF_HOME', str(homes / 'launcher'))
    monkeypatch.setenv('NXF_VER', '25.10.1')
    monkeypatch.setenv('NXF_DIST', str(homes / 'outer-only-framework'))
    params = {'ont_workflow_id': 'ont_construct_screening', 'ont_input_mode': 'fastq',
              'fastq_path': str(homes / 'reads.fastq'),
              'reference_fasta': str(homes / 'reference.fa'),
              'run_assembly': assembly, 'run_fastq_qc': True}
    if explicit:
        params['wf_clone_nxf_home'] = str(homes / 'relocated')
    invocation = compile_nextflow_invocation('nanopore', 'construct_screening', params,
                                             str(homes / 'results'), job_id='engine-selection')
    assert invocation.execution_plan.complete, invocation.execution_plan.blockers
    bound = invocation.native_parameters
    command = list(invocation.command)
    dependencies = invocation.execution_plan.dependencies
    jars = [row for row in dependencies if (row.relative_path or '').endswith('-one.jar')]
    if assembly:
        expected = str(homes / ('relocated' if explicit else 'launcher'))
        assert bound['wf_clone_nxf_home'] == expected
        assert command.count('--wf_clone_nxf_home') == 1
        assert command[command.index('--wf_clone_nxf_home') + 1] == expected
        assert len(jars) == 1
        _, paths = native.ngs_clone_runtime_dependencies(bound)
        assert paths[jars[0].logical_id] == Path(expected) / 'framework/25.10.0/nextflow-25.10.0-one.jar'
    else:
        assert not jars
        assert not any('wf-clone-validation' in row.logical_id for row in dependencies)
        if not explicit:
            assert 'wf_clone_nxf_home' not in bound
            assert '--wf_clone_nxf_home' not in command


def test_clone_default_compiler_uses_managed_data_home(native, homes, monkeypatch):
    from services.nextflow import compile_nextflow_invocation

    monkeypatch.setenv('BMS_DATA', str(homes / 'data'))
    params = {'ont_workflow_id': 'wf_clone_validation', 'ont_input_mode': 'fastq',
              'fastq_path': str(homes / 'reads.fastq'),
              'reference_fasta': str(homes / 'reference.fa')}
    invocation = compile_nextflow_invocation('nanopore', 'clone_validation', params,
                                             str(homes / 'results'), job_id='clone-default')
    assert invocation.execution_plan.complete, invocation.execution_plan.blockers
    assert invocation.native_parameters['wf_clone_nxf_home'] == str(homes / 'data/nextflow')
    assert 'wf_clone_nxf_home' not in params  # No caller-supplied workaround.
    _, paths = native.ngs_clone_runtime_dependencies(invocation.native_parameters)
    jar = next(path for key, path in paths.items() if key.endswith('-one.jar'))
    assert jar == homes / 'data/nextflow/framework/25.10.0/nextflow-25.10.0-one.jar'
