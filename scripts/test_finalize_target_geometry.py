from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parent

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from finalize_target_geometry import iter_prediction_records


def test_iter_prediction_records_finds_nested_protenix_outputs(tmp_path: Path) -> None:
    nested = tmp_path / "Sample_Sequence_006_Sample_Sequence_006" / "seed_42" / "predictions"
    nested.mkdir(parents=True)

    summary = nested / "Sample_Sequence_006_Sample_Sequence_006_summary_confidence_sample_0.json"
    structure = nested / "Sample_Sequence_006_Sample_Sequence_006_sample_0.cif"
    summary.write_text("{}", encoding="utf-8")
    structure.write_text("data_test", encoding="utf-8")

    records = iter_prediction_records(tmp_path, "protenix")

    assert records == [
        (
            "Sample_Sequence_006_Sample_Sequence_006_sample_0",
            structure,
            summary,
        )
    ]
