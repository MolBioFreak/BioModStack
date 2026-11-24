"""Four barcode kits; every place that named one kit now follows the Dorado lock's list."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.ont_ngs_contract import accepted_barcode_kits, normalize_ont_launch_params

REPO = Path(__file__).resolve().parents[3]
KITS = ("SQK-RBK114-24", "SQK-RBK114-96", "SQK-NBD114-24", "SQK-NBD114-96")


def test_the_lock_lists_the_four_kits_and_no_file_names_a_single_kit() -> None:
    assert accepted_barcode_kits() == frozenset(KITS)
    for path in ("platform/api/routers/ont_runs.py", "platform/api/services/ont_ngs_completion.py"):
        assert "SQK-" not in (REPO / path).read_text(encoding="utf-8")
    schema = json.loads((REPO / "schemas/ngs_molbio/ngs-ont-basecall_dna-v1.schema.json").read_text())
    assert schema["properties"]["barcode_kit"]["anyOf"][0]["enum"] == list(KITS)


@pytest.mark.parametrize("kit", KITS)
def test_each_kit_is_accepted_and_both_ends_applies_to_native_kits_only(kit: str) -> None:
    params = normalize_ont_launch_params("ont_basecall_dna", {
        "barcode_kit": kit, "barcode_both_ends": True, "run_modkit": False, "run_fastq_qc": False})
    assert params["barcode_kit"] == kit
    assert params.get("barcode_both_ends", False) is kit.startswith("SQK-NBD")
    assert "barcode_both_ends" not in normalize_ont_launch_params("ont_basecall_dna", {
        "barcode_kit": kit, "run_modkit": False, "run_fastq_qc": False})


def test_a_kit_outside_the_list_is_still_named_as_unsupported() -> None:
    with pytest.raises(ValueError, match="unsupported barcode kit"):
        normalize_ont_launch_params("ont_basecall_dna", {"barcode_kit": "SQK-PCB114-24", "run_modkit": False, "run_fastq_qc": False})


def test_the_both_ends_choice_reaches_the_dorado_command_through_the_preflight() -> None:
    text = (REPO / "modules/ngs/dorado_basecall.nf").read_text(encoding="utf-8")
    assert 'optional += "--barcode-both-ends"' in text
    assert "command+=(--barcode-both-ends)" in text
    preflight = (REPO / "scripts/dorado_p4_preflight.py").read_text(encoding="utf-8")
    assert '"both_ends": True' in preflight and '"--barcode-both-ends"' in preflight


def test_a_project_setup_can_carry_the_both_ends_and_multi_run_settings() -> None:
    schema = json.loads((REPO / "schemas/ngs_molbio/ngs-ont-basecall_dna-v1.schema.json").read_text())
    for key in ("barcode_both_ends", "allow_multiple_pod5_runs"):
        assert schema["properties"][key]["type"] == "boolean" and schema["properties"][key]["default"] is False
