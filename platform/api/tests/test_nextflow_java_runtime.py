from __future__ import annotations

from pathlib import Path

from services import nextflow


def _fake_jdk(root: Path) -> Path:
    (root / "bin").mkdir(parents=True)
    (root / "lib").mkdir(parents=True)
    java = root / "bin" / "java"
    java.write_text("#!/bin/sh\nprintf 'openjdk version \\\"17.0.18\\\" 2026-01-20\\n' >&2\n", encoding="utf-8")
    java.chmod(0o755)
    helper = root / "lib" / "jspawnhelper"
    helper.write_text("17.0.18+8\n", encoding="utf-8")
    helper.chmod(0o755)
    return root


def test_resolve_nextflow_java_env_prefers_explicit_override(monkeypatch, tmp_path):
    override = _fake_jdk(tmp_path / "override-jdk")
    fallback = _fake_jdk(tmp_path / "fallback-jdk")
    monkeypatch.setenv("BMS_NEXTFLOW_JAVA_HOME", str(override))
    monkeypatch.setattr(nextflow, "DEFAULT_NEXTFLOW_JAVA_HOME", fallback)

    env, notes = nextflow.resolve_nextflow_java_env({"PATH": "/usr/bin"})

    assert env["JAVA_HOME"] == str(override)
    assert env["PATH"].split(":", 1)[0] == str(override / "bin")
    assert any("BMS_NEXTFLOW_JAVA_HOME" in note for note in notes)


def test_resolve_nextflow_java_env_uses_temurin_default_when_present(monkeypatch, tmp_path):
    default = _fake_jdk(tmp_path / "temurin-17")
    monkeypatch.delenv("BMS_NEXTFLOW_JAVA_HOME", raising=False)
    monkeypatch.setattr(nextflow, "DEFAULT_NEXTFLOW_JAVA_HOME", default)

    env, notes = nextflow.resolve_nextflow_java_env({"PATH": "/usr/bin"})

    assert env["JAVA_HOME"] == str(default)
    assert env["PATH"].startswith(f"{default / 'bin'}:")
    assert any("default" in note.lower() for note in notes)


def test_java_helper_mismatch_is_reported(monkeypatch, tmp_path):
    jdk = _fake_jdk(tmp_path / "bad-jdk")
    (jdk / "lib" / "jspawnhelper").write_text("17.0.19+10\n", encoding="utf-8")

    class Completed:
        returncode = 0
        stdout = 'openjdk version "17.0.18" 2026-01-20\n'

    monkeypatch.setattr(nextflow.subprocess, "run", lambda *args, **kwargs: Completed())

    ok, message = nextflow.preflight_nextflow_java({"JAVA_HOME": str(jdk), "PATH": f"{jdk / 'bin'}:/usr/bin"})

    assert not ok
    assert "jspawnhelper" in message
    assert "17.0.18" in message
    assert "17.0.19" in message
