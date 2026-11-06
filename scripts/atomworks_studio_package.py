#!/usr/bin/env python3
"""Build verified S03 editor wheels and prepare private-host transport/notices."""
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

from apply_atomworks_studio_patches import apply, digest, ASSETS

RELEASE_COMMIT = "04fe5fbd03fea8b7d49a767156d7d33bd10e5d13"
WHEEL_SHA256 = "da60857d956a65d1b4dfe439326303b0a0c97b6fa520cece96fa02d041d0524d"
OPT_OUT = '<meta name="atomworks-studio-service-worker" content="off">'


def verify_wheel(path: Path) -> int:
    """Require the exact published release, never accept a different release digest."""
    if digest(path) != WHEEL_SHA256:
        raise ValueError("Studio wheel SHA-256 does not match the published release")
    return verify_record(path)


def verify_record(path: Path) -> int:
    """Internal ZIP integrity, not a release/provenance acceptance check."""
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


def build_patched(source: Path, release: Path, output: Path) -> Path:
    """Repack verified release with verified S03 Python and freshly rebuilt web.

    No resolver/hatch fallback can accidentally bundle checked-in stale assets.
    Unchanged wheel members, including native definitions and guide, stay exact.
    """
    source = source.resolve()
    verify_wheel(release)
    build = apply(source, build=True)
    descriptor = json.loads((ASSETS / "host-build.json").read_text())
    if build["manifest_sha256"] != descriptor["source_manifest_sha256"]:
        raise ValueError("host build source manifest drift")
    if build["web_files"] != descriptor["web_files"]:
        raise ValueError("rebuilt web assets differ from reviewed host build")
    with zipfile.ZipFile(release) as archive:
        members = {n: archive.read(n) for n in archive.namelist()}
    manifest = json.loads((ASSETS / "manifest.json").read_text())
    replaced = {}
    for relative, hashes in manifest["files"].items():
        for prefix in ("packages/api/", "packages/editing/"):
            if relative.startswith(prefix) and relative.endswith(".py"):
                name = relative.removeprefix(prefix)
                if hashlib.sha256(members[name]).hexdigest() != hashes["before"]:
                    raise ValueError(f"release/source mismatch: {name}")
                members[name] = (source / relative).read_bytes()
                replaced[name] = digest(source / relative)
    members = {n: data for n, data in members.items() if not n.startswith("annotation_studio/web/")}
    for relative in build["web_files"]:
        members["annotation_studio/web/" + relative] = (source / "packages/web/dist" / relative).read_bytes()
    provenance = {**build, "base_release_wheel_sha256": WHEEL_SHA256,
                  "distribution_kind": "bms-s03-rebuilt-not-original-release-wheel",
                  "host_build_descriptor_sha256": digest(ASSETS / "host-build.json"),
                  "replaced_python": replaced,
                  "source_after_sha256": {p: h["after"] for p, h in manifest["files"].items()},
                  "locked_build_inputs": manifest["build_inputs"]}
    members["annotation_studio/bms_build.json"] = (json.dumps(provenance, indent=2) + "\n").encode()
    record = "atomworks_studio-0.0.5.dist-info/RECORD"
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    for name, data in sorted(members.items()):
        if name != record:
            writer.writerow((name, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode(), len(data)))
    writer.writerow((record, "", ""))
    members[record] = stream.getvalue().encode()
    output.mkdir(parents=True, exist_ok=True)
    wheel = output / "atomworks_studio-0.0.5-1bms03-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 27, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    verify_record(wheel)
    return wheel


def disable_worker(index: Path) -> bool:
    """Apply upstream's opt-out to the installed wheel's actual entry page."""
    text = index.read_text(encoding="utf-8")
    if OPT_OUT in text:
        return False
    if text.count("<head>") != 1:
        raise ValueError("unexpected Studio index layout")
    index.write_text(text.replace("<head>", "<head>\n    " + OPT_OUT, 1), encoding="utf-8")
    return True


def prepare_host(web: Path, output: Path, patched_wheel: Path | None = None) -> None:
    """Verify package provenance, then apply only the bounded host transport patch."""
    provenance = None
    if patched_wheel is not None:
        verify_record(patched_wheel)
        descriptor = json.loads((ASSETS / "host-build.json").read_text())
        with zipfile.ZipFile(patched_wheel) as archive:
            provenance = json.loads(archive.read("annotation_studio/bms_build.json"))
            if provenance["host_build_descriptor_sha256"] != digest(ASSETS / "host-build.json"):
                raise ValueError("patched wheel host descriptor drift")
            manifest = json.loads((ASSETS / "manifest.json").read_text())
            if (provenance["manifest_sha256"] != digest(ASSETS / "manifest.json")
                    or provenance["patches"] != manifest["patches"]
                    or provenance["base_release_wheel_sha256"] != WHEEL_SHA256
                    or provenance["web_files"] != descriptor["web_files"]):
                raise ValueError("patched wheel source/build provenance drift")
            expected_python = {p.removeprefix(prefix): h["after"]
                               for p, h in manifest["files"].items()
                               for prefix in ("packages/api/", "packages/editing/")
                               if p.startswith(prefix) and p.endswith(".py")}
            if provenance["replaced_python"] != expected_python:
                raise ValueError("patched wheel Python provenance drift")
            for name, expected in expected_python.items():
                if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                    raise ValueError(f"patched wheel Python drift: {name}")
            # Validate every installed wheel member before modifying host transport.
            for name in archive.namelist():
                if name.startswith(("annotation_studio/", "atomworks_studio_editing/", "atomworks_studio_rfd4/")):
                    path = web.parent.parent / name
                    if not path.is_file() or path.read_bytes() != archive.read(name):
                        raise ValueError(f"installed patched wheel drift: {name}")
        for relative, expected in descriptor["web_files"].items():
            if digest(web / relative) != expected:
                raise ValueError(f"reviewed rebuilt web drift: {relative}")
        bundle = web / descriptor["transport_asset"]
        original = descriptor["transport_original"]
        replacement = descriptor["transport_replacement"]
    else:
        bundle = web / "assets/index-CUTfffbv.js"
        original = 'function Te(e){return e.startsWith(`/api/`)&&!e.startsWith(`/api/v1/`)?`/api/v1/${e.slice(5)}`:e}'
        replacement = 'function Te(e){return `/api/atomworks-studio`+(e.startsWith(`/api/`)&&!e.startsWith(`/api/v1/`)?`/api/v1/${e.slice(5)}`:e)}'
    disable_worker(web / "index.html")
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
        'release_commit': RELEASE_COMMIT, 'base_release_wheel_sha256': WHEEL_SHA256,
        'wheel_sha256': digest(patched_wheel) if patched_wheel else WHEEL_SHA256,
        'build_provenance': provenance,
        'runtime_lock_sha256': digest(Path(__file__).resolve().parents[1] / 'docker/atomworks-studio.lock'),
        'wheel_record_sha256': (hashlib.sha256(zipfile.ZipFile(patched_wheel).read('atomworks_studio-0.0.5.dist-info/RECORD')).hexdigest()
                                if patched_wheel else None),
        'record_status': 'wheel RECORD verified before install; host asset changes separately inventoried',
        'installed_web_sha256': {str(p.relative_to(web)): digest(p) for p in sorted(web.rglob('*')) if p.is_file()},
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
    host.add_argument("--patched-wheel", type=Path)
    build = sub.add_parser("build-patched")
    build.add_argument("source", type=Path)
    build.add_argument("release", type=Path)
    build.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "build-patched":
        print(build_patched(args.source, args.release, args.output))
    elif args.command == "prepare-host":
        prepare_host(args.web, args.output, args.patched_wheel)
    elif args.command == "verify":
        print(f"verified {verify_wheel(args.wheel)} RECORD entries; Studio commit {RELEASE_COMMIT}")
    else:
        print("disabled" if disable_worker(args.index) else "already disabled")


if __name__ == "__main__":
    main()
