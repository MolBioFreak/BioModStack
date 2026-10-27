#!/usr/bin/env python3
"""Verify the published Studio wheel and disable its standalone offline worker.

This is packaging preparation, not an authenticated Studio ingress.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
from pathlib import Path
import zipfile

RELEASE_COMMIT = "04fe5fbd03fea8b7d49a767156d7d33bd10e5d13"
WHEEL_SHA256 = "da60857d956a65d1b4dfe439326303b0a0c97b6fa520cece96fa02d041d0524d"
OPT_OUT = '<meta name="atomworks-studio-service-worker" content="off">'


def verify_wheel(path: Path) -> int:
    """Check the release digest and every RECORD member before installation."""
    if hashlib.sha256(path.read_bytes()).hexdigest() != WHEEL_SHA256:
        raise ValueError("Studio wheel SHA-256 does not match the published release")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate archive members")
        record = "atomworks_studio-0.0.5.dist-info/RECORD"
        rows = list(csv.reader(io.TextIOWrapper(archive.open(record), encoding="utf-8", newline="")))
        if len(rows) != len(names) or {row[0] for row in rows} != set(names):
            raise ValueError("wheel RECORD does not cover exactly the archive")
        for name, digest, size in rows:
            if name == record:
                if digest or size:
                    raise ValueError("unexpected RECORD self digest")
                continue
            content = archive.read(name)
            expected = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
            if digest != "sha256=" + expected or size != str(len(content)):
                raise ValueError(f"wheel RECORD mismatch: {name}")
        return len(rows)


def disable_worker(index: Path) -> bool:
    """Apply upstream's opt-out to the installed wheel's actual entry page."""
    text = index.read_text(encoding="utf-8")
    if OPT_OUT in text:
        return False
    if text.count("<head>") != 1:
        raise ValueError("unexpected Studio index layout")
    index.write_text(text.replace("<head>", "<head>\n    " + OPT_OUT, 1), encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("wheel", type=Path)
    disable = sub.add_parser("disable-worker")
    disable.add_argument("index", type=Path)
    args = parser.parse_args()
    if args.command == "verify":
        print(f"verified {verify_wheel(args.wheel)} RECORD entries; Studio commit {RELEASE_COMMIT}")
    else:
        print("disabled" if disable_worker(args.index) else "already disabled")


if __name__ == "__main__":
    main()
