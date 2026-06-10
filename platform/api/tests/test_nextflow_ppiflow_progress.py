from pathlib import Path
import sys

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.nextflow import parse_stage_progress


def test_parse_ppiflow_tqdm_progress_from_runpartialflow_stdout(tmp_path: Path) -> None:
    (tmp_path / ".command.out").write_text(
        "Running inference...\n"
        "\rTesting DataLoader 0:  12%|█▎        | 1/8 [02:42<18:54,  0.01it/s]"
        "\rTesting DataLoader 0:  25%|██▌       | 2/8 [05:20<16:00,  0.01it/s]",
        encoding="utf-8",
    )

    assert parse_stage_progress(str(tmp_path), "runpartialflow") == "PPIFlow sample 2/8 (25%)"


def test_parse_ppiflow_progress_falls_back_to_sample_files(tmp_path: Path) -> None:
    out_dir = tmp_path / "ppiflow_out"
    out_dir.mkdir()
    (out_dir / "sample0.pdb").write_text("MODEL\nEND\n", encoding="utf-8")
    (out_dir / "sample1.pdb").write_text("MODEL\nEND\n", encoding="utf-8")
    (tmp_path / ".command.out").write_text("samples_per_target=8\n", encoding="utf-8")

    assert parse_stage_progress(str(tmp_path), "runpartialflow") == "PPIFlow sample 2/8"
