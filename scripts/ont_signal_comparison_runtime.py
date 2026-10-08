#!/usr/bin/env python3
"""Bounded network-silent Squigualiser two-track comparison renderer."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Sequence

MAX_HTML_BYTES = 48 * 1024 * 1024
MAX_TOTAL_OUTPUT_BYTES = 64 * 1024 * 1024
ACTIVE_RESOURCE = re.compile(
    rb"(?:https?://|file://|<\s*(?:iframe|object|embed|audio|video|form)\b|"
    rb"(?:src|href)\s*=\s*[\"'](?!data:|blob:|#))",
    re.IGNORECASE,
)


def build_plot_tracks_argv(real_html: str, simulated_html: str, output_html: str) -> list[str]:
    for value in (real_html, simulated_html, output_html):
        if not value.startswith("/output/") or ".." in Path(value).parts:
            raise ValueError("comparison artifact path is outside /output")
    return [
        "squigualiser", "plot_tracks", "--shared_x",
        "--tag_name", "REAL · INSTRUMENT ACQUIRED",
        "--tag_name", "SIMULATED IDEAL · SQUIGULATOR 0.5.0",
        "-f", real_html, "-f", simulated_html, "-o", output_html,
    ]


def validate_comparison_html(path: Path, *, real_read_id: str, profile_id: str) -> dict[str, int | str]:
    info = path.lstat()
    if path.is_symlink() or not path.is_file() or not 1 <= info.st_size <= MAX_HTML_BYTES:
        raise RuntimeError("comparison HTML violates bounded regular-file policy")
    raw = path.read_bytes()
    if ACTIVE_RESOURCE.search(raw):
        raise RuntimeError("comparison HTML contains an external active resource")
    text = raw.decode("utf-8", "strict")
    required = (
        f"REAL · INSTRUMENT ACQUIRED · {real_read_id}",
        f"SIMULATED IDEAL · SQUIGULATOR 0.5.0 · {profile_id}",
        "Simulated signal is model-derived from the selected reference and profile. It is not instrument-acquired evidence.",
    )
    if not all(label in text for label in required) or "Bokeh" not in text:
        raise RuntimeError("comparison HTML lacks visible governed plot labels")
    return {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": info.st_size}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-html")
    parser.add_argument("--simulated-html")
    parser.add_argument("--output-html", default="/output/comparison.html")
    parser.add_argument("--real-read-id")
    parser.add_argument("--profile-id")
    parser.add_argument("--print-argv", action="store_true")
    args = parser.parse_args(argv)
    if not args.real_html or not args.simulated_html:
        parser.print_help()
        return 0
    command = build_plot_tracks_argv(args.real_html, args.simulated_html, args.output_html)
    if args.print_argv:
        print(json.dumps(command, separators=(",", ":")))
        return 0
    completed = subprocess.run(command, stdin=subprocess.DEVNULL, check=False)
    if completed.returncode != 0:
        return completed.returncode
    validate_comparison_html(Path(args.output_html), real_read_id=args.real_read_id, profile_id=args.profile_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
