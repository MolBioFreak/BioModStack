#!/usr/bin/env python3
"""Publish/reuse a shared immutable image; transport only its receipt/reference."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

# Also support importlib-based consumers outside the scripts directory.
sys.path.insert(0, str(Path(__file__).absolute().parent))
from lib.shared_runtime_images import publish_image, verify_image


class RuntimeImageAttestationError(RuntimeError):
    """The registered image could not be bound to the shared execution object."""


def _expected_digest(value: str) -> str:
    expected = value.removeprefix("sha256:").lower()
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise RuntimeImageAttestationError("expected runtime image digest is not SHA-256")
    return expected


def _identity(info: os.stat_result) -> dict[str, int]:
    return {"device": info.st_dev, "inode": info.st_ino, "bytes": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}


def _write_receipt(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while writing runtime image receipt")
            view = view[written:]
        os.fsync(descriptor)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    os.chmod(path, 0o444)


def create_verified_image_reference(
    *, image: Path, expected_sha256: str, store_root: Path, reference: Path, receipt: Path,
) -> dict[str, Any]:
    expected = _expected_digest(expected_sha256)
    image, store_root, reference, receipt = (
        Path(os.path.abspath(p)) for p in (image, store_root, reference, receipt)
    )
    if reference == receipt or os.path.lexists(reference) or os.path.lexists(receipt):
        raise RuntimeImageAttestationError("runtime reference outputs must be new distinct paths")
    before = image.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeImageAttestationError("runtime image is not a regular file")
    try:
        shared = publish_image(image, store_root, expected)
        measured = verify_image(shared, expected)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeImageAttestationError(f"runtime image publication failed: {exc}") from exc
    after = image.lstat()
    if _identity(before) != _identity(after) or before.st_mode != after.st_mode:
        raise RuntimeImageAttestationError("runtime image source changed during publication")
    # Keep the scientific v1 receipt contract: a shared CAS object is still an
    # independently copied immutable snapshot, not a link to mutable source bytes.
    payload = {
        "schema_name": "cm_runtime_image_receipt", "schema_version": 1,
        "status": "verified_immutable_snapshot",
        "measurement_method": "shared_cas_open_no_follow+source_identity_recheck+sha256",
        "expected_sha256": expected,
        "observed_source": {"path": image.name, **_identity(before), "sha256": expected},
        "verified_snapshot": {"name": shared.name, "bytes": measured["size"], "sha256": expected},
    }
    reference.parent.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        _write_receipt(receipt, payload)
        created.append(receipt)
        _write_receipt(reference, {
            "schema_name": "cm_shared_runtime_image_reference", "schema_version": 1,
            "path": str(shared), "identity": measured,
            "receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
        })
        created.append(reference)
        return payload
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def resolve_verified_image_reference(
    *, reference: Path, receipt: Path, expected_sha256: str, store_root: Path,
    executing_image: Path | None = None,
) -> Path:
    """Resolve only the configured CAS object, never an unchecked receipt path.

    Rehash on every selection (including resume), compare its pinned identity,
    and optionally require Apptainer's actual executing image pathname to agree.
    Reference/receipt hashes alone are not authority: the registry and configured
    store independently determine the only permitted object and its bytes.
    """
    expected = _expected_digest(expected_sha256)
    shared = Path(os.path.abspath(store_root)) / "objects" / "sha256" / expected / "runtime.sif"
    try:
        ref = json.loads(reference.read_text(encoding="utf-8"))
        receipt_bytes = receipt.read_bytes()
        payload = json.loads(receipt_bytes)
        if (not isinstance(ref, dict) or set(ref) != {
            "schema_name", "schema_version", "path", "identity", "receipt_sha256"
        } or ref["schema_name"] != "cm_shared_runtime_image_reference"
                or ref["schema_version"] != 1 or ref["path"] != str(shared)
                or ref["receipt_sha256"] != hashlib.sha256(receipt_bytes).hexdigest()):
            raise RuntimeImageAttestationError("runtime image reference shape/path/receipt mismatch")
        if executing_image is not None and Path(os.path.abspath(executing_image)) != shared:
            raise RuntimeImageAttestationError("actual executing runtime image differs from shared reference")
        measured = verify_image(shared, expected)
        if ref["identity"] != measured:
            raise RuntimeImageAttestationError("shared runtime image identity changed since preflight")
        if (not isinstance(payload, dict) or set(payload) != {
            "schema_name", "schema_version", "status", "measurement_method", "expected_sha256",
            "observed_source", "verified_snapshot"
        } or payload["schema_name"] != "cm_runtime_image_receipt"
                or payload["schema_version"] != 1 or payload["status"] != "verified_immutable_snapshot"
                or payload["expected_sha256"] != expected
                or payload["observed_source"]["sha256"] != expected
                or payload["observed_source"]["bytes"] != measured["size"]
                or payload["verified_snapshot"] != {
                    "name": shared.name, "bytes": measured["size"], "sha256": expected
                }):
            raise RuntimeImageAttestationError("runtime image receipt differs from registry/shared image")
        return shared
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise RuntimeImageAttestationError(f"runtime image reference verification failed: {exc}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--store-root", type=Path, default=os.environ.get("BMS_RUNTIME_IMAGE_STORE"))
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--resolve-reference", action="store_true")
    parser.add_argument("--executing-image", type=Path)
    args = parser.parse_args()
    try:
        registry = json.loads(args.registry.read_text(encoding="utf-8"))
        expected = registry["container_digest"]
        if not isinstance(expected, str):
            raise RuntimeImageAttestationError("registry container digest is not a string")
        if args.store_root is None:
            raise RuntimeImageAttestationError("runtime image store must be configured")
        common: dict[str, Any] = dict(expected_sha256=expected, store_root=args.store_root,
                      reference=args.reference, receipt=args.receipt)
        if args.resolve_reference:
            print(resolve_verified_image_reference(**common, executing_image=args.executing_image))
        else:
            if args.image is None:
                raise RuntimeImageAttestationError("publication requires --image")
            create_verified_image_reference(image=args.image, **common)
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"Protenix runtime image preflight failed: {exc}\n")


if __name__ == "__main__":
    main()
