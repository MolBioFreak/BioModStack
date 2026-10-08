from __future__ import annotations

import json
import pickle
import subprocess
import sys
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "analyse_fampnn_seq_probs.py"


def test_analyse_fampnn_seq_probs_reports_sampled_logprob_entropy_and_missing_status(tmp_path: Path) -> None:
    sample_dir = tmp_path / "sample_pkls"
    sample_dir.mkdir()
    out_jsonl = tmp_path / "metrics.jsonl"

    # Rows are residue probability vectors over 20 amino acids. pred_aatype chooses the sampled residue.
    seq_probs = np.full((3, 20), 0.01, dtype=float)
    seq_probs[0, 0] = 0.81
    seq_probs[1, 1] = 0.72
    seq_probs[2, 2] = 0.55
    seq_probs = seq_probs / seq_probs.sum(axis=1, keepdims=True)
    payload = {
        "seq_probs": seq_probs,
        "pred_aatype": np.array([0, 1, 2], dtype=int),
        "seq_mask": np.array([1, 1, 0], dtype=bool),
        "residue_index": np.array([10, 11, 12], dtype=int),
        "chain_index": np.array([0, 0, 0], dtype=int),
    }
    with (sample_dir / "design_A.pkl").open("wb") as handle:
        pickle.dump(payload, handle)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--sample-pkl-dir",
            str(sample_dir),
            "--out-jsonl",
            str(out_jsonl),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in out_jsonl.read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["design"] == "design_A"
    assert row["metric_source"] == "fampnn_sample_pkl_seq_probs"
    assert row["designed_residue_count"] == 2
    assert row["total_residue_count"] == 3
    assert 0 < row["fampnn_mean_sampled_prob"] < 1
    assert row["fampnn_mean_sampled_log_prob"] < 0
    assert row["fampnn_min_sampled_prob"] <= row["fampnn_mean_sampled_prob"]
    assert row["fampnn_mean_entropy"] > 0
    assert row["fampnn_max_entropy"] >= row["fampnn_mean_entropy"]
    assert row["fampnn_seq_probs_available"] is True


def test_analyse_fampnn_seq_probs_marks_pkl_without_seq_probs_incomplete(tmp_path: Path) -> None:
    sample_dir = tmp_path / "sample_pkls"
    sample_dir.mkdir()
    out_jsonl = tmp_path / "metrics.jsonl"
    with (sample_dir / "missing_probs.pkl").open("wb") as handle:
        pickle.dump({"pred_aatype": [0, 1]}, handle)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--sample-pkl-dir", str(sample_dir), "--out-jsonl", str(out_jsonl)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    row = json.loads(out_jsonl.read_text().strip())
    assert row["design"] == "missing_probs"
    assert row["fampnn_seq_probs_available"] is False
    assert "seq_probs" in row["missing"]
