from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "config/ngs/dorado_v1.3.1.lock.json"
FIXTURES = Path(
    "/mnt/BioModStack/ngs/fixtures/"
    "dorado-v1.3.1-7c84b01de1e46d4c5b2d5208fc430f27579a6c22"
)


def _load_module():
    path = ROOT / "scripts/dorado_p4_preflight.py"
    spec = importlib.util.spec_from_file_location("dorado_p4_preflight", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _preflight(module, pod5: Path, **overrides):
    params = {
        "lock_path": LOCK,
        "pod5_root": pod5,
        "molecule": "dna",
        "quality": "hac",
        "mode": "simplex",
        "model_root": Path("/mnt/BioModStack/models/dorado/1.3.1"),
        "runtime_sif": Path("/home/dalab/biomodstack/biomodstack/apptainer/dorado.sif"),
        "modified_bases": "none",
        "barcode_kit": None,
        "sample_sheet": None,
        "pairs": None,
        "batch_size": None,
        "device": "cuda:0",
        "verify_assets": False,
    }
    params.update(overrides)
    return module.build_preflight(**params)


def _copy_pod5_with_experiment(source: Path, destination: Path, experiment_id: str) -> None:
    pod5 = importlib.import_module("pod5")

    with pod5.Reader(source) as reader, pod5.Writer(destination, software_name="BioModStack P4 acceptance") as writer:
        for record in reader.reads():
            read = record.to_read()
            writer.add_read(replace(read, run_info=replace(read.run_info, experiment_name=experiment_id)))


def test_lock_resolves_only_exact_compatible_models() -> None:
    module = _load_module()
    lock = module.load_lock(LOCK)
    assert lock["schema"] == "biomodstack.dorado_lock.v1"
    assert module.resolve_model(lock, "dna", "fast")["id"] == "dna_r10.4.1_e8.2_400bps_fast@v5.2.0"
    assert module.resolve_model(lock, "dna", "hac")["id"] == "dna_r10.4.1_e8.2_400bps_hac@v5.2.0"
    assert module.resolve_model(lock, "dna", "sup")["id"] == "dna_r10.4.1_e8.2_400bps_sup@v5.2.0"
    assert module.resolve_model(lock, "rna", "sup")["id"] == "rna004_130bps_sup@v5.2.0"
    with pytest.raises(ValueError, match="quality"):
        module.resolve_model(lock, "dna", "dna_r10.4.1_e8.2_400bps_sup@v5.2.0")


def test_checked_in_lock_authorizes_selected_runtime_sif() -> None:
    runtime_value = os.environ.get("BMS_NGS_RUNTIME_SIF")
    if runtime_value is None:
        pytest.skip("BMS_NGS_RUNTIME_SIF is required for runtime identity acceptance")
    module = _load_module()
    lock = module.load_lock(LOCK)
    runtime_sif = Path(runtime_value)
    assert runtime_sif.is_file()
    assert module._sha256(runtime_sif) == lock["dorado"]["sif_sha256"]


def test_dorado_gpu_executes_the_selected_runtime_sif() -> None:
    config = (ROOT / "nextflow.config").read_text(encoding="utf-8")
    block = config.split("withLabel: dorado_gpu {", 1)[1].split("}", 1)[0]
    assert "container = params.dorado_runtime_sif" in block
    assert '${params.container_dir}/dorado.sif' not in block


def test_runtime_scientific_tools_are_version_bound(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    lock = module.load_lock(LOCK)
    responses = {
        ("samtools", "--version"): "samtools 1.24\nUsing htslib 1.24\n",
        ("samtools", "consensus", "--help"): "Usage: samtools consensus [options] <in.bam>\n",
        ("modkit", "--version"): "modkit 0.6.4\n",
        ("/opt/igv-reports/bin/pip", "show", "igv-reports"): "Name: igv-reports\nVersion: 1.16.3\n",
        ("create_report", "--help"): "usage: create_report [options]\n",
    }

    def fake_run(command, **_kwargs):
        key = tuple(command[3:])
        return type("Completed", (), {"returncode": 0, "stdout": responses[key], "stderr": ""})()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    evidence = module._verify_scientific_tools(tmp_path / "runtime.sif", lock)
    assert evidence["consensus_method"]["identity"] == "samtools_1.24_bayesian_consensus"
    assert evidence["samtools"]["output_sha256"]

    responses[("modkit", "--version")] = "modkit 0.6.3\n"
    with pytest.raises(ValueError, match="modkit version mismatch"):
        module._verify_scientific_tools(tmp_path / "runtime.sif", lock)


def test_real_dna_and_rna_fixtures_are_chemistry_qualified() -> None:
    module = _load_module()
    dna = _preflight(module, FIXTURES / "barcode")
    assert dna["schema"] == "biomodstack.dorado_preflight.v1"
    assert dna["inputs"]["sample_rates"] == [5000]
    assert dna["inputs"]["read_count"] == 7
    assert dna["inputs"]["protocol_run_ids"] == ["0d85015e-6a4e-400c-a80f-c187c65a6d03"]
    assert dna["inputs"]["experiment_ids"] == []
    assert dna["inputs"]["flow_cell_ids"] == ["PAO25751"]
    assert dna["selection"]["model_id"] == "dna_r10.4.1_e8.2_400bps_hac@v5.2.0"
    assert dna["execution_policy"]["batch_size"] > 0

    rna = _preflight(
        module,
        FIXTURES / "rna",
        molecule="rna",
        quality="sup",
    )
    assert rna["inputs"]["sample_rates"] == [4000]
    assert rna["selection"]["model_id"] == "rna004_130bps_sup@v5.2.0"


def test_chemistry_rejects_missing_sequencing_kit_metadata() -> None:
    module = _load_module()
    files = module._pod5_files(FIXTURES / "barcode", set())
    inventory, _read_ids = module._read_pod5_inventory(files, FIXTURES / "barcode")
    inventory["sequencing_kits"] = []
    with pytest.raises(ValueError, match="sequencing kits"):
        module._validate_chemistry(module.load_lock(LOCK), "dna", inventory)


def test_dna_rejects_4000hz_rna_fixture_and_mixed_inputs(tmp_path: Path) -> None:
    module = _load_module()
    with pytest.raises(ValueError, match="sample rate"):
        _preflight(module, FIXTURES / "rna", molecule="dna")

    mixed = tmp_path / "mixed"
    mixed.mkdir()
    shutil.copy2(next((FIXTURES / "barcode").glob("*.pod5")), mixed / "dna.pod5")
    shutil.copy2(next((FIXTURES / "rna").glob("*.pod5")), mixed / "rna.pod5")
    with pytest.raises(ValueError, match="mixed|sample rate"):
        _preflight(module, mixed)


def test_pod5_inventory_rejects_fast5_empty_and_symlink_escape(tmp_path: Path) -> None:
    module = _load_module()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="POD5"):
        _preflight(module, empty)

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "legacy.fast5").write_bytes(b"not-fast5")
    with pytest.raises(ValueError, match="FAST5"):
        _preflight(module, bad)

    escaped = tmp_path / "escaped"
    escaped.mkdir()
    (escaped / "outside.pod5").symlink_to(next((FIXTURES / "barcode").glob("*.pod5")))
    with pytest.raises(ValueError, match="symlink"):
        _preflight(module, escaped)


def test_duplex_requires_confined_valid_pairs_and_rejects_rna(tmp_path: Path) -> None:
    module = _load_module()
    duplex_root = FIXTURES / "duplex"
    with pytest.raises(ValueError, match="pairs"):
        _preflight(module, duplex_root, mode="duplex")
    with pytest.raises(ValueError, match="duplex.*RNA|RNA.*duplex"):
        _preflight(module, FIXTURES / "rna", molecule="rna", mode="duplex")

    source_pairs = duplex_root / "pairs.txt"
    accepted = _preflight(module, duplex_root, mode="duplex", pairs=source_pairs)
    assert accepted["selection"]["stereo_model_id"] == "dna_r10.4.1_e8.2_5khz_stereo@v1.4"
    assert accepted["pairs"]["pair_count"] == 2

    outside = tmp_path / "outside.txt"
    outside.write_text(source_pairs.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="confined"):
        _preflight(module, duplex_root, mode="duplex", pairs=outside)


def test_barcode_and_sample_sheet_are_fail_closed(tmp_path: Path) -> None:
    module = _load_module()
    pod5_root = tmp_path / "pod5"
    pod5_root.mkdir()
    _copy_pod5_with_experiment(
        next((FIXTURES / "barcode").glob("*.pod5")),
        pod5_root / "barcoded.pod5",
        "BMS_P4_SAMPLE",
    )
    accepted = _preflight(module, pod5_root, barcode_kit="SQK-RBK114-96")
    assert accepted["barcoding"]["kit"] == "SQK-RBK114-96"

    with pytest.raises(ValueError, match="barcode kit"):
        _preflight(module, pod5_root, barcode_kit="SQK-UNKNOWN")

    sheet = pod5_root / "sample_sheet.csv"
    sheet.write_text(
        "experiment_id,kit,flow_cell_id,barcode,alias\n"
        "BMS_P4_SAMPLE,SQK-RBK114-96,PAO25751,barcode01,clone_a\n",
        encoding="utf-8",
    )
    try:
        accepted_sheet = _preflight(
            module,
            pod5_root,
            barcode_kit="SQK-RBK114-96",
            sample_sheet=sheet,
        )
        assert accepted_sheet["barcoding"]["sample_sheet_sha256"]
        sheet.write_text(
            "experiment_id,kit,flow_cell_id,barcode,alias\n"
            "BMS_P4_SAMPLE,SQK-RBK114-96,PAO25751,barcode01,../escape\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="malformed|incompatible"):
            _preflight(module, pod5_root, barcode_kit="SQK-RBK114-96", sample_sheet=sheet)
        sheet.write_text(
            "experiment_id,kit,flow_cell_id,barcode,alias\n"
            "BMS_P4_SAMPLE,SQK-UNKNOWN,PAO25751,barcode01,clone_a\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="malformed|incompatible"):
            _preflight(module, pod5_root, barcode_kit="SQK-RBK114-96", sample_sheet=sheet)
        sheet.write_text(
            "experiment_id,kit,flow_cell_id,barcode,alias\n"
            "WRONGEXP,SQK-RBK114-96,WRONGFLOW,barcode01,clone_a\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="malformed|incompatible"):
            _preflight(module, pod5_root, barcode_kit="SQK-RBK114-96", sample_sheet=sheet)
    finally:
        sheet.unlink()

    outside = tmp_path / "sample_sheet.csv"
    outside.write_text(
        "experiment_id,kit,flow_cell_id,barcode,alias\n"
        "BMS_P4_SAMPLE,SQK-RBK114-96,PAO25751,barcode01,clone_a\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="confined"):
        _preflight(
            module,
            pod5_root,
            barcode_kit="SQK-RBK114-96",
            sample_sheet=outside,
        )


def test_sample_sheet_rejects_pod5_without_dorado_experiment_metadata(tmp_path: Path) -> None:
    module = _load_module()
    pod5_root = tmp_path / "pod5"
    pod5_root.mkdir()
    shutil.copy2(next((FIXTURES / "barcode").glob("*.pod5")), pod5_root)
    sheet = pod5_root / "sample_sheet.csv"
    sheet.write_text(
        "experiment_id,kit,flow_cell_id,barcode,alias\n"
        "0d85015e-6a4e-400c-a80f-c187c65a6d03,SQK-RBK114-96,PAO25751,barcode01,clone_a\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed|incompatible"):
        _preflight(module, pod5_root, barcode_kit="SQK-RBK114-96", sample_sheet=sheet)


def test_sample_sheet_matches_pinned_dorado_grammar_and_observed_tuple(tmp_path: Path) -> None:
    module = _load_module()
    root = tmp_path / "pod5"
    root.mkdir()
    sheet = root / "sample_sheet.csv"
    inventory = {
        "experiment_ids": ["EXP_A", "EXP_B"],
        "sample_sheet_indexes": [
            {"experiment_id": "EXP_A", "flow_cell_id": "FLOW_A", "position_id": "POS_A"},
            {"experiment_id": "EXP_B", "flow_cell_id": "FLOW_B", "position_id": "POS_B"},
        ],
    }

    def validate(rows: str):
        sheet.write_text("experiment_id,kit,flow_cell_id,position_id,barcode,alias\n" + rows, encoding="utf-8")
        return module._validate_sample_sheet(sheet, root, "SQK-RBK114-96", inventory)

    assert validate("EXP_A,SQK-RBK114-96,FLOW_A,POS_A,barcode01,clone_a\n")["assignments"]
    invalid_rows = (
        "EXP_A,SQK-RBK114-96,FLOW_A,POS_A,barcode01,clone.a\n",
        "EXP_A,SQK-RBK114-96,FLOW_A,POS_A,barcode01,unclassified\n",
        "EXP_A,SQK-RBK114-96,FLOW_A,POS_A,barcode01,barcode123\n",
        "EXP_A,SQK-RBK114-96,FLOW_A,POS_A,barcode99,clone_a\n",
        "EXP_A,SQK-RBK114-96,FLOW_B,POS_B,barcode01,clone_a\n",
        "EXP_A,SQK-RBK114-96,FLOW_A,POS_A,barcode01,clone_a\nEXP_B,SQK-RBK114-96,FLOW_B,POS_B,barcode02,clone_b\n",
    )
    for rows in invalid_rows:
        with pytest.raises(ValueError, match="malformed|incompatible|exactly one"):
            validate(rows)

    literal_syntax_rejections = (
        " EXP_A,SQK-RBK114-96,FLOW_A,POS_A,barcode01,clone_a\n",
        '"EXP_A",SQK-RBK114-96,FLOW_A,POS_A,barcode01,clone_a\n',
        "EXP_A,SQK-RBK114-96,FLOW_A,POS_A,barcode01,clone_a,extra\n",
        "EXP_A,SQK-RBK114-96,FLOW_A,POS_A,barcode01,clone_a,\n",
    )
    for rows in literal_syntax_rejections:
        with pytest.raises(ValueError, match="syntax|cardinality|literal"):
            validate(rows)

    sheet.write_text(
        "experiment_id,kit,flow_cell_id,position_id,barcode,alias,alias\n"
        "EXP_A,SQK-RBK114-96,FLOW_A,POS_A,barcode01,clone_a,clone_b\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        module._validate_sample_sheet(sheet, root, "SQK-RBK114-96", inventory)

    mixed_eol = (
        b"experiment_id,kit,flow_cell_id,position_id,barcode,alias\n"
        b"EXP_A,SQK-RBK114-96,FLOW_A,POS_A,barcode01,clone_a\r\n"
    )
    sheet.write_bytes(mixed_eol)
    with pytest.raises(ValueError, match="syntax|line ending"):
        module._validate_sample_sheet(sheet, root, "SQK-RBK114-96", inventory)

    reverse_mixed_eol = (
        b"experiment_id,kit,flow_cell_id,position_id,barcode,alias\r\n"
        b"EXP_A,SQK-RBK114-96,FLOW_A,POS_A,barcode01,clone_a\n"
    )
    sheet.write_bytes(reverse_mixed_eol)
    with pytest.raises(ValueError, match="syntax|line ending"):
        module._validate_sample_sheet(sheet, root, "SQK-RBK114-96", inventory)

    canonical_crlf = (
        b"experiment_id,kit,flow_cell_id,position_id,barcode,alias\r\n"
        b"EXP_A,SQK-RBK114-96,FLOW_A,POS_A,barcode01,clone_a\r\n"
    )
    sheet.write_bytes(canonical_crlf)
    assert module._validate_sample_sheet(sheet, root, "SQK-RBK114-96", inventory)["assignments"]


def test_asset_verification_detects_tampered_model(tmp_path: Path) -> None:
    module = _load_module()
    lock = module.load_lock(LOCK)
    model = module.resolve_model(lock, "dna", "fast")
    model_dir = tmp_path / model["id"]
    model_dir.mkdir()
    (model_dir / "config.toml").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="model.*identity|identity.*model"):
        module.verify_model_identity(model, model_dir)


def test_min_qscore_zero_is_preserved_and_out_of_range_fails() -> None:
    module = _load_module()
    payload = _preflight(module, FIXTURES / "barcode", min_qscore=0)
    assert payload["execution_policy"]["min_qscore"] == 0
    with pytest.raises(ValueError, match="min_qscore"):
        _preflight(module, FIXTURES / "barcode", min_qscore=31)
