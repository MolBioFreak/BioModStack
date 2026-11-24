"""Sample names at run start, and opening a finished barcoded run in the ONT form."""
from __future__ import annotations

from pathlib import Path

import pytest

import paths
from services import ont_run_control


def test_sample_names_pair_kit_barcodes_with_unique_names(monkeypatch) -> None:
    monkeypatch.setattr(ont_run_control, "_barcoding_kits", lambda: frozenset({"SQK-RBK114-24"}))
    pairs = [{"barcode": "barcode02", "alias": "b"}, {"barcode": "barcode01", "alias": "a"}]
    assert ont_run_control._barcode_samples(pairs, "SQK-RBK114-24") == [
        {"barcode": "barcode01", "alias": "a"}, {"barcode": "barcode02", "alias": "b"}]
    assert ont_run_control._barcode_samples(None, "SQK-LSK114") == []
    for bad, kit in (([{"barcode": "barcode25", "alias": "a"}], "SQK-RBK114-24"),
                     ([{"barcode": "barcode01", "alias": "a"}, {"barcode": "barcode01", "alias": "b"}], "SQK-RBK114-24"),
                     ([{"barcode": "barcode01", "alias": "a b"}], "SQK-RBK114-24"),
                     ([{"barcode": "barcode01", "alias": "a"}], "SQK-LSK114")):
        with pytest.raises(ValueError, match="barcod"):
            ont_run_control._barcode_samples(bad, kit)


@pytest.mark.asyncio
async def test_a_finished_run_opens_with_its_pod5_folder_kit_and_names(monkeypatch, tmp_path: Path) -> None:
    runs, inputs = tmp_path / "runs", tmp_path / "inputs"
    folder = runs / "group" / "sample" / "20261116_run"
    (folder / "pod5_pass" / "barcode01").mkdir(parents=True); inputs.mkdir()
    files = [folder / "pod5_pass" / "barcode01" / "a.pod5", folder / "pod5_skip.pod5"]
    record = type("Run", (), {"state": "completed", "kit": "SQK-RBK114-24", "output_files": {"pod5": [str(p) for p in files]}})()
    event = type("Event", (), {"event_type": "preflight_armed", "minknow_payload": {
        "barcode_samples": [{"barcode": "barcode01", "alias": "plasmid_A"}]}})()

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    async def load(_session, _run_id): return record
    async def events(_session, _run_id): return [event]
    monkeypatch.setattr(ont_run_control, "async_session", lambda: Session())
    monkeypatch.setattr(ont_run_control, "_load_run", load)
    monkeypatch.setattr(ont_run_control, "_events_for_run", events)
    monkeypatch.setattr(ont_run_control, "_barcoding_kits", lambda: frozenset({"SQK-RBK114-24"}))
    monkeypatch.setattr(ont_run_control, "get_inputs_dir", lambda: inputs)
    monkeypatch.setattr(paths, "get_allowed_roots", lambda: {"sequencer_runs": runs, "inputs": inputs})
    handoff = await ont_run_control.build_barcode_basecall_handoff("run-1")
    assert handoff["pod5_dir"] == "sequencer_runs/group/sample/20261116_run"
    assert handoff["barcode_kit"] == "SQK-RBK114-24"
    sheet = inputs / handoff["sample_sheet"].removeprefix("inputs/")
    assert sheet.read_text() == "experiment_id,kit,flow_cell_id,position_id,barcode,alias\n,SQK-RBK114-24,,,barcode01,plasmid_A\n"
    record.state = "running"
    with pytest.raises(ValueError, match="finish"):
        await ont_run_control.build_barcode_basecall_handoff("run-1")
