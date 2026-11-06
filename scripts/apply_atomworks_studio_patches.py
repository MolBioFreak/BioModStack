#!/usr/bin/env python3
"""Apply/verify the reviewed Studio patch series, optionally build locked web assets.

This helper never fetches, changes pins, installs Python packages, or starts a service.
Use a disposable upstream Git checkout, not a deployed session/service directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ASSETS = Path(__file__).resolve().parents[1] / "third_party" / "atomworks_studio"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(source: Path, *args: str) -> str:
    return subprocess.check_output(args, cwd=source, text=True, stderr=subprocess.STDOUT).strip()


def safe_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError(f"Path escapes source root: {relative}")
    return path


def check_files(source: Path, entries: dict, state: str) -> None:
    for relative, versions in entries.items():
        path = safe_path(source, relative)
        expected = versions[state]
        if expected is None:
            if path.exists():
                raise ValueError(f"Expected absent {relative}")
        elif not path.is_file() or digest(path) != expected:
            raise ValueError(f"Hash mismatch ({state}): {relative}")


def apply(source: Path, *, verify_only: bool = False, build: bool = False) -> dict:
    manifest_path = ASSETS / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if run(source, "git", "rev-parse", "HEAD") != manifest["upstream_commit"]:
        raise ValueError("Upstream HEAD is not the exact reviewed release commit")
    # Check locked build inputs even when all patched files already match.
    for relative, expected in manifest["build_inputs"].items():
        if digest(safe_path(source, relative)) != expected:
            raise ValueError(f"Locked build input differs: {relative}")
    patches = []
    for item in manifest["patches"]:
        path = safe_path(ASSETS, item["file"])
        if digest(path) != item["sha256"]:
            raise ValueError(f"Patch digest differs: {item['file']}")
        patches.append(path)
    allowed = set(manifest["files"])
    unexpected_untracked = set(run(source, "git", "ls-files", "--others", "--exclude-standard").splitlines()) - allowed
    if unexpected_untracked:
        raise ValueError(f"Unexpected untracked source files: {sorted(unexpected_untracked)}")
    try:
        check_files(source, manifest["files"], "after")
    except ValueError:
        if verify_only:
            raise
        check_files(source, manifest["files"], "before")
        # Refuse unrelated tracked edits instead of accidentally packaging them.
        if run(source, "git", "diff", "--name-only", "HEAD"):
            raise ValueError("Upstream checkout has unrelated tracked changes")
        # Check every patch together before the first write; git apply is atomic.
        run(source, "git", "apply", "--check", "--whitespace=error-all", *map(str, patches))
        run(source, "git", "apply", "--whitespace=error-all", *map(str, patches))
        check_files(source, manifest["files"], "after")
    unexpected = set(run(source, "git", "diff", "--name-only", "HEAD").splitlines()) - allowed
    if unexpected:
        raise ValueError(f"Unexpected tracked source edits: {sorted(unexpected)}")
    receipt = {
        "upstream_commit": manifest["upstream_commit"],
        "manifest_sha256": digest(manifest_path),
        "patches": manifest["patches"],
        "source_verified": True,
        "web_build": "not_run",
    }
    if build:
        # Forward real tool output; a nonzero install/build must not become a receipt.
        for command in (("pnpm", "install", "--frozen-lockfile"), ("pnpm", "build")):
            subprocess.run(command, cwd=source / "packages" / "web", check=True, stdout=sys.stderr,
                           env={**os.environ, "ANNOTATION_STUDIO_VERSION": manifest["upstream_version"],
                                "ANNOTATION_STUDIO_REVISION": manifest["upstream_commit"] + "-bms-s03"})
        apply(source, verify_only=True)
        dist = source / "packages" / "web" / "dist"
        if not (dist / "index.html").is_file():
            raise ValueError("Build did not produce dist/index.html")
        receipt["web_build"] = "passed"
        receipt["toolchain"] = {tool: run(source, tool, "--version")
                                for tool in ("node", "pnpm", "rustc", "cargo", "wasm-pack")}
        receipt["build_environment"] = {"ANNOTATION_STUDIO_VERSION": manifest["upstream_version"],
                                        "ANNOTATION_STUDIO_REVISION": manifest["upstream_commit"] + "-bms-s03"}
        receipt["web_files"] = {str(p.relative_to(dist)): digest(p) for p in sorted(dist.rglob("*")) if p.is_file()}
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(apply(args.source.resolve(), verify_only=args.verify_only, build=args.build), indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Studio patch verification failed: {error}\n")


if __name__ == "__main__":
    main()
