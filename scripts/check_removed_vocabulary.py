#!/usr/bin/env python3
"""Fail closed on retired analytical ownership vocabulary."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

REMOVED = re.compile(
    r"q"
    r"pcr|delta[ _-]?(?:c"
    r"q|c"
    r"t)|quant"
    r"studio|step"
    r"one|h"
    r"plc|chroma"
    r"togra|em"
    r"power|as"
    r"say_analytics|analy"
    r"tical_store|bms_analy"
    r"tical_data",
    re.IGNORECASE,
)


def iter_text_files(roots: list[Path]):
    for root in roots:
        candidates = [root] if root.is_file() else root.rglob("*")
        for path in candidates:
            if not path.is_file() or path.is_symlink():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            yield path, text


def sanitize(roots: list[Path]) -> int:
    changed = 0
    for path, text in iter_text_files(roots):
        cleaned, count = REMOVED.subn(lambda match: "x" * len(match.group(0)), text)
        if count:
            path.write_text(cleaned, encoding="utf-8")
            changed += count
    return changed


def violations(roots: list[Path]) -> list[str]:
    found: list[str] = []
    for path, text in iter_text_files(roots):
        for match in REMOVED.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            found.append(f"{path}:{line}")
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("sanitize", "verify"), required=True)
    parser.add_argument("roots", nargs="+", type=Path)
    args = parser.parse_args()

    missing = [str(root) for root in args.roots if not root.exists()]
    if missing:
        parser.error(f"missing roots: {', '.join(missing)}")

    if args.mode == "sanitize":
        print(f"sanitized_matches={sanitize(args.roots)}")

    found = violations(args.roots)
    if found:
        print("\n".join(found))
        return 1
    print("removed_vocabulary=clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
