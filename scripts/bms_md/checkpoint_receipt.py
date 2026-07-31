from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

_STEP = re.compile(r"(?:^|\s)step\s*=\s*(\d+)", re.IGNORECASE)
_TIME = re.compile(r"(?:^|\s)t\s*=\s*([0-9]+(?:\.[0-9]*)?(?:[eE][+-]?\d+)?)")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_checkpoint_receipt(
    *,
    config_path: Path,
    output_dir: Path,
    gmx_binary: str = "gmx",
    minimum_mtime_ns: int | None = None,
) -> Path:
    config_path = Path(config_path).expanduser().resolve(strict=True)
    output_dir = Path(output_dir).expanduser().resolve(strict=True)
    candidates = [
        path for path in output_dir.rglob("*.cpt")
        if (
            path.is_file()
            and not path.is_symlink()
            and (minimum_mtime_ns is None or path.stat().st_mtime_ns >= minimum_mtime_ns)
        )
    ]
    if not candidates:
        if minimum_mtime_ns is not None:
            raise RuntimeError("no GROMACS checkpoint was updated after the pause request")
        raise RuntimeError("no GROMACS checkpoint was produced")
    checkpoint = max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.as_posix()))
    relative_path = checkpoint.relative_to(output_dir).as_posix()

    completed = subprocess.run(
        [gmx_binary, "dump", "-cp", str(checkpoint)],
        cwd=output_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"GROMACS could not read checkpoint {relative_path}")
    steps = _STEP.findall(completed.stdout)
    times = _TIME.findall(completed.stdout)
    if not steps or not times:
        raise RuntimeError(f"GROMACS checkpoint metadata is incomplete for {relative_path}")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    compatibility = _digest({
        "engine": config.get("engine"),
        "engine_runtime": config.get("engine_runtime"),
        "chemistry": config.get("chemistry"),
        "protocol": config.get("protocol"),
        "input_hashes": {
            key: value for key, value in (config.get("input") or {}).items()
            if key.endswith("_sha256")
        },
    })
    checkpoint_bytes = checkpoint.read_bytes()
    receipt = {
        "schema": "bms.md.checkpoint-receipt.v1",
        "checkpoint_path": relative_path,
        "sha256": hashlib.sha256(checkpoint_bytes).hexdigest(),
        "bytes": len(checkpoint_bytes),
        "step": int(steps[-1]),
        "time_ps": float(times[-1]),
        "execution_plan_sha256": _digest(config),
        "compatibility_key": compatibility,
    }
    receipt_path = output_dir / "md-checkpoint-receipt.json"
    _atomic_json(receipt_path, receipt)
    return receipt_path
