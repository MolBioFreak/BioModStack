#!/usr/bin/env python3
"""Build the package-local NGS/MolBio runtime implementation record.

This is a build-time byte-binding tool. It uses an explicit source denominator
and never invokes Git. The generated record states source implementation only;
it cannot create or imply runtime, test, Development, or release acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

import rfc8785  # type: ignore[import-not-found]

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "platform/api/config/ngs_molbio_runtime/runtime_implementation_v1.json"
DENOMINATOR_RELATIVE = "schemas/ngs_molbio_runtime/runtime-source-denominator-v1.json"
DENOMINATOR = ROOT / DENOMINATOR_RELATIVE
N0_RECEIPT = ROOT / "docs/reports/ngs-molbio-phase-n0-verification-v1.json"
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

PHASES = (
    ("N1", "Additive global/domain persistence and immutable binding source are implemented."),
    ("N2", "Leased command transport and ordered outbox/inbox convergence source are implemented."),
    ("N3", "Exact native adapter, result-surface, and typed-lineage source are implemented."),
    ("N4", "Nested operator/API controls, launch contexts, run controls, and reopen source are implemented."),
    ("N5", "Dataset, bounded read model, admission, operations, scanner, and retained-audit source are implemented."),
    ("N6", "Release authority schemas and source-byte record generation are implemented; acceptance remains open."),
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _content_sha256(document: dict[str, object]) -> str:
    value = dict(document)
    value.pop("content_sha256", None)
    return _sha256(rfc8785.dumps(value))


def _git_object(value: str, label: str) -> str:
    if _GIT_OBJECT_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be an externally supplied lowercase 40-character Git object ID")
    return value


def _git_object_id(kind: str, raw: bytes) -> str:
    return hashlib.sha1(f"{kind} {len(raw)}\0".encode("ascii") + raw).hexdigest()


def _filesystem_git_tree(path: Path, *, root: bool = False) -> str | None:
    entries: list[tuple[bytes, bytes]] = []
    for child in path.iterdir():
        if (root and child.name == ".git") or child == OUTPUT:
            continue
        if child.name == ".git":
            raise RuntimeError(f"nested Git metadata is not allowed in frozen source: {child}")
        name = os.fsencode(child.name)
        if child.is_symlink():
            raw = os.fsencode(os.readlink(child))
            mode = b"120000"
            object_id = _git_object_id("blob", raw)
            sort_key = name
        elif child.is_dir():
            child_tree = _filesystem_git_tree(child)
            if child_tree is None:
                continue
            object_id = child_tree
            mode = b"40000"
            sort_key = name + b"/"
        elif child.is_file():
            raw = child.read_bytes()
            mode = b"100755" if child.stat().st_mode & 0o100 else b"100644"
            object_id = _git_object_id("blob", raw)
            sort_key = name
        else:
            raise RuntimeError(f"unsupported frozen-source filesystem entry: {child}")
        entries.append((sort_key, mode + b" " + name + b"\0" + bytes.fromhex(object_id)))
    if not entries and not root:
        return None
    body = b"".join(entry for _key, entry in sorted(entries, key=lambda item: item[0]))
    return _git_object_id("tree", body)


def _verify_successor_authority(commit_id: str, tree_id: str, commit_object_path: Path) -> None:
    try:
        commit_object_path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise RuntimeError("successor commit object must be supplied from outside the frozen source tree")
    try:
        raw = commit_object_path.read_bytes()
    except OSError as exc:
        raise RuntimeError("successor commit object is unreadable") from exc
    if not raw or len(raw) > 1_048_576 or _git_object_id("commit", raw) != commit_id:
        raise RuntimeError("successor commit object does not match the supplied commit ID")
    tree_line = next((line for line in raw.splitlines() if line.startswith(b"tree ")), None)
    if tree_line != f"tree {tree_id}".encode("ascii"):
        raise RuntimeError("successor commit object does not bind the supplied source tree")
    observed_tree = _filesystem_git_tree(ROOT, root=True)
    if observed_tree != tree_id:
        raise RuntimeError("frozen source bytes do not match the supplied successor tree")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, child in pairs:
        if key in value:
            raise RuntimeError(f"duplicate runtime source denominator key: {key}")
        value[key] = child
    return value


def _load_source_denominator() -> dict[str, object]:
    try:
        value = json.loads(
            DENOMINATOR.read_text(encoding="utf-8"),
            object_pairs_hook=_pairs,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("runtime source denominator authority is unreadable") from exc
    if type(value) is not dict or set(value) != {"schema", "paths", "content_sha256"}:
        raise RuntimeError("runtime source denominator authority shape is invalid")
    paths = value.get("paths")
    if (
        value.get("schema") != "bms.ngs-molbio.runtime-source-denominator.v1"
        or type(paths) is not list
        or not paths
        or len(paths) > 256
        or any(type(path) is not str or not path or path.startswith("/") for path in paths)
        or any(".." in Path(path).parts for path in paths)
        or len(paths) != len(set(paths))
        or DENOMINATOR_RELATIVE not in paths
        or value.get("content_sha256") != _content_sha256(value)
    ):
        raise RuntimeError("runtime source denominator authority is invalid or digest-divergent")
    return value


def _load_n0_receipt_authority() -> tuple[str, str]:
    try:
        value = json.loads(N0_RECEIPT.read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("N0 verification receipt authority is unreadable") from exc
    if type(value) is not dict:
        raise RuntimeError("N0 verification receipt authority must be an object")
    content_sha256 = value.get("content_sha256")
    package_fingerprint = value.get("payload_fingerprint_sha256")
    if (
        type(content_sha256) is not str
        or _SHA256_RE.fullmatch(content_sha256) is None
        or content_sha256 != _content_sha256(value)
        or type(package_fingerprint) is not str
        or _SHA256_RE.fullmatch(package_fingerprint) is None
    ):
        raise RuntimeError("N0 verification receipt authority is invalid or digest-divergent")
    return content_sha256, package_fingerprint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--successor-source-commit",
        required=True,
        type=lambda value: _git_object(value, "successor source commit"),
        help="Externally attested successor commit; the builder never invokes Git.",
    )
    parser.add_argument(
        "--successor-source-tree",
        required=True,
        type=lambda value: _git_object(value, "successor source tree"),
        help="Externally attested exact successor tree; the builder never invokes Git.",
    )
    parser.add_argument(
        "--successor-commit-object",
        required=True,
        type=Path,
        help="Path to the raw, header-free Git commit object exported by the release authority.",
    )
    arguments = parser.parse_args()
    _verify_successor_authority(
        arguments.successor_source_commit,
        arguments.successor_source_tree,
        arguments.successor_commit_object,
    )
    denominator = _load_source_denominator()
    n0_receipt_content_sha256, n0_package_fingerprint = _load_n0_receipt_authority()
    source_paths = denominator["paths"]
    assert isinstance(source_paths, list)
    authorities: list[dict[str, object]] = []
    for relative in source_paths:
        assert isinstance(relative, str)
        path = (ROOT / relative).resolve()
        try:
            path.relative_to(ROOT.resolve())
        except ValueError as exc:
            raise RuntimeError(f"runtime source path escapes repository: {relative}") from exc
        raw = path.read_bytes()
        if not raw:
            raise RuntimeError(f"runtime source authority is empty: {relative}")
        authorities.append({"path": relative, "sha256": _sha256(raw), "size_bytes": len(raw)})
    document: dict[str, object] = {
        "schema": "bms.ngs-molbio.runtime-implementation.v1",
        "baseline_source_commit": "d2fc413d6d0224fe9fbecb1cb1797e0456ca1517",
        "baseline_source_tree": "f89094ba373e3dd8fa181fd17d942e54a6f0f63e",
        "successor_source_commit": arguments.successor_source_commit,
        "successor_source_tree": arguments.successor_source_tree,
        "n0_package_fingerprint": n0_package_fingerprint,
        "n0_receipt_content_sha256": n0_receipt_content_sha256,
        "implementation_state": "implemented_unverified",
        "release_acceptance_state": "open",
        "verification_state": "source_audit_only",
        "tests_run": 0,
        "capability_exposure_state": "fail_closed",
        "dataset_exposure_state": "fail_closed",
        "binding_runtime_state": "implemented_unverified",
        "adapter_runtime_count": 27,
        "connector_event_runtime_count": 12,
        "payload_scanner_runtime_state": "implemented_unverified",
        "source_denominator": {
            "path": DENOMINATOR_RELATIVE,
            "content_sha256": denominator["content_sha256"],
        },
        "phases": [
            {
                "phase_id": phase_id,
                "source_state": "implemented",
                "acceptance_state": "unverified",
                "evidence": evidence,
            }
            for phase_id, evidence in PHASES
        ],
        "source_authorities": authorities,
    }
    document["content_sha256"] = _content_sha256(document)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(OUTPUT), "source_count": len(authorities), "content_sha256": document["content_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
