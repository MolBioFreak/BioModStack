"""Reference/lease and explicit retirement operations for the shared SIF store.

All writers use shared_runtime_images' lifecycle flock (then digest flock).
state.json is the sole versioned reference authority, not a second image catalog.
Legacy .env files are compatibility projections. A crash between state and env
publication conservatively pins both releases and blocks retirement until repaired.

Leases MUST be acquired at admission, before releasing a queued/running/resumable
job to another process, and explicitly released only after its final possible use.
They do not expire: process death is not evidence that a job cannot resume.
Current lanes alone NEVER establish job coverage. Retirement therefore always
requires an operator's maintenance/quiescence assertion covering unintegrated jobs,
external aliases and admission paths. The lock fences cooperating clients only;
ordinary workers must not own/write this store. No unattended GC is provided.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .shared_runtime_images import (
    SharedRuntimeImageError, _absolute, _digest, _directory, _file, _check_file,
    _lock, verify_image,
)

Error = SharedRuntimeImageError
LANES = {"development", "production"}


def object_path(root, digest):
    return _absolute(root) / "objects" / "sha256" / _digest(digest) / "runtime.sif"


@contextmanager
def transaction(root):
    root = _absolute(root)
    with _lock(root, "lifecycle"):
        yield root


def _read(path):
    with _file(path) as (fd, parent, before):
        if before.st_nlink != 1 or before.st_size > 16 * 1024 * 1024:
            raise Error("invalid reference file")
        data = bytearray()
        while chunk := os.read(fd, 65536):
            data.extend(chunk)
        _check_file(path, fd, parent, before)
        return data.decode("utf-8")


def atomic_write(path, text):
    with _directory(path.parent, create=True) as fd:
        temporary = ".transaction-" + uuid.uuid4().hex
        out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                      0o600, dir_fd=fd)
        try:
            with os.fdopen(out, "w") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path.name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass


def _unique_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise Error("duplicate reference state key")
        value[key] = item
    return value


def load_state(root):
    try:
        state = json.loads(_read(root / "references" / "state.json"), object_pairs_hook=_unique_keys)
    except FileNotFoundError:
        state = {"schema_version": 1, "generation": 0, "current": {}, "releases": {}, "leases": {}}
        # Upgrade the existing publisher's exact .env format without losing old
        # lane roots. Unknown/hand-edited formats require operator reconciliation.
        for lane in sorted(LANES):
            try:
                text = _read(root / "references" / f"{lane}.env")
            except FileNotFoundError:
                continue
            images = {}
            for line in text.splitlines()[2:]:
                key, separator, path = line.partition("=")
                if not separator or key in images or not key.startswith("BMS_"):
                    raise Error("unknown legacy runtime reference")
                digest = _digest(Path(path).parent.name)
                if path != str(object_path(root, digest)):
                    raise Error("unknown legacy runtime reference path")
                images[key] = {"sha256": digest, "path": path}
            release = {"schema_version": 1, "lane": lane, "images": images, "created_ns": 0}
            if not images or text != environment_text(root, release):
                raise Error("unknown legacy runtime reference format")
            key = "legacy-" + lane
            state["releases"][key] = release
            state["current"][lane] = key
        return state
    def require(condition):
        if not condition:
            raise Error("unknown or invalid runtime reference state")

    try:
        require(set(state) == {"schema_version", "generation", "current", "releases", "leases"})
        require(state["schema_version"] == 1 and type(state["generation"]) is int)
        require(state["generation"] >= 0)
        require(isinstance(state["current"], dict) and set(state["current"]) <= LANES)
        require(isinstance(state["releases"], dict) and isinstance(state["leases"], dict))
        for key, release in state["releases"].items():
            require(isinstance(key, str) and key)
            require(set(release) == {"schema_version", "lane", "images", "created_ns"})
            require(release["schema_version"] == 1 and release["lane"] in LANES)
            require(isinstance(release["images"], dict) and release["images"])
            for env, image in release["images"].items():
                require(isinstance(env, str) and env.startswith("BMS_") and env.replace("_", "").isalnum())
                require(set(image) == {"sha256", "path"})
                require(image["sha256"] == _digest(image["sha256"]))
                require(image["path"] == str(object_path(root, image["sha256"])))
        for lane, key in state["current"].items():
            require(state["releases"][key]["lane"] == lane)
        for key, lease in state["leases"].items():
            require(isinstance(key, str) and key)
            require(set(lease) == {"owner", "identities", "created_ns"})
            require(isinstance(lease["owner"], str) and lease["owner"])
            require(isinstance(lease["identities"], dict) and lease["identities"])
            for digest, receipt in lease["identities"].items():
                require(digest == _digest(digest) and receipt["sha256"] == digest)
                require(set(receipt) == {"sha256", "size", "device", "inode", "mtime_ns", "ctime_ns"})
                require(all(type(v) is int for k, v in receipt.items() if k != "sha256"))
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        raise Error("unknown or invalid runtime reference state") from exc
    return state


def save_state(root, state):
    state["generation"] += 1
    atomic_write(root / "references" / "state.json", json.dumps(state, sort_keys=True, indent=2) + "\n")


def environment_text(root, release):
    lines = ["# Managed shared runtime references; image digest is encoded in each path.",
             f"BMS_RUNTIME_IMAGE_STORE={root}"]
    lines.extend(f"{key}={image['path']}" for key, image in sorted(release["images"].items()))
    return "\n".join(lines) + "\n"


def commit_release(root, lane, images):
    """Caller holds transaction. Every prior release remains a rollback root."""
    state = load_state(root)
    release_id = uuid.uuid4().hex
    release = {"schema_version": 1, "lane": lane, "images": images, "created_ns": time.time_ns()}
    state["releases"][release_id] = release
    state["current"][lane] = release_id
    save_state(root, state)  # Pin new AND old objects before exposing the projection.
    target = root / "references" / f"{lane}.env"
    atomic_write(target, environment_text(root, release))
    return target


def select_release(root, lane, release_id):
    """Rollback/select an immutable retained manifest, never a mutable alias."""
    with transaction(root) as root:
        state = load_state(root)
        release = state["releases"][release_id]
        if lane not in LANES or release["lane"] != lane:
            raise Error("release belongs to a different lane")
        for image in release["images"].values():
            verify_image(Path(image["path"]), image["sha256"])
        state["current"][lane] = release_id
        save_state(root, state)
        atomic_write(root / "references" / f"{lane}.env", environment_text(root, release))


def forget_release(root, release_id):
    """Explicit metadata-only retention decision; never forget a current lane."""
    with transaction(root) as root:
        state = load_state(root)
        if release_id in state["current"].values():
            raise Error("cannot forget current release")
        del state["releases"][release_id]
        save_state(root, state)


def acquire_lease(root, digests, *, owner):
    """Atomically verify/pin exact objects. Returns (durable lease id, receipts).

    Call before job admission, persist the id in its receipt, and retain it across
    queueing/restarts/resume. Release explicitly after all execution has ended.
    """
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("lease owner/job identity is required")
    digests = sorted({_digest(d) for d in digests})
    if not digests:
        raise ValueError("lease requires image digests")
    with transaction(root) as root:
        state = load_state(root)
        return _acquire_lease_locked(root, state, digests, owner)


def _acquire_lease_locked(root, state, digests, owner):
    identities = {d: verify_image(object_path(root, d), d) for d in digests}
    token = uuid.uuid4().hex
    state["leases"][token] = {"owner": owner, "identities": identities, "created_ns": time.time_ns()}
    save_state(root, state)
    return token, identities


def acquire_lane_lease(root, lane, *, owner):
    """Resolve immutable current manifest AND pin under one fence.

    Returns (release_id, lease_token, identities). A lane switch cannot occur
    between resolution and pinning. Persist these in the admission receipt.
    """
    if lane not in LANES or not isinstance(owner, str) or not owner.strip():
        raise ValueError("known lane and lease owner/job identity are required")
    with transaction(root) as root:
        state = load_state(root)
        release_id = state["current"][lane]
        digests = {image["sha256"] for image in state["releases"][release_id]["images"].values()}
        token, identities = _acquire_lease_locked(root, state, digests, owner)
        return release_id, token, identities


def release_lease(root, token, *, owner):
    with transaction(root) as root:
        state = load_state(root)
        if state["leases"][token]["owner"] != owner:
            raise Error("lease owner mismatch")
        del state["leases"][token]
        save_state(root, state)


def _reference_audit(root, state):
    """Unrecognized reference files or stale projections block retirement."""
    with _directory(root) as fd:
        if set(os.listdir(fd)) - {"objects", ".locks", "references", "quarantine"}:
            raise Error("unknown store entries/references; explicit inventory required")
    expected = {"state.json"} | {f"{lane}.env" for lane in state["current"]}
    try:
        with _directory(root / "references") as fd:
            unknown = set(os.listdir(fd)) - expected
    except FileNotFoundError:
        if state["generation"] != 0:
            raise Error("missing runtime references")
        unknown = set()
    if unknown:
        raise Error("unknown reference files: " + ", ".join(sorted(unknown)))
    for lane, release_id in state["current"].items():
        actual = _read(root / "references" / f"{lane}.env")
        if actual != environment_text(root, state["releases"][release_id]):
            raise Error("unknown or stale lane projection; republish/select release to repair")


def _plan(root, digest):
    state = load_state(root)
    _reference_audit(root, state)
    path = object_path(root, digest)
    identity = verify_image(path, digest)
    with _directory(path.parent) as fd:
        if set(os.listdir(fd)) != {"runtime.sif"}:
            raise Error("unknown references or contents in runtime object")
    reasons = []
    aliases = []
    for key, release in state["releases"].items():
        for env, image in release["images"].items():
            if image["sha256"] == digest:
                reasons.append(f"retained-release:{key}:{env}")
                if state["current"].get(release["lane"]) == key:
                    aliases.append(f"{release['lane']}.env:{env}")
    reasons.extend(f"lease:{key}:{lease['owner']}" for key, lease in state["leases"].items()
                   if digest in lease["identities"])
    return {"schema_version": 1, "store_root": str(root), "generation": state["generation"],
            "digest": digest, "path": str(path), "identity": identity,
            "allocated_bytes": path.stat().st_blocks * 512, "reasons": sorted(reasons),
            "aliases": sorted(aliases), "job_reference_coverage": "unproven",
            "requires_maintenance_quiescence": True}


def plan_retirement(root, digest):
    """Dry-run exact identity and why-kept report. Never deletes/renames bytes."""
    with transaction(root) as root:
        return _plan(root, _digest(digest))


def apply_retirement(root, plan, *, maintenance_authorization):
    """Revalidate approved plan under admission fence, quarantine by rename.

    Authorization must assert externally fenced admissions, quiescent untracked
    jobs, and an audited absence of unmanaged aliases/references. It is NOT proof
    furnished by this library. No override bypasses tracked roots or unknown refs.
    Returns the quarantine path; no physical deletion/automatic GC is performed.
    """
    if not isinstance(maintenance_authorization, str) or not maintenance_authorization.strip():
        raise Error("explicit maintenance/quiescence authorization is required")
    with transaction(root) as root:
        current = _plan(root, _digest(plan["digest"]))
        if current != plan:
            raise Error("retirement plan is stale; review a new plan")
        if current["reasons"]:
            raise Error("runtime image is retained by references or leases")
        # Same-parent rename preserves the 0500 object mode (moving a directory
        # across parents requires write access to update '..' on Linux). Keep
        # approval separately, without opening a writable window on the object.
        name = ".quarantine-" + current["digest"] + "-" + uuid.uuid4().hex
        quarantine = object_path(root, current["digest"]).parent.parent / name
        atomic_write(root / "quarantine" / (name + ".json"), json.dumps({
            "schema_version": 1, "plan": current, "authorization": maintenance_authorization,
            "quarantined_ns": time.time_ns(),
        }, sort_keys=True) + "\n")
        with _directory(quarantine.parent) as fd:
            try:
                os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise Error("quarantine destination already exists")
            os.rename(current["digest"], name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        return quarantine
