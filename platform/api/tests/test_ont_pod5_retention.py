from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import ont_raw_signal


@pytest.fixture()
def staging(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "data" / "ont-raw-signal-staging"
    monkeypatch.setenv(ont_raw_signal.BLOW5_STAGING_ROOT_ENV, str(root))
    monkeypatch.delenv(ont_raw_signal.RAW_SIGNAL_RETENTION_POLICY_ENV, raising=False)
    return root


class _Session:
    async def flush(self) -> None:
        return None


def _pod5(tmp_path: Path, name: str = "reads.pod5") -> tuple[SimpleNamespace, Path]:
    folder = tmp_path / "inputs"
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_bytes(b"pod5-signal")
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        artifact = ont_raw_signal._file_artifact(
            path, "a1", kind="pod5", governed_root_path=folder, governed_root_fd=fd, governed_relative_path=name,
        )
    finally:
        os.close(fd)
    source = SimpleNamespace(
        id="src", format="pod5", artifact_manifest={"artifacts": [artifact]},
        validation_receipts={}, reason_code="registered",
    )
    return source, path


def _blow5(source_id: str = "src") -> SimpleNamespace:
    return SimpleNamespace(id="b5", format="blow5", state="ready", parent_representation_ids=[source_id])


def test_policy_defaults_to_installation_and_persists_operator_choice(staging: Path, monkeypatch) -> None:
    assert ont_raw_signal.retention_settings()["policy"] == "pod5_and_blow5"
    monkeypatch.setenv(ont_raw_signal.RAW_SIGNAL_RETENTION_POLICY_ENV, "blow5_only")
    assert ont_raw_signal.raw_signal_retention_policy() == "blow5_only"
    saved = ont_raw_signal.save_raw_signal_retention_policy("pod5_and_blow5")
    assert saved == {"policy": "pod5_and_blow5", "remove_pod5_after_verified_blow5": False,
                     "installation_default": "blow5_only"}
    assert ont_raw_signal.retention_settings_path() == staging.parent / "ont-raw-signal-retention.json"
    with pytest.raises(ValueError):
        ont_raw_signal.save_raw_signal_retention_policy("delete_everything")


@pytest.mark.asyncio
async def test_keep_policy_never_removes(staging: Path, tmp_path: Path) -> None:
    source, path = _pod5(tmp_path)
    assert await ont_raw_signal.apply_source_retention(_Session(), source, _blow5()) is None
    assert path.exists()


@pytest.mark.asyncio
async def test_remove_policy_deletes_exact_converted_pod5_and_records_it(staging: Path, tmp_path: Path) -> None:
    ont_raw_signal.save_raw_signal_retention_policy("blow5_only")
    source, path = _pod5(tmp_path)
    outcome = await ont_raw_signal.apply_source_retention(_Session(), source, _blow5())
    assert not path.exists()
    assert outcome["removed_bytes"] == len(b"pod5-signal") and outcome["errors"] == []
    assert source.validation_receipts["source_removed"]["blow5_representation_id"] == "b5"
    assert source.reason_code == "pod5_removed_after_verified_blow5"


@pytest.mark.asyncio
async def test_remove_policy_keeps_changed_files_and_unrelated_blow5(staging: Path, tmp_path: Path) -> None:
    ont_raw_signal.save_raw_signal_retention_policy("blow5_only")
    source, path = _pod5(tmp_path)
    assert await ont_raw_signal.apply_source_retention(_Session(), source, _blow5("other")) is None
    path.write_bytes(b"pod5-signal-changed")
    outcome = await ont_raw_signal.apply_source_retention(_Session(), source, _blow5())
    assert path.exists() and outcome["removed_files"] == [] and outcome["errors"]
    assert "source_removal_incomplete" in source.validation_receipts
