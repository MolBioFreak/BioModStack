from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
NORMALIZER_PATH = REPO_ROOT / "scripts" / "normalize_generated_ownership.py"


def load_normalizer():
    assert NORMALIZER_PATH.exists(), "generated-ownership normalizer is missing"
    spec = importlib.util.spec_from_file_location("normalize_generated_ownership", NORMALIZER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_ownership_normalizer_is_scoped_and_reports_nested_drift(tmp_path: Path) -> None:
    normalizer = load_normalizer()
    generated = tmp_path / "dist"
    nested = generated / "assets"
    nested.mkdir(parents=True)
    artifact = nested / "bundle.js"
    artifact.write_text("generated", encoding="utf-8")

    mismatches = normalizer.normalize_paths(
        [generated],
        uid=os.getuid() + 1,
        gid=os.getgid() + 1,
        check_only=True,
    )

    assert mismatches == [generated, nested, artifact]
    assert all(path == generated or generated in path.parents for path in mismatches)
    assert normalizer.DEFAULT_PATHS
    assert all(path == normalizer.REPO_ROOT or normalizer.REPO_ROOT in path.parents for path in normalizer.DEFAULT_PATHS)


def test_generated_ownership_normalizer_does_not_follow_directory_symlinks(tmp_path: Path) -> None:
    normalizer = load_normalizer()
    generated = tmp_path / "dist"
    external = tmp_path / "external"
    generated.mkdir()
    external.mkdir()
    secret = external / "do-not-touch"
    secret.write_text("external", encoding="utf-8")
    link = generated / "external-link"
    link.symlink_to(external, target_is_directory=True)

    entries = list(normalizer._entries(generated))

    assert generated in entries
    assert link in entries
    assert secret not in entries


def test_privileged_requested_paths_are_restricted_to_generated_allowlist(tmp_path: Path, monkeypatch) -> None:
    normalizer = load_normalizer()
    allowed_child = normalizer.DEFAULT_PATHS[0] / "nested"
    assert normalizer.validate_requested_paths([allowed_child]) == [allowed_child]

    with pytest.raises(ValueError, match="outside the generated-output allowlist"):
        normalizer.validate_requested_paths([tmp_path / "external"])

    traversal = normalizer.DEFAULT_PATHS[0] / ".." / "source-file"
    with pytest.raises(ValueError, match="outside the generated-output allowlist"):
        normalizer.validate_requested_paths([traversal])

    repo = tmp_path / "repo"
    generated = repo / "generated"
    external = tmp_path / "external"
    generated.mkdir(parents=True)
    external.mkdir()
    (generated / "escape").symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(normalizer, "REPO_ROOT", repo)
    monkeypatch.setattr(normalizer, "DEFAULT_PATHS", (generated,))
    with pytest.raises(ValueError, match="outside the generated-output allowlist"):
        normalizer.validate_requested_paths([generated / "escape" / "nested"])
