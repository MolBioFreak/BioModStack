from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_fampnn_module_publishes_sample_pkls_and_runs_seq_prob_analysis() -> None:
    module_text = (REPO_ROOT / "modules" / "fampnn.nf").read_text()

    assert 'pattern: "fampnn_output/sample_pkls/*.pkl"' in module_text
    assert "analyse_fampnn_seq_probs.py" in module_text
    assert "fampnn_seq_prob_metrics_${batch_id}.jsonl" in module_text
    assert "${params.out_dir}/run/fampnn/sample_pkls" in module_text
    assert "--mutation-top-n" in module_text
    assert "fampnn_mutation_top_n" in module_text
    assert "--mutation-min-log-odds-delta" in module_text
    assert "fampnn_mutation_min_log_odds_delta" in module_text


def test_fampnn_mutation_scoring_params_propagate_to_child_jobs_and_api_model_configs() -> None:
    workflow_text = (REPO_ROOT / "workflows" / "antibody_denovo.nf").read_text()
    child_model_text = (REPO_ROOT / "platform/api/config/models/fampnn.yaml").read_text()
    denovo_model_text = (REPO_ROOT / "platform/api/config/models/antibody_denovo.yaml").read_text()

    assert "fampnn_mutation_top_n: params.fampnn_mutation_top_n" in workflow_text
    assert "fampnn_mutation_min_log_odds_delta: params.fampnn_mutation_min_log_odds_delta" in workflow_text

    for model_text in (child_model_text, denovo_model_text):
        assert "name: fampnn_mutation_top_n" in model_text
        assert "name: fampnn_mutation_min_log_odds_delta" in model_text
