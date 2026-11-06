#!/usr/bin/env python3
"""Remove redundant live-status run events from the experiments database.

Until the live runtime receipt stopped carrying ``elapsed_seconds``, every
2-second reconcile sweep appended a ``core_live_projected`` run event whose only
difference from the previous one was that counter. This tool keeps, per
workflow run, every live event whose payload (ignoring ``elapsed_seconds``)
differs from the previously kept one, and deletes the rest. All other event
types are untouched.

run_events is append-only by trigger. The tool drops the two immutability
triggers, deletes, and recreates them from their exact stored DDL inside one
transaction, so the schema attestation is unchanged.

Default is a dry run. ``--apply`` requires the API to be stopped: it takes the
global-experiment dispatcher lock and refuses if another process holds it.
Take a backup first (``--backup PATH`` writes a consistent SQLite online
backup before any change). Run ``VACUUM`` separately to return the space.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sqlite3
import sys
from pathlib import Path

_TRIGGERS = ("trg_experiment_run_events_immutable_update", "trg_experiment_run_events_immutable_delete")
_VOLATILE = ("elapsed_seconds",)


def _stable(payload: str) -> str:
    try:
        value = json.loads(payload)
    except ValueError:
        return payload
    if isinstance(value, dict):
        for key in _VOLATILE:
            value.pop(key, None)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def redundant_event_ids(connection: sqlite3.Connection) -> tuple[list[int], int]:
    redundant: list[int] = []
    total = 0
    last: dict[str, str] = {}
    rows = connection.execute(
        "SELECT id, workflow_run_id, payload_json FROM run_events "
        "WHERE event_type = 'core_live_projected' ORDER BY workflow_run_id, sequence_number"
    )
    for event_id, run_id, payload in rows:
        total += 1
        stable = _stable(payload)
        if last.get(run_id) == stable:
            redundant.append(int(event_id))
        else:
            last[run_id] = stable
    return redundant, total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remove redundant live-status run events.")
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true", help="delete redundant events (default: dry run)")
    parser.add_argument("--backup", type=Path, help="write an online backup here before applying")
    parser.add_argument("--vacuum", action="store_true", help="VACUUM after applying to return the space")
    args = parser.parse_args(argv)
    if args.vacuum and not args.apply:
        parser.error("--vacuum requires --apply")
    path = args.database.expanduser().resolve()
    if not path.is_file():
        parser.error(f"database not found: {path}")

    lock = None
    if args.apply:
        lock = (path.parent / f"{path.name}.dispatcher.lock").open("a+")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("refused: the API's experiment worker holds the dispatcher lock; stop the API first", file=sys.stderr)
            return 2

    connection = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        redundant, total = redundant_event_ids(connection)
        report = {"database": str(path), "live_events": total, "redundant": len(redundant),
                  "kept": total - len(redundant), "applied": False}
        if args.apply and redundant:
            if args.backup is not None:
                target = sqlite3.connect(str(args.backup.expanduser()))
                connection.backup(target)
                target.close()
                report["backup"] = str(args.backup.expanduser())
            ddl = {name: sql for name, sql in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND name IN (?, ?)", _TRIGGERS)}
            if set(ddl) != set(_TRIGGERS):
                print(f"refused: expected triggers missing: {sorted(set(_TRIGGERS) - set(ddl))}", file=sys.stderr)
                return 3
            connection.execute("BEGIN IMMEDIATE")
            try:
                for name in _TRIGGERS:
                    connection.execute(f'DROP TRIGGER "{name}"')
                connection.execute("CREATE TEMP TABLE _drop_ids(id INTEGER PRIMARY KEY)")
                connection.executemany("INSERT INTO _drop_ids(id) VALUES (?)", ((i,) for i in redundant))
                deleted = connection.execute(
                    "DELETE FROM run_events WHERE id IN (SELECT id FROM _drop_ids)").rowcount
                connection.execute("DROP TABLE _drop_ids")
                for name in _TRIGGERS:
                    connection.execute(ddl[name])
                if deleted != len(redundant):
                    raise RuntimeError(f"deleted {deleted}, expected {len(redundant)}")
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            report["applied"] = True
            report["deleted"] = deleted
        if args.vacuum:
            # Schema triggers call BMS SQL functions; VACUUM re-parses them.
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))
            from migrations.sqlite_sha256 import register_sqlite_sha256

            register_sqlite_sha256(connection)
            connection.execute("VACUUM")
            report["vacuumed"] = True
        print(json.dumps(report, sort_keys=True))
        return 0
    finally:
        connection.close()
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
