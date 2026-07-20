#!/usr/bin/env python3
"""Provision the network-enabled, immutable wf-clone-validation P3 runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


COMMIT_ENV = {
    "GIT_AUTHOR_NAME": "BioModStack Runtime Pin",
    "GIT_AUTHOR_EMAIL": "runtime-pin@biomodstack.local",
    "GIT_AUTHOR_DATE": "1784473200 +0000",
    "GIT_COMMITTER_NAME": "BioModStack Runtime Pin",
    "GIT_COMMITTER_EMAIL": "runtime-pin@biomodstack.local",
    "GIT_COMMITTER_DATE": "1784473200 +0000",
}
COMMIT_MESSAGE = "compat: make wf-clone-validation v1.8.4 parse on Nextflow 25\n"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(args, cwd=cwd, env=merged_env, text=True, capture_output=True, check=True).stdout.strip()


def resolve(lock_path: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (lock_path.parent / path).resolve()


def source_is_exact(source: Path, expected_commit: str, expected_tree: str) -> bool:
    if not source.is_dir():
        return False
    try:
        return (
            run("git", "-C", str(source), "rev-parse", "HEAD") == expected_commit
            and run("git", "-C", str(source), "rev-parse", "HEAD^{tree}") == expected_tree
            and run("git", "-C", str(source), "status", "--porcelain", "--untracked-files=all") == ""
        )
    except (OSError, subprocess.SubprocessError):
        return False


def provision_source(lock_path: Path, lock: dict[str, Any]) -> None:
    upstream = lock["upstream"]
    patched = lock["patched_source"]
    compatibility = lock["compatibility_patch"]
    destination = resolve(lock_path, patched["path"])
    if destination.exists():
        if source_is_exact(destination, patched["commit"], patched["tree"]):
            return
        raise RuntimeError(f"refusing to mutate existing mismatched source: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.provision-", dir=destination.parent))
    try:
        run("git", "clone", "--no-checkout", upstream["repository"], str(temporary))
        run("git", "-C", str(temporary), "switch", "--detach", upstream["commit"])
        if run("git", "-C", str(temporary), "rev-parse", "HEAD^{tree}") != upstream["tree"]:
            raise RuntimeError("downloaded upstream tree does not match lock")
        patch_path = resolve(lock_path, compatibility["path"])
        if sha256_file(patch_path) != compatibility["sha256"]:
            raise RuntimeError("checked-in compatibility patch does not match lock")
        run("git", "-C", str(temporary), "apply", "--index", str(patch_path))
        tree = run("git", "-C", str(temporary), "write-tree")
        if tree != patched["tree"]:
            raise RuntimeError(f"patched tree {tree} does not match locked tree {patched['tree']}")
        commit = subprocess.run(
            ["git", "-C", str(temporary), "commit-tree", tree, "-p", upstream["commit"]],
            input=COMMIT_MESSAGE,
            text=True,
            capture_output=True,
            check=True,
            env=os.environ | COMMIT_ENV,
        ).stdout.strip()
        if commit != patched["commit"]:
            raise RuntimeError(f"compatibility commit {commit} does not match locked commit {patched['commit']}")
        run("git", "-C", str(temporary), "update-ref", "HEAD", commit)
        if not source_is_exact(temporary, patched["commit"], patched["tree"]):
            raise RuntimeError("materialized patched source failed final identity/cleanliness check")
        os.rename(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def provision_images(lock_path: Path, lock: dict[str, Any], apptainer: str) -> None:
    containers = lock["containers"]
    cache_dir = resolve(lock_path, containers["cache_dir"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    for image in containers["images"]:
        destination = cache_dir / image["cache_file"]
        if destination.exists():
            if destination.is_file() and not destination.is_symlink() and sha256_file(destination) == image["sha256"]:
                continue
            raise RuntimeError(f"refusing to replace existing mismatched image: {destination}")
        file_descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=cache_dir)
        os.close(file_descriptor)
        temporary = Path(temporary_name)
        temporary.unlink()
        try:
            run(apptainer, "pull", "--disable-cache", str(temporary), image["uri"])
            actual = sha256_file(temporary)
            if actual != image["sha256"]:
                raise RuntimeError(f"downloaded image {image['uri']} SHA-256 is {actual}, expected {image['sha256']}")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("config/ngs/wf_clone_validation_v1.8.4.lock.json"))
    parser.add_argument("--apptainer", default="apptainer")
    args = parser.parse_args()
    lock_path = args.lock.resolve()
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    provision_images(lock_path, lock, args.apptainer)
    provision_source(lock_path, lock)
    model = lock["models"]["default"]
    validator = Path(__file__).with_name("validate_wf_clone_runtime.py")
    run("python3", str(validator), "--lock", str(lock_path), "--model", model, "--output", "-")
    print("wf-clone-validation runtime is provisioned and matches the lock")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
