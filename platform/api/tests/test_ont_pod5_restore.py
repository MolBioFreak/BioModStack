from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import ont_pod5_restore as restore


def _representation(tmp_path: Path, *, state: str = "ready", fmt: str = "blow5") -> SimpleNamespace:
    blow5 = tmp_path / "reads.blow5"
    blow5.write_bytes(b"x" * 10)
    return SimpleNamespace(
        id="ont-raw-rep-test", run_id="run-1", observed_generation=1, format=fmt, state=state,
        artifact_manifest={"artifacts": [{"kind": "blow5", "path": str(blow5), "bytes": 10, "read_count": 12}]},
    )


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    monkeypatch.setattr(restore, "get_inputs_dir", lambda: tmp_path / "inputs")
    monkeypatch.setenv("BMS_ONT_CONTAINER_RUNTIME", "podman")
    monkeypatch.setenv("BMS_ONT_SLOW5TOOLS_IMAGE", "sha256:" + "a" * 64)
    monkeypatch.setenv("BMS_ONT_SLOW5TOOLS_IMAGE_DIGEST", "a" * 64)
    monkeypatch.setattr(restore.shutil, "which", lambda name: "/usr/bin/" + name)
    calls: list[list[str]] = []
    reads = {"count": "12"}

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        stdout = reads["count"] if "python3" in command else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(restore, "_run", fake_run)
    calls.append(["__reads__", reads])  # type: ignore[list-item]
    return calls


async def _finish(representation_id: str) -> dict:
    await asyncio.gather(*restore._TASKS.values())
    return restore.restore_status(representation_id)


@pytest.mark.asyncio
async def test_restore_runs_blue_crab_and_checks_read_count(configured, tmp_path: Path) -> None:
    representation = _representation(tmp_path)
    started = await restore.request_restore(representation)
    assert started["state"] == "running"
    status = await _finish(representation.id)
    assert status["state"] == "ready" and status["restored_read_count"] == 12
    convert = next(command for command in configured[1:] if "s2p" in command)
    assert convert[-4:] == ["s2p", "/in/reads.blow5", "-o", "/out/restored-000.pod5"]
    assert "--network=none" in convert and "sha256:" + "a" * 64 in convert
    on_disk = json.loads((restore.restore_dir(representation.id) / restore.STATUS_NAME).read_text())
    assert on_disk["state"] == "ready"


@pytest.mark.asyncio
async def test_restore_reports_read_count_mismatch(configured, tmp_path: Path) -> None:
    configured[0][1]["count"] = "11"
    representation = _representation(tmp_path)
    await restore.request_restore(representation)
    status = await _finish(representation.id)
    assert status["state"] == "failed" and "11 reads" in status["error"]


@pytest.mark.asyncio
async def test_restore_requires_ready_blow5(configured, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ready BLOW5"):
        await restore.request_restore(_representation(tmp_path, fmt="pod5"))
    assert restore.restore_status("never-requested")["state"] == "not_requested"
