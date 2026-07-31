from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

from .checkpoint_receipt import write_checkpoint_receipt


class CheckpointingRunnerError(RuntimeError):
    pass


def _wait_for_process_group_exit(process_group: int, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return
        if time.monotonic() >= deadline:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                return
            raise CheckpointingRunnerError("MD checkpoint process group did not stop before timeout")
        time.sleep(0.05)


def run_checkpointable_command(
    *,
    command: Sequence[str],
    config_path: Path,
    output_dir: Path,
    gmx_binary: str = "gmx",
    stop_timeout_seconds: float = 120.0,
) -> int:
    if not command:
        raise ValueError("checkpointable command is required")
    process = subprocess.Popen(list(command), start_new_session=True)
    process_group = process.pid
    pause_requested_at_ns: int | None = None

    def request_checkpoint(signum: int, _frame: object) -> None:
        nonlocal pause_requested_at_ns
        if pause_requested_at_ns is None:
            pause_requested_at_ns = time.time_ns()
            try:
                os.killpg(process_group, signal.SIGTERM)
            except ProcessLookupError:
                pass

    previous_term = signal.signal(signal.SIGTERM, request_checkpoint)
    previous_int = signal.signal(signal.SIGINT, request_checkpoint)
    try:
        pause_boundary = output_dir / ".bms-pause-boundary.json"
        while True:
            returncode = process.poll()
            if returncode is not None:
                break
            if pause_requested_at_ns is None and pause_boundary.is_file():
                pause_requested_at_ns = pause_boundary.stat().st_mtime_ns
                try:
                    os.killpg(process_group, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            time.sleep(0.05)
        if pause_requested_at_ns is None:
            return returncode
        _wait_for_process_group_exit(process_group, timeout_seconds=stop_timeout_seconds)
        write_checkpoint_receipt(
            config_path=config_path,
            output_dir=output_dir,
            gmx_binary=gmx_binary,
            minimum_mtime_ns=pause_requested_at_ns,
        )
        return 128 + signal.SIGTERM
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run MD and publish a checkpoint receipt after TERM")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--gmx-binary", default="gmx")
    parser.add_argument("--stop-timeout-seconds", type=float, default=120.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main() -> None:
    args = _parser().parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    raise SystemExit(run_checkpointable_command(
        command=command,
        config_path=args.config,
        output_dir=args.output_dir,
        gmx_binary=args.gmx_binary,
        stop_timeout_seconds=args.stop_timeout_seconds,
    ))


if __name__ == "__main__":
    main()
