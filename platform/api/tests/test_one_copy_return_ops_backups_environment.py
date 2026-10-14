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
    namespace = {"DB_PATH": source, "PROJECT_ROOT": tmp_path, "API_ROOT": ROOT / "platform/api", "datetime": datetime,
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


@pytest.mark.parametrize("filename,callback", [("biomodstack_tray.py", "backup_database"), ("biomodstack_panel.py", "_on_backup_db")])
def test_desktop_stdlib_callback_import_and_wal(tmp_path, filename, callback):
    import subprocess
    # A real isolated desktop-like Python, without site-packages or injected
    # services. Execute the actual callback AST; GUI display is the only omission.
    program = r"""
import ast, sqlite3, sys
from pathlib import Path
from datetime import datetime
root, scratch, filename, callback = map(str, sys.argv[1:])
root, scratch = Path(root), Path(scratch)
source = scratch / 'live.db'
writer = sqlite3.connect(source)
writer.execute('PRAGMA journal_mode=WAL')
writer.execute('CREATE TABLE t(value)')
writer.execute("INSERT INTO t VALUES ('committed')")
writer.commit()
writer.execute("INSERT INTO t VALUES ('pending')")
node = next(n for n in ast.walk(ast.parse((root / filename).read_text()))
            if isinstance(n, ast.FunctionDef) and n.name == callback)
notices = []
namespace = dict(DB_PATH=source, PROJECT_ROOT=scratch, API_ROOT=root/'platform/api',
                 datetime=datetime, show_notification=lambda *a: notices.append(a))
exec(compile(ast.Module(body=[node], type_ignores=[]), filename, 'exec'), namespace)
namespace[callback](*([None, None] if callback == '_on_backup_db' else []))
assert notices[0][0] == 'Backup Complete', notices
backup, = (scratch/'backups').glob('*.db')
with sqlite3.connect(backup) as reader:
    assert reader.execute('SELECT * FROM t').fetchall() == [('committed',)]
    assert reader.execute('PRAGMA integrity_check').fetchone() == ('ok',)
assert 'services' not in sys.modules
assert not any(n.startswith(('sqlalchemy', 'numpy', 'torch', 'Bio.')) for n in sys.modules)
writer.rollback()
writer.close()
print('stdlib leaf imported; committed WAL included; pending row excluded; no services package')
"""
    result = subprocess.run(['/usr/bin/python3', '-I', '-S', '-c', program,
                             str(ROOT), str(tmp_path), filename, callback],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


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


@pytest.mark.parametrize("existing", [False, True])
def test_failed_start_claims_only_scratch_it_created(tmp_path, monkeypatch, existing):
    monkeypatch.setenv("BMS_AI_STATE_ROOT", str(tmp_path / "receipts"))
    worktree = tmp_path / "checkout"
    (worktree / "platform/frontend/node_modules").mkdir(parents=True)
    state = tmp_path / "state"
    scratch = state / ".disposable"
    if existing:
        scratch.mkdir(parents=True)
        (scratch / "unknown").write_text("legacy custody")
    receipt = dict(id="owned", status="created", worktree=str(worktree), state_root=str(state),
                   api_port=23400, web_port=23401)
    env.save_receipt(receipt)
    monkeypatch.setattr(env, "systemctl", lambda *a, **k: SimpleNamespace(returncode=0))
    def failed_launch(*a, **k):
        raise RuntimeError("launch failed")
    monkeypatch.setattr(env, "run", failed_launch)
    with pytest.raises(RuntimeError, match="launch failed"):
        env.command_start(SimpleNamespace(id="owned"))
    persisted = env.load_receipt("owned")
    assert bool(persisted.get("disposable_scratch")) == (not existing)
    env.cleanup_disposable_scratch(persisted)
    assert scratch.exists() == existing


def test_close_joins_writer_and_serializes_restart(tmp_path, monkeypatch):
    import threading
    monkeypatch.setenv("BMS_AI_STATE_ROOT", str(tmp_path / "receipts"))
    state = tmp_path / "runtime"
    scratch = state / ".disposable"
    scratch.mkdir(parents=True)
    receipt = dict(id="owned", status="created", branch="ai/owned", state_root=str(state),
                   worktree=str(tmp_path / "absent"), disposable_scratch=str(scratch))
    env.save_receipt(receipt)
    stopped, release, started = threading.Event(), threading.Event(), threading.Event()
    errors = []
    def writer():
        assert release.wait(10)
        (scratch / "last-write").write_text("joined")
    worker = threading.Thread(target=writer)
    worker.start()
    def systemctl(*args, **kwargs):
        if args[0] == "stop":
            stopped.set()
            worker.join(10)
            assert not worker.is_alive()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(env, "systemctl", systemctl)
    monkeypatch.setattr(env, "git", lambda *a, **k: "nonempty")
    def start_owned(args):
        assert not scratch.exists()
        started.set()
    monkeypatch.setattr(env, "_start_owned", start_owned)
    def close():
        try:
            env.command_close(SimpleNamespace(id="owned", repo=str(tmp_path), discard=True))
        except BaseException as exc:
            errors.append(exc)
    closer = threading.Thread(target=close)
    closer.start()
    assert stopped.wait(10)
    starter = threading.Thread(target=env.command_start, args=(SimpleNamespace(id="owned"),))
    starter.start()
    assert not started.wait(0.1)
    assert scratch.exists() and worker.is_alive()
    release.set()
    closer.join(10)
    starter.join(10)
    assert not errors and not closer.is_alive() and not starter.is_alive()
    assert started.is_set() and not scratch.exists()


def test_cleanup_failure_is_not_close_policy(tmp_path, monkeypatch):
    scratch = tmp_path / ".disposable"
    scratch.mkdir()
    monkeypatch.setattr(env.shutil, "rmtree", lambda *a: (_ for _ in ()).throw(OSError("busy")))
    env.cleanup_disposable_scratch(dict(state_root=str(tmp_path), disposable_scratch=str(scratch)))
    assert scratch.exists()


@pytest.mark.parametrize("preexisting_scratch", [False, True])
def test_environment_create_records_existing_shared_selection(tmp_path, monkeypatch, preexisting_scratch):
    monkeypatch.setenv("BMS_AI_STATE_ROOT", str(tmp_path / "receipts"))
    monkeypatch.setenv("BMS_AI_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setenv("BMS_WEIGHTS", str(tmp_path / "shared-weights"))
    monkeypatch.setattr(env, "assert_canonical_test", lambda _: "a" * 40)
    monkeypatch.setattr(env, "allocate_port_pair", lambda: (24000, 24001))
    def git(repo, *args, **kwargs):
        if args[0] == "ls-remote":
            return ""
        return "not-empty"
    monkeypatch.setattr(env, "git", git)
    scratch = tmp_path / "receipts/runtime/owned/.disposable"
    if preexisting_scratch:
        scratch.mkdir(parents=True)
        (scratch / "unknown").write_text("prior creator")
    receipt = env.command_create(SimpleNamespace(id="owned", repo=str(tmp_path / "repo")))
    persisted = env.load_receipt("owned")
    assert persisted == receipt
    assert persisted["shared_weights_root"] == str(tmp_path / "shared-weights")
    if preexisting_scratch:
        assert "disposable_scratch" not in persisted
        env.cleanup_disposable_scratch(persisted)
        assert (scratch / "unknown").read_text() == "prior creator"
    else:
        assert persisted["disposable_scratch"] == str(scratch)
        assert scratch.is_dir()
    assert not (Path(persisted["state_root"]) / "weights").exists()
