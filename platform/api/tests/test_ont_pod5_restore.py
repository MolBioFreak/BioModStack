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
    monkeypatch.setattr(
        restore, "local_image_identity", lambda env_name, runtime: ("sha256:" + "a" * 64, "a" * 64)
    )
    monkeypatch.setattr(restore.shutil, "which", lambda name: "/usr/bin/" + name)
    calls: list[list[str]] = []
    reads = {"count": "12"}

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "s2p" in command:  # the converter writes its output file
            target = restore.restore_dir("ont-raw-rep-test") / command[-1].removeprefix("/out/")
            target.write_bytes(b"pod5")
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
    assert convert[-4:] == ["s2p", "/in/reads.blow5", "-o", "/out/partial/restored-000.pod5"]
    folder = restore.restore_dir(representation.id)
    assert (folder / "restored-000.pod5").is_file() and not (folder / "partial").exists()
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


def _two_partitions(tmp_path: Path) -> SimpleNamespace:
    representation = _representation(tmp_path)
    second = tmp_path / "more.blow5"
    second.write_bytes(b"y" * 10)
    representation.artifact_manifest["artifacts"][0]["read_count"] = 5
    representation.artifact_manifest["artifacts"].append({"kind": "blow5", "path": str(second), "bytes": 10, "read_count": 7})
    return representation


def _interrupted_folder(representation_id: str, owner: dict | None) -> Path:
    """What an API that stopped part-way leaves behind: one finished file, one half-written one, state running."""
    folder = restore.restore_dir(representation_id)
    (folder / "partial").mkdir(parents=True)
    (folder / "restored-000.pod5").write_bytes(b"finished before the stop")
    (folder / "partial" / "restored-001.pod5").write_bytes(b"half")
    status = {"representation_id": representation_id, "run_id": "run-1", "observed_generation": 1, "state": "running",
              "directory": str(folder), "image": "sha256:" + "a" * 64, "completed_files": ["restored-000.pod5"]}
    if owner is not None:
        status["owner"] = owner
    (folder / restore.STATUS_NAME).write_text(json.dumps(status))
    return folder


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [None, {"pid": 2 ** 22 + 12345, "started": "1"}], ids=["no owner recorded", "owner gone"])
async def test_a_restore_interrupted_by_a_restart_continues_and_keeps_what_was_finished(configured, tmp_path: Path, owner) -> None:
    representation = _two_partitions(tmp_path)
    folder = _interrupted_folder(representation.id, owner)
    interrupted = restore.restore_status(representation.id)
    assert interrupted["state"] == "interrupted" and "Request it again" in interrupted["reason"]
    started = await restore.request_restore(representation)
    assert (started["state"], started["attempt"], started["completed_files"]) == ("running", 2, ["restored-000.pod5"])
    status = await _finish(representation.id)
    assert status["state"] == "ready" and status["files"] == ["restored-000.pod5", "restored-001.pod5"]
    conversions = [command for command in configured[1:] if "s2p" in command]
    assert [command[-1] for command in conversions] == ["/out/partial/restored-001.pod5"]
    assert (folder / "restored-000.pod5").read_bytes() == b"finished before the stop"
    assert (folder / "restored-001.pod5").read_bytes() == b"pod5" and not (folder / "partial").exists()
    # Asking once more returns the finished result without converting anything.
    assert (await restore.request_restore(representation))["state"] == "ready"
    assert len([command for command in configured[1:] if "s2p" in command]) == 1


@pytest.mark.asyncio
async def test_a_restore_that_is_still_running_is_left_alone(configured, tmp_path: Path) -> None:
    import os
    representation = _two_partitions(tmp_path)
    # Running in another live process: this test's parent.
    parent = {"pid": os.getppid(), "started": restore._process_start(os.getppid())}
    assert parent["started"] is not None
    _interrupted_folder(representation.id, parent)
    assert restore.restore_status(representation.id)["state"] == "running"
    assert (await restore.request_restore(representation))["state"] == "running"
    assert representation.id not in restore._TASKS and not [command for command in configured[1:] if "s2p" in command]


@pytest.mark.asyncio
async def test_two_requests_while_one_restore_runs_start_one_conversion(configured, tmp_path: Path) -> None:
    representation = _representation(tmp_path)
    first = await restore.request_restore(representation)
    second = await restore.request_restore(representation)
    assert first["state"] == second["state"] == "running"
    status = await _finish(representation.id)
    assert status["state"] == "ready"
    assert len([command for command in configured[1:] if "s2p" in command]) == 1
