#!/usr/bin/env python3
"""Validate a bounded, self-contained IGV Reports HTML artifact."""

from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path


class _ResourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.resources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag
        for name, value in attrs:
            if name.casefold() in {"src", "href", "data", "poster", "action", "formaction"} and value:
                self.resources.append(value)


def validate_report(report: str | Path, *, max_bytes: int) -> int:
    path = Path(report)
    if path.is_symlink() or not path.is_file():
        raise ValueError("unsafe or missing standalone IGV report")
    size = path.stat().st_size
    if max_bytes <= 0 or size > max_bytes:
        raise ValueError(f"standalone IGV report exceeds size limit: {size} > {max_bytes}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("standalone IGV report is not valid UTF-8 HTML") from exc

    parser = _ResourceParser()
    parser.feed(text)
    parser.close()
    external = [value for value in parser.resources if not value.startswith("data:") and not value.startswith("#")]
    if external or re.search(r"(?:/api/|file://)", text, re.IGNORECASE):
        raise ValueError("standalone IGV report contains an external or host-bound resource")
    if "data:" not in text or "igv.createBrowser" not in text or "tableJson" not in text:
        raise ValueError("standalone IGV report is missing embedded code or locus data")
    return size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--max-bytes", required=True, type=int)
    args = parser.parse_args()
    try:
        size = validate_report(args.report, max_bytes=args.max_bytes)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"standalone IGV report valid: {size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())