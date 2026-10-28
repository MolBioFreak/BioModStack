#!/usr/bin/env python3
"""Verify the released editor and prepare private-host transport assets/notices."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
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


def prepare_host(web: Path, output: Path) -> None:
    """Bounded released-asset transport patch; scientific JS remains unchanged."""
    disable_worker(web / "index.html")
    bundle = web / "assets/index-CUTfffbv.js"
    original = 'function Te(e){return e.startsWith(`/api/`)&&!e.startsWith(`/api/v1/`)?`/api/v1/${e.slice(5)}`:e}'
    replacement = 'function Te(e){return `/api/atomworks-studio`+(e.startsWith(`/api/`)&&!e.startsWith(`/api/v1/`)?`/api/v1/${e.slice(5)}`:e)}'
    text = bundle.read_text()
    if replacement not in text:
        if text.count(original) != 1:
            raise ValueError("released Studio transport function changed; review patch")
        bundle.write_text(text.replace(original, replacement))
    output.mkdir(parents=True, exist_ok=True)
    notices = output / "notices"
    notices.mkdir(exist_ok=True)
    supplements = Path(__file__).resolve().parents[1] / 'docker/atomworks-studio-notices'
    shutil.copytree(supplements, notices / 'supplements', dirs_exist_ok=True)
    inventory = []
    for dist in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = dist.metadata['Name']
        copied = []
        for item in dist.files or []:
            # Include full installed license/notice texts, including vendored bundles.
            if any(token in item.name.lower() for token in ('license', 'licence', 'copying', 'notice', 'copyright')):
                source = Path(dist.locate_file(item))
                if source.is_file():
                    target = notices / name / str(item).replace('../', '_/')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    copied.append(str(target.relative_to(output)))
        inventory.append({'name': name, 'version': dist.version, 'license_files': copied,
                          'license': dist.metadata.get('License-Expression') or dist.metadata.get('License'),
                          'requires': dist.requires or []})
    (output / 'receipt.json').write_text(json.dumps({
        'release_commit': RELEASE_COMMIT, 'wheel_sha256': WHEEL_SHA256,
        'python': sys.version, 'packages': inventory,
        'notice_sha256': {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(notices.rglob('*')) if p.is_file()},
        'modified_web': {str(p.relative_to(web)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in [web / 'index.html', bundle]},
        'patches': ['service-worker opt-out', 'private API path prefix'],
    }, indent=2) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("wheel", type=Path)
    disable = sub.add_parser("disable-worker")
    disable.add_argument("index", type=Path)
    host = sub.add_parser("prepare-host")
    host.add_argument("web", type=Path)
    host.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "prepare-host":
        prepare_host(args.web, args.output)
    elif args.command == "verify":
        print(f"verified {verify_wheel(args.wheel)} RECORD entries; Studio commit {RELEASE_COMMIT}")
    else:
        print("disabled" if disable_worker(args.index) else "already disabled")


if __name__ == "__main__":
    main()
