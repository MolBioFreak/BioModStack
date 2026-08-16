from __future__ import annotations

import json
import pickle
import subprocess
import sys
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "analyse_fampnn_seq_probs.py"


def test_analyse_fampnn_seq_probs_reports_model_favored_single_mutation_deltas(tmp_path: Path) -> None:
    sample_dir = tmp_path / "sample_pkls"
    sample_dir.mkdir()
    out_jsonl = tmp_path / "metrics.jsonl"

    seq_probs = np.full((2, 20), 0.01, dtype=float)
    # Position 101 sampled A, but D is more likely: model-favored A101D.
    seq_probs[0, 0] = 0.20
    seq_probs[0, 2] = 0.60
    # Position 102 sampled C and no better alternatives above threshold.
    seq_probs[1, 1] = 0.80
    seq_probs[1, 3] = 0.05
    seq_probs = seq_probs / seq_probs.sum(axis=1, keepdims=True)
    payload = {
        "seq_probs": seq_probs,
        "pred_aatype": np.array([0, 1], dtype=int),
        "seq_mask": np.array([1, 1], dtype=bool),
        "residue_index": np.array([101, 102], dtype=int),
        "chain_index": np.array([0, 0], dtype=int),
    }
    with (sample_dir / "design_mut.pkl").open("wb") as handle:
        pickle.dump(payload, handle)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--sample-pkl-dir",
            str(sample_dir),
            "--out-jsonl",
            str(out_jsonl),
            "--mutation-top-n",
            "3",
            "--mutation-min-log-odds-delta",
            "0.5",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    row = json.loads(out_jsonl.read_text().strip())
    assert row["fampnn_mutation_scoring_available"] is True
    assert row["fampnn_mutation_score_source"] == "seq_probs_log_odds_delta"
    assert row["fampnn_top_model_favored_mutations"] == [
        {
            "chain_index": 0,
            "residue_index": 101,
            "from_aa": "A",
            "to_aa": "D",
            "mutation": "A101D",
            "from_prob": row["fampnn_top_model_favored_mutations"][0]["from_prob"],
            "to_prob": row["fampnn_top_model_favored_mutations"][0]["to_prob"],
            "log_odds_delta": row["fampnn_top_model_favored_mutations"][0]["log_odds_delta"],
        }
    ]
    assert row["fampnn_top_model_favored_mutations"][0]["to_prob"] > row["fampnn_top_model_favored_mutations"][0]["from_prob"]
    assert row["fampnn_top_model_favored_mutations"][0]["log_odds_delta"] > 0.5
    assert row["fampnn_mutation_opportunity_count"] == 1
