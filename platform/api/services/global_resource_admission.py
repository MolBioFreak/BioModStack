"""Derived allocations owned by the existing global experiment resource policy.

Synchronous entrypoints run in worker threads. No caller handoff or completed
scientific-job lease authorizes derived work. Every acquire resolves this process'
actual target and fresh capacity under the same SQLite write fence as workflows.
Reservations are cooperative admission, not kernel memory/disk isolation.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import uuid

import psutil
from paths import get_experiment_db_url
from services.ngs_molbio_quiescence import _open_fence_fd, _release_fence_fd

SCHEMA = "bms.global-derived-resource-allocation.v1"
ACTIVE_WORKFLOW_STATES = ("admitted", "queued")


class ResourceCapacityUnavailable(RuntimeError):
    """A target/policy/capacity refusal, never artifact corruption."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _db_path():
    url = get_experiment_db_url()
    if not url.startswith("sqlite") or ":///" not in url:
        raise ResourceCapacityUnavailable("global resource store must be SQLite")
    return Path(url.split(":///", 1)[1]).expanduser().resolve()


@contextmanager
def _transaction():
    # Participate in the existing backup/acceptance writer fence. Never create
    # an unmigrated database on a GET, reserve, or release path.
    fence = _open_fence_fd()
    db = None
    try:
        fcntl.flock(fence, fcntl.LOCK_SH | fcntl.LOCK_NB)
        db = sqlite3.connect(_db_path().as_uri() + "?mode=rw", uri=True, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except (sqlite3.Error, BlockingIOError) as exc:
        if db is not None:
            db.rollback()
        raise ResourceCapacityUnavailable("global resource authority unavailable") from exc
    except BaseException:
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            db.close()
        _release_fence_fd(fence)


def _machine():
    # Stable host identity prevents a different host mounting the same store
    # from claiming local flock evidence about a live remote process.
    value = Path("/etc/machine-id").read_text().strip()
    if re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ResourceCapacityUnavailable("execution host identity unavailable")
    return value


def _target():
    target = os.environ.get("BMS_EXECUTION_TARGET_ID", "local").strip()
    if not target or re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", target) is None:
        raise ResourceCapacityUnavailable("execution target identity invalid")
    # Explicit remote policy rows use the SAME global owner and units. Never
    # inherit the managed workstation policy on a named remote/cloud executor.
    policy = "managed-workflows" if target == "local" else "execution-target:" + target
    return target, policy, _machine()


def _owner_lock(identifier):
    if re.fullmatch(r"[0-9a-f]{32}", identifier) is None:
        raise ResourceCapacityUnavailable("invalid resource owner identity")
    root = _db_path().parent / ".global-resource-owners"
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ResourceCapacityUnavailable("unsafe global resource owner directory")
    descriptor = os.open(root / identifier, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ResourceCapacityUnavailable("unsafe global resource owner lock")
    return descriptor


def _move_reason(journal):
    return "storage_move:" + json.dumps(journal, sort_keys=True, separators=(",", ":"))


def _move_journal(row):
    reason = row["release_reason"] or ""
    return json.loads(reason[len("storage_move:"):]) if reason.startswith("storage_move:") else None


def _row_storage_path(row):
    move = _move_journal(row)
    if move and move["pending"]:
        raise ResourceCapacityUnavailable("interrupted storage move requires explicit recovery")
    return Path(move["destination"] if move else row["storage_path"])


def _storage_rows(db, path):
    rows = []
    for row in db.execute("SELECT * FROM derived_resource_reservations WHERE state!='released' "
                          "ORDER BY created_at,reservation_id"):
        move = _move_journal(row)
        if move and move["pending"]:
            if str(path) in {move["source"], move["destination"], row["storage_path"]}:
                raise ResourceCapacityUnavailable("interrupted storage move requires explicit recovery")
        elif str(_row_storage_path(row)) == str(path):
            rows.append(row)
    return rows


def recover_quiescent_storage_move(reservation_id):
    """Explicit restart repair; retain the entire charge if location is ambiguous."""
    target, _, machine = _target()
    descriptor = _owner_lock(reservation_id)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        with _transaction() as db:
            row = db.execute("SELECT * FROM derived_resource_reservations WHERE reservation_id=?",
                             (reservation_id,)).fetchone()
            if row is None or row["target_id"] != target or row["machine_id"] != machine:
                return False
            move = _move_journal(row)
            if not move or not move["pending"] or row["state"] == "released":
                return False
            locations = [Path(move[key]) for key in ("source", "destination") if Path(move[key]).exists()]
            if len(locations) != 1 or owned_storage_bytes(locations[0]) > row["disk_bytes"]:
                return False
            move.update(destination=str(locations[0]), pending=False)
            db.execute("UPDATE derived_resource_reservations SET state='orphaned',cpu_threads=0,dram_bytes=0,"
                       "release_reason=?,updated_at=? WHERE reservation_id=?",
                       (_move_reason(move), _now(), reservation_id))
        return True
    finally:
        os.close(descriptor)


def _recover(db, target, machine):
    # Lock files are never unlinked: inode replacement must not split ownership.
    # No timestamp, PID reuse, heartbeat, or expired scientific lease releases
    # live work. Only a same-host independently acquired exclusive lock does.
    for row in db.execute("SELECT * FROM derived_resource_reservations WHERE target_id=? "
                          "AND machine_id=? AND state IN ('active','retained')", (target, machine)):
        descriptor = _owner_lock(row["reservation_id"])
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            db.execute("UPDATE derived_resource_reservations SET state='orphaned', "
                       "cpu_threads=0, dram_bytes=0, updated_at=?, release_reason=CASE "
                       "WHEN release_reason LIKE 'storage_move:%' THEN release_reason ELSE ? END "
                       "WHERE reservation_id=? AND state IN ('active','retained')",
                       (_now(), "resident_storage_owner_quiescent" if row["state"] == "retained" else
                        "owner_lock_quiescent; disk retained pending cleanup", row["reservation_id"]))
        finally:
            os.close(descriptor)


def _positive(value, name, *, zero=False):
    if type(value) is not int or value < (0 if zero else 1):
        raise ResourceCapacityUnavailable(name + " must be integer allocation units")
    return value


def _process_limits():
    cpu = len(os.sched_getaffinity(0))
    ram = psutil.virtual_memory().available
    # cgroup v2 constrains containers even when host memory is much larger.
    for line in Path("/proc/self/cgroup").read_text().splitlines():
        if not line.startswith("0::"):
            continue
        group = Path("/sys/fs/cgroup") / line[3:].lstrip("/")
        while group == Path("/sys/fs/cgroup") or Path("/sys/fs/cgroup") in group.parents:
            memory = group / "memory.max"
            if memory.exists():
                limit = memory.read_text().strip()
                if limit != "max":
                    ram = min(ram, max(0, int(limit) - int((group / "memory.current").read_text())))
            quota_path = group / "cpu.max"
            if quota_path.exists():
                quota, period = quota_path.read_text().split()
                if quota != "max":
                    cpu = min(cpu, int(quota) // int(period))
            if group == Path("/sys/fs/cgroup"):
                break
            group = group.parent
    return cpu, ram


def _capacity(db, storage_root, *, resident_credit=0):
    target, policy_id, machine = _target()
    policy = db.execute("SELECT * FROM resource_admission_policy WHERE policy_id=?", (policy_id,)).fetchone()
    if policy is None:
        raise ResourceCapacityUnavailable("global policy missing for execution target " + target)
    db.execute("UPDATE resource_admission_policy SET lock_generation=lock_generation+1, updated_at=? "
               "WHERE policy_id=?", (_now(), policy_id))
    _recover(db, target, machine)
    root = Path(storage_root).resolve(strict=True)
    device = str(root.stat().st_dev)
    disk = shutil.disk_usage(root)
    process_cpu, process_ram = _process_limits()
    cpu_limit = min(_positive(policy["cpu_thread_limit"], "CPU"), process_cpu)
    memory_limit = _positive(policy["dram_byte_limit"], "DRAM")
    # Null disk policy means the actual filesystem capacity, not an NGS limit.
    disk_limit = min(policy["disk_byte_limit"] or disk.total, disk.total)
    totals = db.execute("SELECT COALESCE(SUM(cpu_threads),0), COALESCE(SUM(dram_bytes),0) "
                        "FROM derived_resource_reservations WHERE policy_id=? AND state!='released'", (policy_id,)).fetchone()
    disk_used = db.execute("SELECT COALESCE(SUM(disk_bytes),0) FROM derived_resource_reservations "
                          "WHERE machine_id=? AND storage_device=? AND state!='released'", (machine, device)).fetchone()[0]
    # Resident bytes already reduced disk.free; only unmaterialized commitments
    # reduce physical availability again. Unknown crashed active work remains
    # conservatively pending until the quiescent storage owner reconciles it.
    pending_disk = db.execute("SELECT COALESCE(SUM(disk_bytes),0) FROM derived_resource_reservations "
        "WHERE machine_id=? AND storage_device=? AND state NOT IN ('released','retained') "
        "AND COALESCE(release_reason,'') NOT LIKE 'storage_move:%' "
        "AND COALESCE(release_reason,'') NOT IN ('resident_storage_owner_quiescent',"
        "'quiescent_owned_storage_reconciled')", (machine, device)).fetchone()[0]
    workflow_cpu = workflow_ram = 0
    if policy_id == "managed-workflows":
        workflow_cpu, workflow_ram = db.execute(
            "SELECT COALESCE(SUM(cpu_threads),0), COALESCE(SUM(dram_bytes),0) FROM resource_admissions "
            "WHERE state IN ('admitted','queued')").fetchone()
    available = (max(0, cpu_limit - totals[0] - workflow_cpu),
                 max(0, min(memory_limit - totals[1] - workflow_ram, process_ram)),
                 max(0, min(disk_limit - disk_used, disk.free - pending_disk + resident_credit)))
    return target, policy, machine, root, device, available


@dataclass
class Allocation:
    reservation_id: str
    token: str
    cpu_threads: int
    dram_bytes: int
    disk_bytes: int
    receipt: dict
    _descriptor: int
    _closed: bool = False
    _resident: bool = False

    def retain(self, *, disk_bytes, dram_bytes=0):
        """After construction quiesces, keep only charged owned storage/cache RAM."""
        _positive(disk_bytes, "disk", zero=True)
        _positive(dram_bytes, "DRAM", zero=True)
        if self._closed or disk_bytes > self.disk_bytes or dram_bytes > self.dram_bytes:
            raise ResourceCapacityUnavailable("retained object exceeds admitted allocation")
        with _transaction() as db:
            result = db.execute("UPDATE derived_resource_reservations SET state='retained',cpu_threads=0,"
                                "dram_bytes=?,disk_bytes=?,updated_at=? WHERE reservation_id=? AND token=? AND state='active'",
                                (dram_bytes, disk_bytes, _now(), self.reservation_id, self.token))
            if result.rowcount != 1:
                raise ResourceCapacityUnavailable("resource allocation ownership lost")
        self.cpu_threads, self.dram_bytes, self.disk_bytes = 0, dram_bytes, disk_bytes
        self._resident = True

    def move_storage(self, destination):
        """Journal an owner-controlled same-filesystem rename before moving bytes.

        Reservation identity is immutable. Its mutable lifecycle reason records
        the physical location, so a crash never makes moved bytes look removed.
        A pending move blocks cleanup/adoption until explicit recovery.
        """
        destination = Path(destination).resolve(strict=False)
        if self._closed or not self._resident or destination.exists():
            raise ResourceCapacityUnavailable("storage move requires resident exclusive ownership")
        if str(destination.parent.stat().st_dev) != self.receipt["storage_device"]:
            raise ResourceCapacityUnavailable("resource storage moved across devices")
        with _transaction() as db:
            row = db.execute("SELECT * FROM derived_resource_reservations WHERE reservation_id=? AND token=?",
                             (self.reservation_id, self.token)).fetchone()
            if row is None or row["state"] != "retained":
                raise ResourceCapacityUnavailable("resource allocation ownership lost")
            source = _row_storage_path(row)
            journal = {"source": str(source), "destination": str(destination), "pending": True}
            db.execute("UPDATE derived_resource_reservations SET release_reason=?,updated_at=? WHERE reservation_id=?",
                (_move_reason(journal), _now(), self.reservation_id))
        os.replace(source, destination)
        journal["pending"] = False
        with _transaction() as db:
            db.execute("UPDATE derived_resource_reservations SET release_reason=?,updated_at=? WHERE reservation_id=? AND token=?",
                       (_move_reason(journal), _now(), self.reservation_id, self.token))

    def release(self, *, storage_removed=False):
        """Call only after all threads/children/readers quiesce.

        Disk remains charged unless the owner has actually removed the bytes.
        Published derived products are retained, not silently freed on exit.
        """
        if self._closed:
            return
        with _transaction() as db:
            db.execute("UPDATE derived_resource_reservations SET state=?,cpu_threads=0,dram_bytes=0,"
                       "disk_bytes=?,updated_at=?,release_reason=CASE WHEN release_reason LIKE 'storage_move:%' "
                       "THEN release_reason ELSE ? END WHERE reservation_id=? AND token=? "
                       "AND state IN ('active','retained')",
                       ("released" if storage_removed or not self.disk_bytes else "orphaned",
                        0 if storage_removed else self.disk_bytes, _now(),
                        "owner_quiescent_storage_removed" if storage_removed else
                        "resident_storage_owner_quiescent" if self._resident else "owner_quiescent_storage_retained",
                        self.reservation_id, self.token))
        self._closed = True
        os.close(self._descriptor)


def reserve(*, owner, storage_root, cpu_threads=1, dram_bytes=None, disk_bytes=None, owned_path=None, adopt_quiescent=False):
    """Resolve current target and atomically reserve requested units.

    None requests the currently available remainder of that global resource,
    suitable for a streamed builder whose input expansion has no fixed bound.
    The allocation, not the source BAM's compressed size, is its work envelope.
    """
    _positive(cpu_threads, "CPU", zero=True)
    if not isinstance(owner, str) or not owner or len(owner) > 512:
        raise ResourceCapacityUnavailable("invalid derived owner")
    identifier, token = uuid.uuid4().hex, uuid.uuid4().hex
    descriptor = _owner_lock(identifier)
    adopted_descriptors = []
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with _transaction() as db:
            surviving = 0
            if adopt_quiescent:
                # Transfer accounting only with independent quiescence proof.
                # The cache/product caller additionally holds its storage lock.
                root_path = Path(storage_root).resolve(strict=True)
                owned = Path(owned_path).resolve(strict=False) if owned_path is not None else root_path
                try:
                    owned.relative_to(root_path)
                except ValueError as exc:
                    raise ResourceCapacityUnavailable("owned storage escapes allocation root") from exc
                target_id, _, machine_id = _target()
                rows = _storage_rows(db, owned)
                for row in rows:
                    if row["target_id"] != target_id or row["machine_id"] != machine_id:
                        raise ResourceCapacityUnavailable("storage accounting belongs to another execution target")
                    old_descriptor = _owner_lock(row["reservation_id"])
                    adopted_descriptors.append(old_descriptor)
                    try:
                        fcntl.flock(old_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError as exc:
                        raise ResourceCapacityUnavailable("storage allocation still has a live owner") from exc
                surviving = owned_storage_bytes(owned)
                if disk_bytes is not None and surviving > disk_bytes:
                    raise ResourceCapacityUnavailable("surviving storage exceeds replacement allocation")
                for row in rows:
                    db.execute("UPDATE derived_resource_reservations SET state='released',cpu_threads=0,"
                        "dram_bytes=0,disk_bytes=0,updated_at=?,release_reason=? WHERE reservation_id=?",
                        (_now(), "quiescent_storage_transferred:" + identifier, row["reservation_id"]))
            target, policy, machine, root, device, available = _capacity(db, storage_root, resident_credit=surviving)
            owned = root if owned_path is None else Path(owned_path).resolve(strict=False)
            try:
                owned.relative_to(root)
            except ValueError as exc:
                raise ResourceCapacityUnavailable("owned storage escapes allocation root") from exc
            ram = available[1] if dram_bytes is None else _positive(dram_bytes, "DRAM", zero=True)
            disk = available[2] if disk_bytes is None else _positive(disk_bytes, "disk", zero=True)
            if (cpu_threads > available[0] or ram > available[1] or disk > available[2]
                    or dram_bytes is None and ram == 0 or disk_bytes is None and disk == 0):
                raise ResourceCapacityUnavailable("global target allocation capacity unavailable")
            receipt = {"schema": SCHEMA, "reservation_id": identifier, "target_id": target,
                       "machine_id": machine, "policy_id": policy["policy_id"], "policy_version": policy["policy_version"],
                       "policy_generation": policy["lock_generation"] + 1,
                       "requested": {"cpu_threads": cpu_threads, "dram_bytes": dram_bytes, "disk_bytes": disk_bytes},
                       "effective": {"cpu_threads": cpu_threads, "dram_bytes": ram, "disk_bytes": disk},
                       "storage_device": device, "storage_path": str(owned), "owner": owner}
            stamp = _now()
            db.execute("INSERT INTO derived_resource_reservations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (identifier, policy["policy_id"], policy["policy_version"], target, machine, owner, token,
                        "active", cpu_threads, ram, disk, device, str(owned),
                        json.dumps(receipt, sort_keys=True, separators=(",", ":")), stamp, stamp, None))
        return Allocation(identifier, token, cpu_threads, ram, disk, receipt, descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    finally:
        for old_descriptor in adopted_descriptors:
            os.close(old_descriptor)


@contextmanager
def derived_work(**kwargs):
    allocation = reserve(**kwargs)
    try:
        yield allocation
    finally:
        allocation.release()


def validate_receipt(receipt):
    """Validate historical provenance only; never turn a receipt into admission."""
    keys = {"schema", "reservation_id", "target_id", "machine_id", "policy_id", "policy_version",
            "policy_generation", "requested", "effective", "storage_device", "storage_path", "owner"}
    if not isinstance(receipt, dict) or set(receipt) != keys or receipt["schema"] != SCHEMA:
        raise ValueError("invalid global allocation receipt")
    for name in keys - {"requested", "effective", "policy_generation"}:
        if not isinstance(receipt[name], str) or not receipt[name]:
            raise ValueError("invalid global allocation identity")
    if type(receipt["policy_generation"]) is not int or receipt["policy_generation"] < 1:
        raise ValueError("invalid global policy generation")
    for field in ("requested", "effective"):
        value = receipt[field]
        if not isinstance(value, dict) or set(value) != {"cpu_threads", "dram_bytes", "disk_bytes"}:
            raise ValueError("invalid global allocation units")
        for name, amount in value.items():
            if field == "requested" and name != "cpu_threads" and amount is None:
                continue
            if type(amount) is not int or amount < 0:
                raise ValueError("invalid global allocation amount")
    return receipt


def owned_storage_bytes(path):
    """Count owned bytes without following links; missing namespace means zero.

    This is only for an already quiescent application-owned namespace, never a
    scientific result root or a pathname supplied by an HTTP client.
    """
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return 0
    if stat.S_ISREG(info.st_mode):
        return info.st_size
    if not stat.S_ISDIR(info.st_mode):
        raise ResourceCapacityUnavailable("unsafe resource storage namespace")
    return sum(owned_storage_bytes(child) for child in path.iterdir())


def reconcile_quiescent_storage(owned_path):
    """Explicit cleanup/adoption hook; refuses to alter any live allocation.

    Called after the existing product/cache owner has cleaned its attempt tree.
    Disk accounting shrinks only to surviving bytes, not to a lease timestamp.
    Multiple crashed attempts for one namespace charge surviving bytes once.
    """
    path = str(Path(owned_path).resolve(strict=False))
    target, _, machine = _target()
    descriptors = []
    try:
        with _transaction() as db:
            rows = _storage_rows(db, path)
            if any(row["target_id"] != target or row["machine_id"] != machine for row in rows):
                return False
            for row in rows:
                descriptor = _owner_lock(row["reservation_id"])
                descriptors.append(descriptor)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return False
            remaining = owned_storage_bytes(path)
            if remaining > sum(row["disk_bytes"] for row in rows):
                # Unadmitted legacy bytes are not retroactively called covered.
                return False
            for row in reversed(rows):
                charge = min(remaining, row["disk_bytes"])
                remaining -= charge
                db.execute("UPDATE derived_resource_reservations SET state=?,cpu_threads=0,dram_bytes=0,"
                           "disk_bytes=?,updated_at=?,release_reason=CASE WHEN release_reason LIKE 'storage_move:%' "
                           "THEN release_reason ELSE ? END WHERE reservation_id=?",
                           ("orphaned" if charge else "released", charge, _now(),
                            "quiescent_owned_storage_reconciled", row["reservation_id"]))
        return True
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def remove_quiescent_storage(owned_path, *, owner):
    """Explicit recovery of a registered transient namespace, never live work."""
    path = Path(owned_path).resolve(strict=False)
    target, _, machine = _target()
    descriptors = []
    try:
        with _transaction() as db:
            rows = _storage_rows(db, path)
            if not rows or any(row["owner"] != owner or row["target_id"] != target
                               or row["machine_id"] != machine for row in rows):
                return False
            for row in rows:
                descriptor = _owner_lock(row["reservation_id"])
                descriptors.append(descriptor)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return False
            if path.exists():
                if path.is_symlink() or not path.is_dir():
                    return False
                shutil.rmtree(path)
            for row in rows:
                db.execute("UPDATE derived_resource_reservations SET state='released',cpu_threads=0,"
                    "dram_bytes=0,disk_bytes=0,updated_at=?,release_reason=? WHERE reservation_id=?",
                    (_now(), "quiescent_transient_storage_removed", row["reservation_id"]))
            return True
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def publish_execution_target_readiness(*, target_id, remote_root, readiness):
    """Provisioner-only policy publication from authenticated actual-target I/O.

    Never infer remote capacity from the controller, provider marketing, or GPU
    telemetry. Publication does not admit work or copy the experiment database.
    """
    value = readiness.get("resources") if isinstance(readiness, dict) else None
    keys = {"cpu_threads", "dram_bytes", "disk_bytes", "free_disk_bytes", "storage_root",
            "storage_device", "machine_id"}
    if not isinstance(value, dict) or set(value) != keys or value["storage_root"] != remote_root:
        raise ResourceCapacityUnavailable("remote readiness resource identity is incomplete")
    for key in ("cpu_threads", "dram_bytes", "disk_bytes"):
        _positive(value[key], key)
    _positive(value["free_disk_bytes"], "free disk", zero=True)
    if (value["free_disk_bytes"] > value["disk_bytes"]
            or not isinstance(value["machine_id"], str)
            or re.fullmatch(r"[0-9a-f]{32}", value["machine_id"]) is None
            or not isinstance(value["storage_device"], str) or not value["storage_device"].isdigit()):
        raise ResourceCapacityUnavailable("remote readiness resource identity is invalid")
    policy = {"target_id": target_id, **value}
    version = hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    configure_execution_target_policy(target_id=target_id, cpu_thread_limit=value["cpu_threads"],
        dram_byte_limit=value["dram_bytes"], disk_byte_limit=value["disk_bytes"], policy_version=version)
    return {**policy, "policy_id": "execution-target:" + target_id, "policy_version": version}


def configure_execution_target_policy(*, target_id, cpu_thread_limit, dram_byte_limit,
                                      disk_byte_limit=None, policy_version):
    """Server-owned remote provisioner entrypoint, not an operator job parameter.

    A named executor must be provisioned with its own global policy and matching
    BMS_EXECUTION_TARGET_ID. This does not relabel local/API work as remote, infer
    provider capacity, or change the existing managed-workflows scientific caps.
    """
    if (not isinstance(target_id, str) or target_id == "local"
            or re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", target_id) is None):
        raise ResourceCapacityUnavailable("explicit nonlocal execution target required")
    _positive(cpu_thread_limit, "CPU")
    _positive(dram_byte_limit, "DRAM")
    if disk_byte_limit is not None:
        _positive(disk_byte_limit, "disk")
    if not isinstance(policy_version, str) or not policy_version or len(policy_version) > 64:
        raise ResourceCapacityUnavailable("global policy version required")
    policy_id = "execution-target:" + target_id
    with _transaction() as db:
        active = db.execute("SELECT COALESCE(SUM(cpu_threads),0), COALESCE(SUM(dram_bytes),0), "
                            "COALESCE(SUM(disk_bytes),0) FROM derived_resource_reservations "
                            "WHERE policy_id=? AND state!='released'", (policy_id,)).fetchone()
        if (active[0] > cpu_thread_limit or active[1] > dram_byte_limit
                or disk_byte_limit is not None and active[2] > disk_byte_limit):
            raise ResourceCapacityUnavailable("new target policy would revoke active allocations")
        db.execute("INSERT INTO resource_admission_policy "
                   "(policy_id,policy_version,cpu_thread_limit,dram_byte_limit,disk_byte_limit,lock_generation,updated_at) "
                   "VALUES (?,?,?,?,?,0,?) ON CONFLICT(policy_id) DO UPDATE SET "
                   "policy_version=excluded.policy_version,cpu_thread_limit=excluded.cpu_thread_limit,"
                   "dram_byte_limit=excluded.dram_byte_limit,disk_byte_limit=excluded.disk_byte_limit,"
                   "lock_generation=resource_admission_policy.lock_generation+1,updated_at=excluded.updated_at",
                   (policy_id, policy_version, cpu_thread_limit, dram_byte_limit, disk_byte_limit, _now()))
