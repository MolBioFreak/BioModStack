from pathlib import Path


def test_pre_collected_sequence_inputs_set_fampnn_candidate_dir_for_ppiflow_maturation():
    workflow = Path("workflows/antibody_denovo.nf").read_text()
    branch = workflow.split("if (!run_fampnn && selectedInputIsSequenceConditioned", 1)[1].split("if (run_fampnn)", 1)[0]
    assert "fampnn_seqs = pre_collected_pdbs.map" in branch
    assert "fampnnCandidateDir = selectedInputDir.toString()" in branch
