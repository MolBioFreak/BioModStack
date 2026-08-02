#!/usr/bin/env python3
"""Fail-closed Phase 7 FrustraMPNN retirement source scanner.

The scanner inventories active production files, reports every violation as
``path:line: rule: evidence``, and intentionally does not scan historical DB
schema/read projections or test fixtures.  Tests are covered only by the exact
allowlist in ``TEST_ALLOWLIST`` so the retirement guard itself can contain the
forbidden spellings it detects.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    rule: str
    evidence: str

    def render(self, root: Path) -> str:
        try:
            relative = self.path.relative_to(root)
        except ValueError:
            relative = self.path
        return f"{relative}:{self.line}: {self.rule}: {self.evidence.strip()}"


PRODUCTION_ROOTS = (
    "modules",
    "workflows",
    "platform/api/routers",
    "platform/api/services",
    "platform/frontend/src",
    "schemas",
    "scripts",
)
PRODUCTION_FILES = ("nextflow.config",)
EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".hermes",
        ".venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "artifacts",
        "fixtures",
        "probes",
    }
)
SCRIPT_ALLOWLIST = frozenset({"scripts/check_frustrampnn_retirement.py"})
TEST_ALLOWLIST = frozenset({"platform/api/tests/test_frustrampnn_retirement.py"})
TEXT_SUFFIXES = frozenset(
    {"", ".py", ".nf", ".config", ".groovy", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml", ".toml", ".md", ".sh"}
)

# Lexical retirement rules.  The names are intentionally stable operator output.
GENERAL_RULES = (
    (
        "legacy_nextflow_process",
        re.compile(r"\b(?:process\s+)?(?:FrustrampnnQC|AggregateFrustrationReports)\b"),
    ),
    (
        "legacy_batch_execution",
        re.compile(
            r"\b(?:run_batch_frustrampnn|maybe_trigger_batch_frustrampnn|"
            r"_is_canonical_protein_design_batch|run_frustrampnn_batch)\b"
        ),
    ),
    ("retired_upload_route", re.compile(r"/api/frustrampnn/analyze(?:\b|[/?'\"]|$)")),
    ("stale_container_selector", re.compile(r"(?:^|[/'\"])containers/frustrampnn\.sif\b")),
    ("loose_csv_parser", re.compile(r"\bdef\s+(?:parse_frustration_csv|ingest_frustration_data)\s*\(")),
    ("loose_csv_discovery", re.compile(r"(?:glob|rglob)\s*\(\s*['\"]\*_frustration\.csv['\"]\s*\)")),
    (
        "historical_projection_write",
        re.compile(
            r"\.frustration_(?:high_count|min_count|pct_high|residues|csv_path)\s*="
        ),
    ),
    ("fail_open_error_strategy", re.compile(r"errorStrategy\s+['\"]ignore['\"]")),
    (
        "degraded_or_optional_success",
        re.compile(r"\b(?:completed_degraded|requested_but_failed|degraded_success)\b"),
    ),
    (
        "placeholder_scoring",
        re.compile(r"(?i)(?:placeholder[^\n]{0,80}frustra|frustra[^\n]{0,80}placeholder)"),
    ),
    (
        "basename_identity_join",
        re.compile(
            r"(?i)(?:candidate_id[^\n]{0,100}(?:\.baseName|basename\s*\()|"
            r"(?:\.baseName|basename\s*\()[^\n]{0,100}candidate_id)"
        ),
    ),
)

FRONTEND_RULES = (
    ("frontend_native_profile_parser", re.compile(r"\bnative_profile\b")),
    ("frontend_raw_score_parser", re.compile(r"\bfrustration_pred\b")),
    ("frontend_papa_parser", re.compile(r"\bPapa\.parse\b")),
)

CM_POLICY_PREFIXES = (
    "platform/api/services/conformational_mapping/",
    "platform/frontend/src/components/conformationalMapping/",
    "schemas/conformational_mapping/",
)
CM_POLICY_FILES = frozenset(
    {
        "scripts/run_conformational_mapping_analysis_plane.py",
        "platform/frontend/src/components/FrustraMpnnResultsViewer.tsx",
    }
)
POLICY_RULE = re.compile(r"(?<![\w.])(?:-1\.0|0\.58)(?!\d)|frustrampnn_class_v1")
DIRECT_PROCESS_RULE = re.compile(r"\bsubprocess\.(?:run|Popen|call|check_call|check_output)\s*\(")
MODEL_EXECUTION_TOKEN = re.compile(r"(?i)\bfrustrampnn\b")
CM_ADAPTER_ID_PATH = "platform/api/services/conformational_mapping/frustrampnn_adapter.py"
CM_ADAPTER_ID_LINE = re.compile(
    r'^CM_THRESHOLD_POLICY_ADAPTER_ID\s*=\s*["\']frustrampnn_class_v1["\']\s*$'
)
CM_SCHEMA_ID_PATH = "schemas/conformational_mapping/cm_frustration_landscape_v1.schema.json"


def _is_versioned_cm_adapter_identity(relative: str, line: str) -> bool:
    if relative == CM_ADAPTER_ID_PATH and CM_ADAPTER_ID_LINE.fullmatch(line.strip()):
        return True
    return (
        relative == CM_SCHEMA_ID_PATH
        and '"threshold_policy_id"' in line
        and '"const"' in line
        and '"frustrampnn_class_v1"' in line
        and not re.search(r"(?<![\w.])(?:-1\.0|0\.58)(?!\d)", line)
    )


class _DirectProcessVisitor(ast.NodeVisitor):
    def __init__(self, *, source: str, relative: str) -> None:
        self.source = source
        self.relative = relative
        self.functions: list[str] = []
        self.findings: list[tuple[int, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        is_subprocess = (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "subprocess"
            and function.attr in {"run", "Popen", "call", "check_call", "check_output"}
        )
        if is_subprocess:
            owner = self.functions[-1] if self.functions else ""
            if self.relative.endswith("/services/frustrampnn/runtime.py") and owner in {
                "container_sha256",
                "execute_frustrampnn",
            }:
                pass
            else:
                segment = ast.get_source_segment(self.source, node) or ""
                if MODEL_EXECUTION_TOKEN.search(segment) or "frustrampnn" in owner.lower():
                    self.findings.append((node.lineno, segment.splitlines()[0]))
        self.generic_visit(node)


def _direct_process_findings(path: Path, relative: str, text: str) -> list[tuple[int, str]]:
    if path.suffix == ".py":
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            return [
                (line_number, line)
                for line_number, line in enumerate(text.splitlines(), start=1)
                if DIRECT_PROCESS_RULE.search(line) and MODEL_EXECUTION_TOKEN.search(line)
            ]
        visitor = _DirectProcessVisitor(source=text, relative=relative)
        visitor.visit(tree)
        return visitor.findings
    return [
        (line_number, line)
        for line_number, line in enumerate(text.splitlines(), start=1)
        if DIRECT_PROCESS_RULE.search(line) and MODEL_EXECUTION_TOKEN.search(line)
    ]


def _active_files(root: Path) -> Iterator[Path]:
    seen: set[Path] = set()
    for relative_root in PRODUCTION_ROOTS:
        base = root / relative_root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root)
            if relative.as_posix() in SCRIPT_ALLOWLIST:
                continue
            if any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if path not in seen:
                seen.add(path)
                yield path
    for relative_file in PRODUCTION_FILES:
        path = root / relative_file
        if path.is_file() and not path.is_symlink() and path not in seen:
            yield path


def _decoded_lines(path: Path) -> list[str]:
    payload = path.read_bytes()
    if b"\0" in payload:
        return []
    try:
        return payload.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError:
        return []


def scan(root: Path) -> list[Violation]:
    root = root.resolve()
    violations: list[Violation] = []
    for path in _active_files(root):
        relative = path.relative_to(root).as_posix()
        lines = _decoded_lines(path)
        text = "\n".join(lines)
        for line_number, line in enumerate(lines, start=1):
            for rule, pattern in GENERAL_RULES:
                if pattern.search(line):
                    violations.append(Violation(path, line_number, rule, line))
            if relative.startswith("platform/frontend/src/"):
                for rule, pattern in FRONTEND_RULES:
                    if pattern.search(line):
                        violations.append(Violation(path, line_number, rule, line))
            is_cm_policy_surface = relative in CM_POLICY_FILES or relative.startswith(
                CM_POLICY_PREFIXES
            )
            if (
                is_cm_policy_surface
                and POLICY_RULE.search(line)
                and not _is_versioned_cm_adapter_identity(relative, line)
            ):
                violations.append(Violation(path, line_number, "duplicate_threshold_policy", line))
        for line_number, evidence in _direct_process_findings(path, relative, text):
            violations.append(
                Violation(
                    path,
                    line_number,
                    "direct_frustrampnn_process_execution",
                    evidence,
                )
            )
    return sorted(violations, key=lambda item: (str(item.path), item.line, item.rule))


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (default: script parent)",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    violations = scan(args.root)
    for violation in violations:
        print(violation.render(args.root))
    if violations:
        print(f"FAIL: {len(violations)} active FrustraMPNN retirement violation(s)", file=sys.stderr)
        return 1
    print("PASS: active FrustraMPNN production paths satisfy Phase 7 retirement rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
