"""Source wiring regressions, NOT proof of Nextflow execution or acceptance."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]


def test_clone_verifier_consumes_selected_structural_channels():
    text=(ROOT/'workflows/ngs/wf_clone_validation.nf').read_text()
    assert 'if (has_fastq && runFastqQc)' not in text
    assert 'BamToFastqForQC(analysis_bam)' in text
    assert 'breakpointEvidence = BuildDimerCanonicalOutputs.out.breakpoint_call' in text
    assert 'secondaryEvidence = BuildDimerCanonicalOutputs.out.secondary_summary' in text
    assert text.count('FastqDimerAnalysis(qcReads,')==1
    invocation=text[text.index('    ConstructVerify(\n'):]
    assert 'verificationInput,' in invocation
    assert 'ComparePlasmidConsensus(CloneValidationAdapter.out.verification_input, FastqPlasmidQC.out.consensus)' in text
    assert 'breakpointEvidence,' in invocation
    assert 'workflow.onComplete' in text


def test_construct_screening_verifies_assembly_and_normalizes_all_inputs():
    text=(ROOT/'workflows/ngs/ont_construct_screening.nf').read_text()
    assert 'if (has_fastq && runFastqQc)' not in text
    assert 'BamToFastqForQC(analysis_bam)' in text
    assert 'verificationInput = CloneValidationAdapter.out.verification_input' in text
    assert 'verificationInput = FastqPlasmidQC.out.verification_input' in text
    assert text.count('    ConstructVerify(')==1
    assert 'if (!runFastqQc)' in text
    assert 'if (runFastqQc || runAssembly)' in text


def test_model_guard_precedes_upstream_override():
    text=(ROOT/'modules/ngs/clone_validation.nf').read_text()
    assert text.index('${apiPython} ${modelValidator}')<text.index('--override_basecaller_cfg')


def test_every_plasmid_method_runs_tandem_modkit_from_aligned_reads():
    for name in ('wf_clone_validation.nf', 'ont_construct_screening.nf', 'ont_plasmid_qc.nf'):
        text=(ROOT/'workflows/ngs'/name).read_text()
        assert "include { ModkitAnalysis } from '../../modules/ngs/modkit_analysis.nf'" in text, name
        assert 'ModkitAnalysis(' in text, name
        if name != 'ont_plasmid_qc.nf':
            assert 'if (params.run_modkit == true && !has_fastq) {' in text, name
