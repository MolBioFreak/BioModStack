from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi import HTTPException

from routers import molbio_ops


def _clear_plannotate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [
        "BMS_PLANNOTATE_BIN",
        "BMS_MICROMAMBA_BIN",
        "BMS_MICROMAMBA_ROOT_PREFIX",
        "BMS_PLANNOTATE_ENV",
        "BMS_PLANNOTATE_SENSITIVE_YAML",
    ]:
        monkeypatch.delenv(name, raising=False)


def test_auto_annotate_command_reports_missing_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_plannotate_env(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setenv("BMS_PLANNOTATE_SENSITIVE_YAML", str(tmp_path / "missing.yml"))

    with pytest.raises(HTTPException) as exc_info:
        molbio_ops._build_plannotate_command(
            input_file=str(tmp_path / "input.fasta"),
            output_dir=str(tmp_path),
            is_linear=True,
            detailed=False,
        )

    assert exc_info.value.status_code == 503
    assert "pLannotate is not available" in str(exc_info.value.detail)
    assert "micromamba" in str(exc_info.value.detail)


def test_auto_annotate_command_uses_configured_micromamba(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_plannotate_env(monkeypatch)
    fake_micromamba = tmp_path / "micromamba"
    fake_micromamba.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_micromamba.chmod(0o755)
    sensitive_yaml = tmp_path / "sensitive.yml"
    sensitive_yaml.write_text("databases: []\n", encoding="utf-8")

    monkeypatch.setenv("BMS_MICROMAMBA_BIN", str(fake_micromamba))
    monkeypatch.setenv("BMS_MICROMAMBA_ROOT_PREFIX", "/opt/bms-micromamba")
    monkeypatch.setenv("BMS_PLANNOTATE_ENV", "custom-plannotate")
    monkeypatch.setenv("BMS_PLANNOTATE_SENSITIVE_YAML", str(sensitive_yaml))
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    cmd = molbio_ops._build_plannotate_command(
        input_file=str(tmp_path / "input.fasta"),
        output_dir=str(tmp_path / "out"),
        is_linear=True,
        detailed=True,
    )

    assert cmd[:7] == [
        str(fake_micromamba),
        "run",
        "--root-prefix",
        "/opt/bms-micromamba",
        "-n",
        "custom-plannotate",
        "plannotate",
    ]
    assert cmd[7:13] == ["batch", "-i", str(tmp_path / "input.fasta"), "-o", str(tmp_path / "out"), "--csv"]
    assert cmd[-4:] == ["-y", str(sensitive_yaml), "-l", "-d"]


def test_auto_annotate_command_can_use_direct_plannotate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_plannotate_env(monkeypatch)
    fake_plannotate = tmp_path / "plannotate"
    fake_plannotate.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_plannotate.chmod(0o755)

    monkeypatch.setenv("BMS_PLANNOTATE_BIN", str(fake_plannotate))
    monkeypatch.setenv("BMS_PLANNOTATE_SENSITIVE_YAML", str(tmp_path / "missing.yml"))
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    cmd = molbio_ops._build_plannotate_command(
        input_file=str(tmp_path / "input.fasta"),
        output_dir=str(tmp_path / "out"),
        is_linear=False,
        detailed=False,
    )

    assert cmd == [
        str(fake_plannotate),
        "batch",
        "-i",
        str(tmp_path / "input.fasta"),
        "-o",
        str(tmp_path / "out"),
        "--csv",
    ]


def test_plannotate_empty_hit_error_is_treated_as_no_features() -> None:
    stderr = "ValueError: Cannot set a DataFrame without columns to the column feat loc"

    assert molbio_ops._plannotate_error_means_no_features(stderr, "") is True
    assert molbio_ops._plannotate_error_means_no_features("blast crashed", "") is False


@pytest.mark.asyncio
async def test_auto_annotate_subprocess_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import subprocess
    import time
    from types import SimpleNamespace

    monkeypatch.setattr(
        molbio_ops,
        "_build_plannotate_command",
        lambda **_kwargs: ["fake-plannotate"],
    )

    def slow_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        time.sleep(0.15)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", slow_run)
    task = asyncio.create_task(
        molbio_ops.auto_annotate(
            molbio_ops.AutoAnnotateRequest(
                sequence="ATGCGT",
                is_linear=True,
                detailed=False,
                min_identity=50.0,
            ),
        ),
    )
    await asyncio.sleep(0.02)

    assert task.done() is False
    response = await task
    assert response.features == []
    assert response.message == "No features detected"
