"""Restore POD5 from a ready BLOW5 representation for Dorado basecalling.

BLOW5 is the retained raw-signal format; Dorado reads POD5. This writes POD5
next to the inputs (``ont-raw-signal/restored-pod5/<representation_id>/``) with
blue-crab s2p from the pinned raw-signal image, then checks the restored read
count against the BLOW5 manifest. Status is a small JSON file in that folder.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database import OntRawSignalRepresentation
from paths import get_inputs_dir
from services.container_cli_environment import container_cli_environment, container_user_namespace_args
from services.ont_raw_signal import (
    BLOW5_CONTAINER_DIGEST_ENV,
    BLOW5_CONTAINER_ENV,
    BLOW5_CONTAINER_RUNTIME_ENV,
)

STATUS_NAME = "restore-status.json"
_TASKS: dict[str, asyncio.Task[None]] = {}


def restore_dir(representation_id: str) -> Path:
    return get_inputs_dir() / "ont-raw-signal" / "restored-pod5" / representation_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_status(directory: Path, status: dict[str, Any]) -> None:
    temporary = directory / f".{STATUS_NAME}.tmp"
    temporary.write_text(json.dumps(status, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, directory / STATUS_NAME)


def restore_status(representation_id: str) -> dict[str, Any]:
    path = restore_dir(representation_id) / STATUS_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"representation_id": representation_id, "state": "not_requested"}


def _blow5_partitions(representation: OntRawSignalRepresentation) -> list[dict[str, Any]]:
    manifest = representation.artifact_manifest if isinstance(representation.artifact_manifest, dict) else {}
    raw = manifest.get("artifacts")
    artifacts: list[Any] = raw if isinstance(raw, list) else []
    partitions = [item for item in artifacts if isinstance(item, dict) and item.get("kind") == "blow5" and item.get("path")]
    if not partitions:
        raise ValueError("representation has no BLOW5 artifacts")
    for item in partitions:
        path = Path(str(item["path"]))
        if not path.is_file() or path.stat().st_size != int(item.get("bytes") or -1):
            raise ValueError("a BLOW5 artifact is missing or changed size since publication")
    return partitions


def _image() -> tuple[str, str]:
    runtime = os.getenv(BLOW5_CONTAINER_RUNTIME_ENV, "docker").strip() or "docker"
    image = os.getenv(BLOW5_CONTAINER_ENV, "").strip()
    digest = os.getenv(BLOW5_CONTAINER_DIGEST_ENV, "").strip()
    if not image or not digest:
        raise ValueError("raw-signal container image is not configured")
    reference = image if image.startswith("sha256:") else f"{image}@sha256:{digest}"
    if shutil.which(runtime) is None:
        raise ValueError(f"container runtime {runtime} is unavailable")
    return runtime, reference


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, env=container_cli_environment(), timeout=6 * 3600)


def _restore(directory: Path, partitions: list[dict[str, Any]], runtime: str, image: str, status: dict[str, Any]) -> None:
    common = [
        runtime, "run", *container_user_namespace_args(runtime), "--rm", "--pull=never", "--network=none",
        "--read-only", f"--user={os.getuid()}:{os.getgid()}", "--cpus=4", "--memory=8g",
        "--mount", f"type=bind,src={directory},dst=/out",
    ]
    expected = 0
    outputs: list[str] = []
    for index, item in enumerate(partitions):
        source = Path(str(item["path"]))
        expected += int(item.get("read_count") or 0)
        name = f"restored-{index:03d}.pod5"
        result = _run(common + [
            "--mount", f"type=bind,src={source.parent},dst=/in,readonly", image,
            "blue-crab", "s2p", f"/in/{source.name}", "-o", f"/out/{name}",
        ])
        if result.returncode != 0:
            raise RuntimeError(f"blue-crab s2p failed: {result.stderr.strip()[-400:]}")
        outputs.append(name)
    count = _run(common + [image, "python3", "-c", (
        "import glob,pod5\n"
        "n=0\n"
        "for f in glob.glob('/out/*.pod5'):\n"
        "    with pod5.Reader(f) as r: n+=sum(1 for _ in r.reads())\n"
        "print(n)"
    )])
    if count.returncode != 0:
        raise RuntimeError(f"restored POD5 could not be read: {count.stderr.strip()[-400:]}")
    restored = int(count.stdout.strip().splitlines()[-1])
    status.update(restored_read_count=restored, expected_read_count=expected or None, files=outputs)
    if expected and restored != expected:
        raise RuntimeError(f"restored {restored} reads but the BLOW5 manifest records {expected}")


async def request_restore(representation: OntRawSignalRepresentation) -> dict[str, Any]:
    if representation.format != "blow5" or representation.state != "ready":
        raise ValueError("POD5 restore requires a ready BLOW5 representation")
    current = restore_status(representation.id)
    if current.get("state") in {"running", "ready"}:
        return current
    partitions = _blow5_partitions(representation)
    runtime, image = _image()
    directory = restore_dir(representation.id)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    status: dict[str, Any] = {
        "representation_id": representation.id, "run_id": representation.run_id,
        "observed_generation": representation.observed_generation, "state": "running",
        "directory": str(directory), "requested_at": _now(), "image": image,
    }
    _write_status(directory, status)

    async def worker() -> None:
        try:
            await asyncio.to_thread(_restore, directory, partitions, runtime, image, status)
            status.update(state="ready", completed_at=_now())
        except Exception as exc:  # report the failure; the folder stays for inspection
            status.update(state="failed", error=str(exc)[:600], completed_at=_now())
        _write_status(directory, status)
        _TASKS.pop(representation.id, None)

    _TASKS[representation.id] = asyncio.create_task(worker())
    return status


__all__ = ["request_restore", "restore_dir", "restore_status"]
