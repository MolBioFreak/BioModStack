from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_msa.mmseqs_stage_report import command_report, effective_gpu_stages  # noqa: E402


def test_command_report_detects_gpu_native_split_search(tmp_path: Path) -> None:
    report = command_report(
        "envdb_search",
        tmp_path / "mmseqs-gpu-blackwell/bin/mmseqs",
        [
            "search",
            "profile",
            "colabfold_envdb_202108_db",
            "res",
            "tmp",
            "--gpu",
            "1",
            "--prefilter-mode",
            "1",
            "--db-load-mode",
            "2",
            "--split",
            "4",
            "--split-mode",
            "0",
            "--threads",
            "32",
        ],
    )

    payload = report.to_json()
    assert payload["module"] == "search"
    assert payload["binary_kind"] == "gpu"
    assert payload["target_db"] == "colabfold_envdb_202108_db"
    assert payload["uses_gpu_flag"] is True
    assert payload["uses_gpu_server"] is False
    assert payload["prefilter_mode"] == 1
    assert payload["db_load_mode"] == 2
    assert payload["split_count"] == 4
    assert payload["split_mode"] == 0
    assert payload["threads"] == 32


def test_command_report_detects_cpu_native_split_search(tmp_path: Path) -> None:
    report = command_report(
        "envdb_search",
        tmp_path / "mmseqs-cpu/bin/mmseqs",
        [
            "search",
            "profile",
            "colabfold_envdb_202108_db",
            "res",
            "tmp",
            "--db-load-mode",
            "2",
            "--split",
            "4",
            "--split-mode",
            "0",
            "--threads",
            "32",
        ],
    )

    payload = report.to_json()
    assert payload["binary_kind"] == "cpu"
    assert payload["uses_gpu_flag"] is False
    assert payload["uses_gpu_server"] is False
    assert payload["split_count"] == 4
    assert payload["threads"] == 32


def test_command_report_detects_gpuserver_search(tmp_path: Path) -> None:
    report = command_report(
        "uniref_search",
        tmp_path / "mmseqs-gpu/bin/mmseqs",
        [
            "search",
            "qdb",
            "uniref30_2302_db",
            "res",
            "tmp",
            "--gpu",
            "1",
            "--gpu-server",
            "1",
            "--gpu-server-wait-timeout",
            "120",
            "--prefilter-mode",
            "1",
            "--threads",
            "32",
        ],
    )

    payload = report.to_json()
    assert payload["uses_gpu_flag"] is True
    assert payload["uses_gpu_server"] is True
    assert payload["gpu_server_wait_timeout"] == 120
    assert payload["target_db"] == "uniref30_2302_db"


def test_effective_gpu_stages_are_derived_from_stage_reports(tmp_path: Path) -> None:
    reports = [
        command_report("createdb", tmp_path / "cpu/mmseqs", ["createdb", "in", "qdb"]).to_json(),
        command_report(
            "envdb_search",
            tmp_path / "gpu/mmseqs",
            ["search", "q", "env", "res", "tmp", "--gpu", "1"],
        ).to_json(),
    ]

    assert effective_gpu_stages(reports) == ["envdb_search"]
