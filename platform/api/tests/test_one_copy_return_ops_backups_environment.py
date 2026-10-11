"""GTK callback bodies and AI-environment close against tiny owned fixtures."""
import ast
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import manage_ai_environment as env
from services import sqlite_backup

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("filename,callback", [("biomodstack_tray.py", "backup_database"), ("biomodstack_panel.py", "_on_backup_db")])
def test_gtk_callbacks_take_consistent_wal_snapshot(tmp_path, filename, callback):
    source = tmp_path / "live.db"
    writer = sqlite3.connect(source)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE observations(id INTEGER PRIMARY KEY, value TEXT)")
    writer.execute("INSERT INTO observations VALUES (1, 'committed WAL')")
    # An unrelated legacy FK violation must not block an intentional UI backup.
    writer.execute("CREATE TABLE legacy(parent INTEGER REFERENCES observations(id))")
    writer.execute("INSERT INTO legacy VALUES (99)")
    writer.commit()
    writer.execute("INSERT INTO observations VALUES (2, 'uncommitted')")
    node = next(n for n in ast.walk(ast.parse((ROOT / filename).read_text()))
                if isinstance(n, ast.FunctionDef) and n.name == callback)
    notices = []
    namespace = {"DB_PATH": source, "PROJECT_ROOT": tmp_path, "datetime": datetime,
                 "show_notification": lambda *args: notices.append(args)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, "exec"), namespace)
    if callback == "_on_backup_db":
        namespace[callback](None, None)
    else:
        namespace[callback]()
    assert notices[0][0] == "Backup Complete"
    backup, = (tmp_path / "backups").glob("*.db")
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT * FROM observations").fetchall() == [(1, "committed WAL")]
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall()
    writer.rollback()
    writer.close()
    assert not list(backup.parent.glob(".*.tmp-*"))


def test_sqlite_snapshot_failure_keeps_existing_backup(tmp_path, monkeypatch):
    source, destination = tmp_path / "source.db", tmp_path / "backup.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE t(x)")
    destination.write_bytes(b"intentional old backup")
    def failure(*args):
        raise RuntimeError("copy fault")
    monkeypatch.setattr(sqlite_backup, "_copy_sqlite_online", failure)
    with pytest.raises(RuntimeError, match="copy fault"):
        sqlite_backup.copy_sqlite_database(source, destination)
    assert destination.read_bytes() == b"intentional old backup"
    assert not list(tmp_path.glob(".*.tmp-*"))


@pytest.mark.parametrize("discard,stop_code", [(True, 0), (False, 0), (True, 1)])
def test_environment_close_preserves_science_and_only_reaps_joined_disposables(tmp_path, monkeypatch, discard, stop_code):
    monkeypatch.setenv("BMS_AI_STATE_ROOT", str(tmp_path / "receipts"))
    state = tmp_path / "runtime"
    state.mkdir()
    paths = ["biomodstack.db", "results/design.pdb", "backups/intentional.db", "work/attempt/cache", "inputs/source.fa", "weights/legacy.pt", "msa_cache/old.a3m", ".disposable/tmp/transient"]
    for relative in paths:
        path = state / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode())
    receipt = dict(id="owned", status="created" if discard else "promoted", branch="ai/owned",
                   state_root=str(state), worktree=str(tmp_path / "absent-worktree"),
                   disposable_scratch=str(state / ".disposable"))
    env.save_receipt(receipt)
    calls = []
    def systemctl(*args, **kwargs):
        calls.append(args)
        assert (state / ".disposable/tmp/transient").exists()
        return SimpleNamespace(returncode=stop_code, stdout="")
    monkeypatch.setattr(env, "systemctl", systemctl)
    monkeypatch.setattr(env, "git", lambda *a, **k: "not-empty")
    result = env.command_close(SimpleNamespace(id="owned", repo=str(tmp_path), discard=discard))
    assert result["status"] == "closed"
    assert calls[0][0] == "stop"
    for relative in paths[:-1]:
        assert (state / relative).read_bytes() == relative.encode()
    assert (state / ".disposable").exists() == (stop_code != 0)
    env.cleanup_disposable_scratch(dict(receipt, disposable_scratch="unowned"))


def test_environment_start_uses_shared_dependencies_and_private_state(tmp_path, monkeypatch):
    worktree = tmp_path / "checkout"
    (worktree / "platform/frontend/node_modules").mkdir(parents=True)
    state = tmp_path / "state"
    shared = tmp_path / "shared-weights"
    shared.mkdir()
    (shared / "selected.pt").write_bytes(b"one body")
    receipt = dict(id="owned", status="created", worktree=str(worktree), state_root=str(state),
                   shared_weights_root=str(shared), disposable_scratch=str(state / ".disposable"),
                   api_port=23400, web_port=23401)
    monkeypatch.setattr(env, "load_receipt", lambda _: receipt)
    monkeypatch.setattr(env, "save_receipt", lambda _: None)
    monkeypatch.setattr(env, "systemctl", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(env, "wait_http", lambda *a: None)
    commands = []
    monkeypatch.setattr(env, "run", lambda command, **kwargs: commands.append(command))
    env.command_start(SimpleNamespace(id="owned"))
    assert f"--setenv=BMS_WEIGHTS={shared}" in commands[0]
    assert f"--setenv=BMS_DB_PATH={state / 'biomodstack.db'}" in commands[0]
    assert f"--setenv=TMPDIR={state / '.disposable/tmp'}" in commands[0]
    assert not (state / "weights").exists()
    assert (shared / "selected.pt").read_bytes() == b"one body"
