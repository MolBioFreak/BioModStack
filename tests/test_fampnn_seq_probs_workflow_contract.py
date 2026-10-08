from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_fampnn_module_publishes_sample_pkls_and_runs_seq_prob_analysis() -> None:
    module_text = (REPO_ROOT / "modules" / "fampnn.nf").read_text()

    assert 'pattern: "fampnn_output/sample_pkls/*.pkl"' in module_text
    assert "analyse_fampnn_seq_probs.py" in module_text
    assert "fampnn_seq_prob_metrics_${batch_id}.jsonl" in module_text
    assert "${params.out_dir}/run/fampnn/sample_pkls" in module_text
