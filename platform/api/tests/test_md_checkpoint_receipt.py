from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.bms_md.checkpoint_receipt import write_checkpoint_receipt


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def test_write_checkpoint_receipt_validates_latest_gromacs_checkpoint(tmp_path: Path) -> None:
    output = tmp_path / "replica_0"
    old = output / "npt" / "npt.cpt"
    current = output / "production" / "production.cpt"
    old.parent.mkdir(parents=True)
    current.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    current.write_bytes(b"current-checkpoint")
    old.touch()
    current.touch()

    config = {
        "engine": "gromacs",
        "engine_runtime": {"sif_sha256": "1" * 64},
        "chemistry": {"profile_sha256": "2" * 64},
        "protocol": {"timestep_fs": 2.0},
        "input": {"structure_sha256": "3" * 64},
        "random_seed": 42,
    }
    config_path = tmp_path / "job.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    fake_gmx = tmp_path / "gmx"
    fake_gmx.write_text(
        "#!/bin/sh\nprintf 'step = 2500\\nt = 5.000000\\n'\n",
        encoding="utf-8",
    )
    fake_gmx.chmod(0o755)

    receipt_path = write_checkpoint_receipt(
        config_path=config_path,
        output_dir=output,
        gmx_binary=str(fake_gmx),
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    compatibility = _digest({
        "engine": config["engine"],
        "engine_runtime": config["engine_runtime"],
        "chemistry": config["chemistry"],
        "protocol": config["protocol"],
        "input_hashes": {"structure_sha256": "3" * 64},
    })
    assert receipt == {
        "schema": "bms.md.checkpoint-receipt.v1",
        "checkpoint_path": "production/production.cpt",
        "sha256": hashlib.sha256(current.read_bytes()).hexdigest(),
        "bytes": len(current.read_bytes()),
        "step": 2500,
        "time_ps": 5.0,
        "execution_plan_sha256": _digest(config),
        "compatibility_key": compatibility,
    }


def test_write_checkpoint_receipt_rejects_missing_checkpoint(tmp_path: Path) -> None:
    config = tmp_path / "job.json"
    config.write_text(json.dumps({"engine": "gromacs"}), encoding="utf-8")
    output = tmp_path / "replica_0"
    output.mkdir()
    with pytest.raises(RuntimeError, match="no GROMACS checkpoint"):
        write_checkpoint_receipt(config_path=config, output_dir=output, gmx_binary="gmx")


def test_write_checkpoint_receipt_rejects_checkpoint_older_than_pause(tmp_path: Path) -> None:
    config = tmp_path / "job.json"
    config.write_text(json.dumps({"engine": "gromacs"}), encoding="utf-8")
    output = tmp_path / "replica_0"
    output.mkdir()
    checkpoint = output / "production.cpt"
    checkpoint.write_bytes(b"periodic-before-pause")
    minimum_mtime_ns = time.time_ns() + 1_000_000
    with pytest.raises(RuntimeError, match="updated after the pause request"):
        write_checkpoint_receipt(
            config_path=config,
            output_dir=output,
            gmx_binary="gmx",
            minimum_mtime_ns=minimum_mtime_ns,
        )


def test_checkpointing_runner_waits_for_gromacs_grandchild_flush(tmp_path: Path) -> None:
    output = tmp_path / "replica_0"
    output.mkdir()
    stale = output / "periodic.cpt"
    stale.write_bytes(b"stale")
    config = {
        "engine": "gromacs",
        "engine_runtime": {"sif_sha256": "1" * 64},
        "chemistry": {"profile_sha256": "2" * 64},
        "protocol": {"timestep_fs": 2.0},
        "input": {"structure_sha256": "3" * 64},
        "random_seed": 42,
    }
    config_path = tmp_path / "job.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    fake_gmx = tmp_path / "gmx"
    fake_gmx.write_text("#!/bin/sh\nprintf 'step = 3000\\nt = 6.000000\\n'\n", encoding="utf-8")
    fake_gmx.chmod(0o755)
    grandchild = tmp_path / "grandchild.py"
    grandchild.write_text(
        "import signal,time,sys\n"
        "from pathlib import Path\n"
        "def stop(*_):\n"
        "    time.sleep(0.35)\n"
        "    Path(sys.argv[1]).write_bytes(b'signal-flushed')\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "while True: time.sleep(0.05)\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    checkpoint = output / "signal.cpt"
    parent.write_text(
        "import subprocess,sys,time\n"
        "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])\n"
        "while True: time.sleep(0.05)\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    process = subprocess.Popen([
        sys.executable, "-m", "scripts.bms_md.checkpointing_runner",
        "--config", str(config_path),
        "--output-dir", str(output),
        "--gmx-binary", str(fake_gmx),
        "--stop-timeout-seconds", "5",
        "--", sys.executable, str(parent), str(grandchild), str(checkpoint),
    ], cwd=ROOT, env=env)
    time.sleep(0.25)
    started = time.monotonic()
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=10) == 128 + signal.SIGTERM
    assert time.monotonic() - started >= 0.30
    receipt = json.loads((output / "md-checkpoint-receipt.json").read_text(encoding="utf-8"))
    assert receipt["checkpoint_path"] == "signal.cpt"
    assert receipt["sha256"] == hashlib.sha256(b"signal-flushed").hexdigest()
    assert receipt["step"] == 3000 and receipt["time_ps"] == 6.0
