from __future__ import annotations

import ast
import json
import re
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


def test_removed_implementation_paths_are_absent() -> None:
    assert [
        relative for relative in REMOVED_PATHS if (REPO_ROOT / relative).exists()
    ] == []


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
