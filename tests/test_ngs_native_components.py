"""Real native annotation graph checks; no scheduler, provider, or sequencing run."""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLONE_SOURCE = 'modules/ngs/clone_validation.nf'


@pytest.fixture
def native(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'platform/api'))
    monkeypatch.syspath_prepend(str(ROOT))
    return importlib.import_module('native_components')


def declared_processes(text):
    return set(re.findall(r'^\s*process\s+(\w+)\s*\{', text, re.MULTILINE))


def assert_registered_processes(contracts, source):
    declared = declared_processes((ROOT / source).read_text())
    registered = {key.split(':', 1)[1] for key in contracts if key.startswith(source + ':')}
    assert declared == registered, (source, 'missing', declared - registered,
                                   'stale', registered - declared)


def test_every_ngs_process_is_registered(native):
    for source in sorted((ROOT / 'modules/ngs').glob('*.nf')):
        assert_registered_processes(native.PROCESS_CONTRACTS, source.relative_to(ROOT).as_posix())


def test_reverse_coverage_detects_an_omitted_process(native):
    contracts = dict(native.PROCESS_CONTRACTS)
    del contracts[CLONE_SOURCE + ':ComparePlasmidConsensus']
    with pytest.raises(AssertionError, match='ComparePlasmidConsensus'):
        assert_registered_processes(contracts, CLONE_SOURCE)


def test_clone_registry_exact_declarations(native):
    text = (ROOT / CLONE_SOURCE).read_text()
    parts = re.split(r'^process\s+(\w+)\s*\{', text, flags=re.MULTILINE)
    for name, body in zip(parts[1::2], parts[2::2]):
        labels, inputs, outputs, helpers, _ = native.PROCESS_CONTRACTS[CLONE_SOURCE + ':' + name]
        assert tuple(re.findall(r"^\s*label\s+'([^']+)'", body, re.MULTILINE)) == labels
        for section, expected in [('input', inputs), ('output', outputs)]:
            match = re.search(r'^\s*' + section + r':\s*\n(.*?)(?=^\s*(?:input|output|script|when|shell|exec):)',
                              body, re.MULTILINE | re.DOTALL)
            assert match, (name, section)
            actual = tuple(line.strip() for line in match[1].splitlines()
                           if line.strip() and not line.lstrip().startswith('//'))
            assert actual == expected, (name, section, actual, expected)
        for helper in helpers:
            assert (ROOT / helper).is_file(), helper
    outputs = native.PROCESS_CONTRACTS[CLONE_SOURCE + ':RunCloneValidation'][2]
    assert 'path "input_model_provenance.json", emit: input_model_provenance' in outputs


def metadata(native, workflow, settings):
    components, dynamic, roles, services, blockers = [], [], [], [], []
    dependencies = {}
    def unresolved(*args, **kwargs):
        blockers.append((args, kwargs))
    handled = native.append_native_workflow_metadata(
        'nanopore', workflow, settings, f'workflows/ngs/{workflow}.nf',
        components, dynamic, dependencies, roles, services, unresolved,
    )
    assert handled
    assert not dynamic
    graph = {row.component_key: row for row in components}
    assert len(graph) == len(components)
    for key, row in graph.items():
        assert set(row.depends_on) <= graph.keys(), key
        assert key not in row.depends_on
        assert row.authority in native.PROCESS_CONTRACTS
    return graph, dependencies, roles, blockers


@pytest.mark.parametrize('workflow,assembly', [
    ('wf_clone_validation', True), ('ont_construct_screening', True),
    ('ont_construct_screening', False),
])
@pytest.mark.parametrize('input_mode', ['fastq', 'bam', 'pod5'])
@pytest.mark.parametrize('run_qc', [False, True])
@pytest.mark.parametrize('realign', [False, True])
def test_real_native_graph_matches_plasmid_routes(native, workflow, assembly, input_mode, run_qc, realign):
    settings = {
        {'fastq': 'fastq_path', 'bam': 'bam_path', 'pod5': 'pod5_dir'}[input_mode]: '/input/reads',
        'reference_fasta': '/input/reference.fasta', 'run_fastq_qc': run_qc,
        'run_assembly': assembly, 'bam_force_realign': realign,
        'comparison_panel_snapshot': '/input/panel.json',
    }
    graph, deps, roles, blockers = metadata(native, workflow, settings)
    mapped = ('FastqAlign' if input_mode == 'fastq' else
              'ValidateMappedBam' if input_mode == 'bam' and not realign else 'DoradoAlign')
    assert mapped in graph
    assert ('ValidateMappedBam' in graph) == (input_mode == 'bam' and not realign)
    assert ('BamToFastqForQC' in graph) == (run_qc and input_mode != 'fastq')
    for key in ['FastqDimerAnalysis', 'BuildDimerCanonicalOutputs', 'FastqPlasmidQC']:
        assert (key in graph) == run_qc
    assert ('RunCloneValidation' in graph) == assembly
    assert ('CloneValidationAdapter' in graph) == assembly
    assert ('ComparePlasmidConsensus' in graph) == (assembly and run_qc)
    assert ('ConstructVerify' in graph) == (assembly or run_qc)
    assert ('ComparisonPanelAttribution' in graph) == (run_qc and workflow == 'ont_construct_screening')
    if run_qc:
        reads = () if input_mode == 'fastq' else ('BamToFastqForQC',)
        assert graph['FastqDimerAnalysis'].depends_on == reads
        assert graph['BuildDimerCanonicalOutputs'].depends_on == ('FastqDimerAnalysis',)
        assert set(graph['FastqPlasmidQC'].depends_on) == {mapped, *reads}
    if assembly:
        assert set(graph['CloneValidationAdapter'].depends_on) == {'RunCloneValidation', mapped}
        # Preserve the pre-existing nested-runtime admission blocker; a graph
        # fix must not pretend its external image inventory was qualified.
        assert any('RunCloneValidation' == args[0] for args, _ in blockers)
    if assembly and run_qc:
        assert set(graph['ComparePlasmidConsensus'].depends_on) == {'CloneValidationAdapter', 'FastqPlasmidQC'}
        assert 'support_tool:scripts/compare_plasmid_consensus.py' in deps
        assert 'support_tool:scripts/plasmid_evidence.py' in deps
        assert 'support_tool:scripts/plasmid_circular.py' in deps
    if assembly or run_qc:
        parents = set(graph['ConstructVerify'].depends_on)
        assert mapped in parents
        assert ('BuildDimerCanonicalOutputs' in parents) == run_qc
        if assembly:
            assert 'CloneValidationAdapter' in parents
            assert ('ComparePlasmidConsensus' in parents) == run_qc
        else:
            assert 'FastqPlasmidQC' in parents
        verify_inputs = [role for role in roles if role.component_key == 'ConstructVerify' and role.direction == 'input']
        assert verify_inputs
        source_ids = set(verify_inputs[0].source_role_ids)
        if run_qc:
            assert 'BuildDimerCanonicalOutputs:output:breakpoint_call' in source_ids
            assert 'BuildDimerCanonicalOutputs:output:secondary_summary' in source_ids
        if assembly and run_qc:
            assert 'ComparePlasmidConsensus:output:verification_input' in source_ids
