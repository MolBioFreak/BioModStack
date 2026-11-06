"""Exercise the installer's actual dependency-copy block without root."""
from pathlib import Path
from types import SimpleNamespace
import importlib.metadata as metadata
import sys


REPO = Path(__file__).resolve().parents[3]


def test_distribution_record_paths_cannot_escape_staging(tmp_path, monkeypatch):
    source = tmp_path / "locked-module.py"
    source.write_text("# synthetic package source\n")
    root = tmp_path / "install"
    target = root / "a" / "b" / "site-packages"
    escaped = root / "outside.py"
    distribution = SimpleNamespace(
        requires=[],
        files=[Path("pure/module.py"), Path("../../../bin/uvicorn"), escaped],
        locate_file=lambda _: source,
    )
    monkeypatch.setattr(metadata, "distribution", lambda _: distribution)
    monkeypatch.setattr(sys, "argv", ["installer-copy-test", str(target)])
    installer = (REPO / "config/share-funnel/install-share-server.sh").read_text()
    block = installer.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    exec(compile(block, "share-installer-dependency-copy", "exec"), {})
    assert (target / "pure/module.py").read_bytes() == source.read_bytes()
    assert not (root / "bin").exists()
    assert not escaped.exists()
    assert all(p.is_relative_to(target) for p in root.rglob("*") if p.is_file())
