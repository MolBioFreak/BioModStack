#!/usr/bin/env python3
"""Check staged paths/content without importing BMS or contacting any service.

Run after staging intended changes. In CI the index is the checked-out commit.
This is a focused hygiene gate, not proof that code is reachable or a full
credential/history scanner. Approved package/fixture exceptions are hash-bound.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

POLICY_PATH = "config/repository-hygiene.json"
FORBIDDEN_PARTS = {
    "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".hermes", ".nextflow", ".artifacts",
    ".ngs-tools", ".ngs_latest_papers", ".protenix_cache", ".protenix_anchor_root",
}
FORBIDDEN_PREFIXES = (
    "work/", "output/", "backups/", "bms_results/", "tmp/", "dogfood-output/",
    "platform/frontend/dist/", "platform/frontend/.test-dist/",
    "platform/mobile-cordova/platforms/", "platform/mobile-cordova/plugins/",
    "platform/mobile-cordova/.cache/", "platform/mobile-cordova/www/",
)
OUTPUT_SUFFIXES = (
    ".pyc", ".pyo", ".pyd", ".db", ".db-shm", ".db-wal",
    ".sqlite", ".sqlite3", ".sqlite-shm", ".sqlite-wal",
    ".sqlite3-shm", ".sqlite3-wal", ".db-journal", ".sqlite-journal",
    ".sqlite3-journal", ".log", ".apk", ".aab", ".sif",
    ".zip", ".tar", ".tar.gz", ".tgz", ".deb", ".rpm", ".msi",
    ".exe", ".dmg", ".jks", ".keystore", ".p12", ".pfx",
    ".pt", ".pth", ".ckpt", ".safetensors",
)
SECRET_PATTERNS = {
    "private-key block": re.compile(
        rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----\s+[A-Za-z0-9+/=]{20}"),
    "GitHub token": re.compile(
        rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    "AWS access-key ID": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "Slack token": re.compile(rb"\bxox[baprs]-[0-9A-Za-z-]{30,}\b"),
}
MAX_UNREVIEWED_BYTES = 5 * 1024 * 1024
# Bound memory before loading blob contents. Large distributions belong in
# release storage; a hash exception cannot silently disable these scan limits.
MAX_SCANNABLE_BLOB_BYTES = 64 * 1024 * 1024
MAX_SCANNABLE_TOTAL_BYTES = 512 * 1024 * 1024
CREDENTIAL_SUFFIXES = (".jks", ".keystore", ".p12", ".pfx")


def git(root: Path, *args: str, data: bytes | None = None) -> bytes:
    process = subprocess.run(
        ["git", "-C", str(root), *args], input=data, capture_output=True, check=False)
    if process.returncode:
        raise ValueError(f"git {' '.join(args)} failed: "
                         + process.stderr.decode("utf-8", errors="replace").strip())
    return process.stdout


def read_index(root: Path) -> dict[str, bytes]:
    objects: dict[str, str] = {}
    for entry in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        meta, raw_path = entry.split(b"\t", 1)
        mode, oid, stage = meta.decode("ascii").split()
        path = raw_path.decode("utf-8")
        if stage != "0":
            raise ValueError(f"unmerged index entry: {path}")
        if mode not in {"100644", "100755", "120000"}:
            raise ValueError(f"unsupported tracked mode {mode}: {path}; review explicitly")
        if PurePosixPath(path).name == ".gitignore" and mode == "120000":
            raise ValueError(f"tracked .gitignore must be a regular file: {path}")
        objects[path] = oid
    ids = sorted(set(objects.values()))
    request = ("\n".join(ids) + "\n").encode() if ids else b""
    metadata = git(root, "cat-file", "--batch-check", data=request).splitlines() if ids else []
    if len(metadata) != len(ids):
        raise ValueError("incomplete indexed object metadata")
    total = 0
    for expected, row in zip(ids, metadata):
        oid, kind, count = row.decode("ascii").split()
        size = int(count)
        if oid != expected or kind != "blob" or size < 0:
            raise ValueError("invalid indexed object metadata")
        if size > MAX_SCANNABLE_BLOB_BYTES:
            raise ValueError("indexed blob exceeds 64 MiB scan budget; move distribution bytes to release storage")
        total += size
    if total > MAX_SCANNABLE_TOTAL_BYTES:
        raise ValueError("indexed blobs exceed 512 MiB scan budget; explicit repository-size review required")
    response = git(root, "cat-file", "--batch", data=request) if ids else b""
    blobs: dict[str, bytes] = {}
    offset = 0
    for expected in ids:
        end = response.index(b"\n", offset)
        oid, kind, count = response[offset:end].decode("ascii").split()
        size = int(count)
        if oid != expected or kind != "blob":
            raise ValueError(f"unexpected object response for {expected}")
        start = end + 1
        if response[start + size:start + size + 1] != b"\n":
            raise ValueError(f"incomplete blob {expected}")
        blobs[oid] = response[start:start + size]
        offset = start + size + 1
    return {path: blobs[oid] for path, oid in objects.items()}


def violation(path: str, size: int) -> str | None:
    parts = PurePosixPath(path).parts
    name = parts[-1]
    if any(part in FORBIDDEN_PARTS for part in parts) or path.startswith(FORBIDDEN_PREFIXES):
        return "dependency/cache/generated-output directory"
    if (name == ".env" or name.startswith(".env.")) and not name.endswith((".example", ".sample", ".template")):
        return "private environment file"
    if name.lower().endswith(CREDENTIAL_SUFFIXES):
        return "private credential container"
    if name.lower().endswith(OUTPUT_SUFFIXES) or ".sif." in name.lower():
        return "runtime output, model weight, credential container, or packaged artifact"
    if size > MAX_UNREVIEWED_BYTES:
        return "file larger than 5 MiB without an explicit reviewed exception"
    return None


def unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate repository hygiene policy key: {key}")
        result[key] = value
    return result


def ignored_index_paths(root: Path, indexed: dict[str, bytes]) -> list[str]:
    """Evaluate only staged repository rules, never a developer's local ignores.

    A directory-only scratch tree holds the indexed .gitignore bytes. Git still
    supplies pattern semantics and the real index; no BMS files are executed or
    checked out. --exclude-per-directory deliberately excludes global rules and
    .git/info/exclude. Untracked/unstaged .gitignore edits cannot alter the result.
    """
    with tempfile.TemporaryDirectory(prefix="bms-hygiene-ignores-") as directory:
        scratch = Path(directory)
        for path, data in indexed.items():
            target = scratch / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.name == ".gitignore":
                target.write_bytes(data)
        raw = git(root, "--work-tree=" + str(scratch), "ls-files", "-ci",
                  "--exclude-per-directory=.gitignore", "-z")
    return [path for path in raw.decode("utf-8").split("\0") if path]


def audit(root: Path) -> tuple[list[str], int, int]:
    indexed = read_index(root)
    if POLICY_PATH not in indexed:
        raise ValueError("required repository hygiene policy is missing from the index")
    policy = json.loads(indexed[POLICY_PATH], object_pairs_hook=unique_keys)
    if (type(policy) is not dict or set(policy) != {"version", "exceptions"}
            or type(policy.get("version")) is not int or policy["version"] != 1
            or type(policy.get("exceptions")) is not list):
        raise ValueError("unsupported repository hygiene policy")
    errors: list[str] = []
    exceptions: dict[str, dict[str, str]] = {}
    for entry in policy["exceptions"]:
        if type(entry) is not dict or set(entry) != {"path", "sha256", "reason"}:
            raise ValueError("each exception must contain exactly path, sha256, and reason")
        path, digest, reason = (entry.get(key) for key in ("path", "sha256", "reason"))
        if (not isinstance(path, str) or not path or path.startswith("/") or ".." in PurePosixPath(path).parts
                or PurePosixPath(path).as_posix() != path or "\\" in path
                or any(ord(char) < 32 for char in path)
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not isinstance(reason, str) or not reason.strip()):
            raise ValueError("exception requires a relative path, SHA-256, and justification")
        if path in exceptions:
            raise ValueError(f"duplicate exception: {path}")
        exceptions[path] = entry
        reason_for_path = violation(path, len(indexed.get(path, b"")))
        if reason_for_path in {
            "dependency/cache/generated-output directory", "private environment file",
            "private credential container",
        }:
            errors.append(f"{path}: private/local-state paths cannot be approved as artifact exceptions")
        if path not in indexed:
            errors.append(f"{path}: stale exception; remove it with the retired file")
        elif hashlib.sha256(indexed[path]).hexdigest() != digest:
            errors.append(f"{path}: approved artifact/fixture hash changed; review and update explicitly")
        elif violation(path, len(indexed[path])) is None:
            errors.append(f"{path}: unnecessary exception")
    for path, data in indexed.items():
        reason = violation(path, len(data))
        if reason and path not in exceptions:
            errors.append(f"{path}: {reason}")
        # Byte patterns avoid disclosing values. Binary artifacts are NOT unpacked.
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(data):
                errors.append(f"{path}: possible {name}; value suppressed")
    for path in ignored_index_paths(root, indexed):
        errors.append(f"{path}: tracked file is masked by a staged ignore rule")
    return errors, len(indexed), len(exceptions)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        errors, count, approved = audit(args.repo.resolve())
    except (ValueError, OSError, UnicodeError, KeyError, TypeError) as exc:
        print(f"Repository hygiene could not complete: {exc}", file=sys.stderr)
        return 2
    for error in errors:
        print(f"FAIL: {error}", file=sys.stderr)
    print(f"Repository hygiene: {count} indexed files; {approved} hash-bound exceptions; {len(errors)} findings.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
