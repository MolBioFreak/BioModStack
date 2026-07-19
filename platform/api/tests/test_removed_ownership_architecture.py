from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for root in (API_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _joined(*parts: str) -> str:
    return "".join(parts)


REMOVED_PATHS = tuple(
    _joined(*parts)
    for parts in (
        ("platform/api/routers/", "as", "say_analytics.py"),
        ("platform/api/services/", "as", "say_analytical_", "st", "ore.py"),
        ("platform/api/services/", "as", "say_chrom_persistence.py"),
        ("platform/api/services/", "as", "say_tool_integrations.py"),
        ("platform/api/services/", "db_", "service.py"),
        ("platform/api/services/", "sta", "ts_tools.py"),
        ("docker/install_", "as", "say_r_packages.R"),
        ("platform/frontend/src/components/", "As", "sayAnalytics.tsx"),
        ("platform/frontend/src/components/", "Sta", "tsToolsControlPanel.tsx"),
        ("platform/frontend/src/components/", "Db", "ServiceControlPanel.tsx"),
        ("platform/frontend/src/components/", "as", "sayPersistence.ts"),
        ("platform/frontend/src/components/", "as", "say"),
        ("platform/frontend/src/components/", "hp", "lc"),
        ("platform/frontend/src/components/", "q", "pcr"),
        ("platform/frontend/src/components/", "statistics"),
    )
)
REMOVED_RULES = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"q(?:[-_.+'\"\s]*)p" r"cr",
        r"del" r"ta[-_ ]?(?:c" r"q|c" r"t)",
        r"quant" r"studio",
        r"step" r"one",
        r"hp" r"lc",
        r"chroma" r"togra",
        r"em" r"power",
        r"\bplate[-_ ]?" r"maps?\b",
        r"as" r"say[-_ ]?analytics",
        r"analytical[-_ ]?" r"store",
        r"bms_analy" r"tical_data",
        r"/api/as" r"say-analytics",
        r"/as" r"say(?:[/?#\"']|$)",
        r"sta" r"ts[_-]?tools",
        r"sta" r"ts[-_ ]?tool[-_ ]?kit",
        r"as" r"say_db",
        r"BMS_FEATURE_STA" r"TS_TOOLS",
        r"BMS_FEATURE_AS" r"SAY_DB",
        r"BMS_ANALY" r"TICAL_",
        r"bms-sta" r"ts-tools",
        r"sta" r"ts-tools-runtime",
        r"bms-db-" r"service",
        r"/api/system/db-" r"service",
        r"db[-_ ]?" r"service",
        r"bms_db_" r"service_data",
        r"554" r"32",
    )
)
GENERATED_PARTS = frozenset(
    {
        ".artifacts",
        ".git",
        ".pytest_cache",
        ".test-dist",
        ".venv",
        "build",
        "dist",
        "node_modules",
    }
)
HISTORICAL_BANNER = "> **Historical / superseded:**"
FROZEN_STATIC_EVIDENCE_PATHS = frozenset(
    {"docs/audits/bioxp-phase0-baseline-2026-07-17.json"}
)


def _matches(text: str) -> bool:
    return any(pattern.search(text) for pattern in REMOVED_RULES)


def _git_inventory_files() -> list[Path]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        check=True,
        capture_output=True,
    )
    return sorted(
        REPO_ROOT / relative
        for relative in result.stdout.decode("utf-8", errors="surrogateescape").split(
            "\0"
        )
        if relative
    )


def _read_utf8(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    return None if "\0" in text else text


def _is_generated(path: Path) -> bool:
    relative = path.relative_to(REPO_ROOT)
    return bool({part.casefold() for part in relative.parts[:-1]} & GENERATED_PARTS)


def _is_historical(path: Path) -> bool:
    relative = path.relative_to(REPO_ROOT)
    return "archive" in relative.parts or relative.parts[:2] == ("docs", "reports")


def _is_static_evidence(path: Path) -> bool:
    relative = path.relative_to(REPO_ROOT)
    return (
        relative.parts[:2] == ("docs", "evidence")
        or relative.as_posix() in FROZEN_STATIC_EVIDENCE_PATHS
    )


def _is_frozen_lock(path: Path) -> bool:
    name = path.name.casefold()
    return name.endswith(".lock") or name in {
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    }


def _active_text_files() -> list[Path]:
    return [
        path
        for path in _git_inventory_files()
        if path.is_file()
        and not _is_generated(path)
        and not _is_historical(path)
        and not _is_static_evidence(path)
        and not _is_frozen_lock(path)
        and _read_utf8(path) is not None
    ]


def _historical_text_files() -> list[Path]:
    return [
        path
        for path in _git_inventory_files()
        if path.is_file()
        and not _is_generated(path)
        and _is_historical(path)
        and _read_utf8(path) is not None
    ]


def test_removed_implementation_paths_are_absent() -> None:
    assert [
        relative for relative in REMOVED_PATHS if (REPO_ROOT / relative).exists()
    ] == []


def test_active_source_paths_and_text_have_no_removed_ownership() -> None:
    violations: list[str] = []
    for path in _active_text_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        text = _read_utf8(path)
        assert text is not None
        if _matches(relative):
            violations.append(f"{relative}:path")
        if _matches(text):
            violations.append(f"{relative}:text")
    assert violations == []


def test_inventory_includes_tests_and_arbitrary_utf8_extensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    path = tmp_path / "platform" / "api" / "tests" / "runtime-guide.rst"
    path.parent.mkdir(parents=True)
    path.write_text(_joined("q", "P", "CR", " runtime guide\n"), encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)

    assert path in _active_text_files()
    with pytest.raises(AssertionError):
        test_active_source_paths_and_text_have_no_removed_ownership()


@pytest.mark.parametrize(
    "relative",
    (
        "README.rst",
        "platform/api/tests/contract.py",
        "platform/api/contract.yaml",
        ".github/workflows/contract.yml",
        "platform/frontend/src/contract.ts",
        "platform/desktop-electron/src/contract.ts",
        "docker/Dockerfile.contract",
    ),
)
@pytest.mark.parametrize(
    "payload",
    (
        _joined("q", "P", "CR"),
        _joined("q", "\n", "P", "CR"),
        _joined("Sta", "tsTool", "kit"),
        _joined("Sta", "ts", "Tools"),
        _joined("As", "say", "Analytics"),
        _joined("HP", "LC"),
        _joined("Chroma", "togram"),
        _joined("Plate", "Map"),
        _joined("Db", "Service"),
    ),
)
def test_full_git_inventory_rejects_removed_matrix(
    relative: str,
    payload: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"Active ownership: {payload}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", relative], check=True)
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)

    with pytest.raises(AssertionError):
        test_active_source_paths_and_text_have_no_removed_ownership()


def test_historical_sources_with_removed_vocabulary_are_bannered() -> None:
    violations: list[str] = []
    for path in _historical_text_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        text = _read_utf8(path)
        assert text is not None
        if (
            _matches(relative) or _matches(text)
        ) and HISTORICAL_BANNER not in "\n".join(text.splitlines()[:6]):
            violations.append(relative)
    assert violations == []


def test_core_compose_has_no_removed_services_or_analytical_volume() -> None:
    compose = yaml.safe_load(
        (REPO_ROOT / "compose.core-runtime.yml").read_text(encoding="utf-8")
    )
    services = set(compose.get("services") or {})
    assert services.isdisjoint(
        {
            _joined("bms-", "db"),
            _joined("bms-", "sta", "ts-tools"),
        }
    )
    assert _joined("bms_db_", "service_data") not in (compose.get("volumes") or {})


def _project_dependencies() -> set[str]:
    pyproject_text = (API_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project_section = pyproject_text.split("[project]", 1)[1].split("\n[", 1)[0]
    dependency_match = re.search(
        r"^dependencies\s*=\s*(\[.*?^\])",
        project_section,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert dependency_match is not None
    return {
        re.split(r"[<>=!~;\s\[]", dependency, maxsplit=1)[0].lower()
        for dependency in ast.literal_eval(dependency_match.group(1))
    }


def test_api_has_no_removed_domain_only_direct_dependencies() -> None:
    removed = {
        "asyncpg",
        _joined("moc", "ca2"),
        "pydoe3",
        "statsmodels",
        "scikit-learn",
        "bofire",
        _joined("q", "pcr"),
        _joined("qs", "lib"),
        "xlrd",
    }
    dependencies = _project_dependencies()
    assert dependencies.isdisjoint(removed)
    assert "pillow" in dependencies


def test_removed_frontend_direct_dependencies_are_absent() -> None:
    manifest = json.loads(
        (REPO_ROOT / "platform/frontend/package.json").read_text(encoding="utf-8")
    )
    dependencies = set(manifest.get("dependencies", {})) | set(
        manifest.get("devDependencies", {})
    )
    assert dependencies.isdisjoint({"d3", "papaparse", "@types/d3", "@types/papaparse"})


def test_openapi_has_no_removed_control_routes() -> None:
    from main import app

    paths = set(app.openapi()["paths"])
    removed_prefixes = (
        _joined("/api/", "as", "say-analytics"),
        _joined("/api/system/", "sta", "ts-tools"),
        _joined("/api/system/", "db-", "service"),
    )
    assert not any(path.startswith(removed_prefixes) for path in paths)
