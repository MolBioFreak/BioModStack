#!/usr/bin/env python3
"""Fail-closed Phase 6 operator acceptance for the managed FrustraMPNN stack.

This harness is deliberately an API client and evidence verifier.  It never
executes Nextflow, the model, or a container directly, and it never fabricates
missing product evidence.  A committed clean source tree, operator-supplied
canonical case definitions, and retained managed-job evidence are mandatory.

Runtime environment (values are never written to evidence):
  BMS_PHASE6_API_BASE       Managed API origin (required).
  BMS_PHASE6_CASES_ROOT     Private directory of <case>.json definitions.
  BMS_PHASE6_RESULTS_ROOT   Local root containing managed job output_dir paths.
  BMS_PHASE6_SPEC_PATH      Canonical Phase 6 specification file.
  BMS_PHASE6_AUTH_FILE      Optional private regular file containing a bearer token.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "platform" / "api"
CASES = (
    "scheduler_gpu",
    "one_ubq_1520",
    "exact_multichain_map",
    "structure_prediction",
    "protein_design",
    "complex_prediction",
    "antibody_denovo",
    "conformational_mapping",
    "intentional_runtime_failure",
    "disabled_not_requested",
)
PARENT_CASES = frozenset(
    {"structure_prediction", "protein_design", "complex_prediction", "antibody_denovo"}
)
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
SUCCESS_STATES = frozenset({"completed", "success", "succeeded", "complete"})
FAILURE_STATES = frozenset({"failed", "error", "cancelled"})
AA_ORDER = "ACDEFGHIKLMNPQRSTVWY"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_RETAINED_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_RETAINED_BYTES = 4 * 1024 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024 * 1024
CASE_SCHEMA_NAME = "bms.frustrampnn.phase6.case"
PACKET_SCHEMA_NAME = "bms.frustrampnn.phase6.packet"
INDEX_SCHEMA_NAME = "bms.frustrampnn.phase6.index"
FAILURE_SCHEMA_NAME = "bms.frustrampnn.phase6.incomplete"

# Every successful managed run must expose these evidence roles.  Definitions
# may narrow a role to exact relative paths, but may not remove one.
DEFAULT_EVIDENCE_PATTERNS: dict[str, tuple[str, ...]] = {
    "report": ("*report*.html", "*report*.json"),
    "timeline": ("*timeline*.html", "*timeline*.json"),
    "trace": ("*trace*.txt", "*trace*.tsv", "*trace*.json"),
    "dag": ("*dag*.svg", "*dag*.html", "*dag*.json"),
    "nextflow_log": ("nextflow.log",),
    "stdout": ("*stdout*",),
    "stderr": ("*stderr*",),
    "terminal_manifest": ("*terminal_manifest*.json",),
    "request": ("workflow_component_request_v1.json", "*request*.json"),
    "source": ("normalized_input.pdb", "canonical_source.pdb", "*source*.pdb"),
    "structure_map": ("frustrampnn_structure_map_v1.json", "*structure_map*.json"),
    "landscape": ("frustrampnn_landscape_v1.json", "*landscape*.json"),
    "summary": ("frustrampnn_summary_v1.json", "*summary*.json"),
    "receipt": ("frustrampnn_execution_receipt_v1.json", "*receipt*.json"),
    "result": ("workflow_component_result_v1.json",),
    "artifact_manifest": ("frustrampnn_result_manifest_v1.json", "*artifact_manifest*.json"),
}

SCHEMA_BY_BASENAME = {
    "workflow_component_request_v1.json": "workflow_component_request_v1",
    "workflow_component_result_v1.json": "workflow_component_result_v1",
    "frustrampnn_structure_map_v1.json": "frustrampnn_structure_map_v1",
    "frustrampnn_landscape_v1.json": "frustrampnn_landscape_v1",
    "frustrampnn_summary_v1.json": "frustrampnn_summary_v1",
    "frustrampnn_result_manifest_v1.json": "frustrampnn_result_manifest_v1",
    "frustrampnn_execution_receipt_v1.json": "frustrampnn_execution_receipt_v1",
}


class AcceptanceFailure(RuntimeError):
    """A stable, typed acceptance failure that can never be interpreted as PASS."""

    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class ApiResponse:
    def __init__(self, *, status: int, headers: Mapping[str, str], body: bytes, data: Any):
        self.status = status
        self.headers = dict(headers)
        self.body = body
        self.data = data


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise AcceptanceFailure("api_redirect", f"managed API redirect is forbidden: HTTP {code}")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AcceptanceFailure("noncanonical_json", f"value is not canonical JSON: {exc}") from exc


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AcceptanceFailure("duplicate_json_key", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json_loads(raw: bytes, *, source: str, require_canonical: bool = False) -> Any:
    if len(raw) > MAX_JSON_BYTES:
        raise AcceptanceFailure("json_too_large", f"{source} exceeds the JSON byte limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AcceptanceFailure("json_encoding", f"{source} is not UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda token: (_raise_nonfinite(token)),
        )
    except AcceptanceFailure:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise AcceptanceFailure("invalid_json", f"{source} is not valid JSON: {exc}") from exc
    if require_canonical and raw != _canonical_bytes(value):
        raise AcceptanceFailure("noncanonical_json", f"{source} is not canonical JSON")
    return value


def _raise_nonfinite(token: str) -> NoReturn:
    raise AcceptanceFailure("nonfinite_json", f"non-finite JSON number: {token}")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_relative_path(value: object, *, field: str = "path") -> str:
    if not isinstance(value, str) or not value or "\\" in value or "//" in value:
        raise AcceptanceFailure("unsafe_path", f"unsafe {field}: {value!r}")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise AcceptanceFailure("unsafe_path", f"unsafe {field}: {value!r}")
    return value


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _open_regular_nofollow(path: Path, *, max_bytes: int = MAX_RETAINED_FILE_BYTES) -> tuple[bytes, os.stat_result]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise AcceptanceFailure("artifact_missing", f"cannot lstat {path}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode):
        raise AcceptanceFailure("artifact_symlink", f"symlink evidence is forbidden: {path}")
    if not stat.S_ISREG(before.st_mode):
        raise AcceptanceFailure("artifact_not_regular", f"evidence is not regular: {path}")
    if before.st_size > max_bytes:
        raise AcceptanceFailure("artifact_too_large", f"evidence exceeds byte limit: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise AcceptanceFailure("artifact_symlink", f"symlink evidence is forbidden: {path}") from exc
        raise AcceptanceFailure("artifact_open", f"cannot open evidence {path}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise AcceptanceFailure("artifact_race", f"evidence changed during open: {path}")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                raise AcceptanceFailure("artifact_too_large", f"evidence exceeds byte limit: {path}")
        after = os.fstat(descriptor)
        if (
            after.st_size != size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise AcceptanceFailure("artifact_race", f"evidence changed while read: {path}")
        return b"".join(chunks), after
    finally:
        os.close(descriptor)


def _write_exclusive(path: Path, raw: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, mode)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_canonical_exclusive(path: Path, value: Any) -> None:
    _write_exclusive(path, _canonical_bytes(value))


def _git_subprocess(repo: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise AcceptanceFailure("git_unavailable", f"cannot execute git: {exc}") from exc
    if completed.returncode != 0:
        diagnostic = completed.stderr.decode("utf-8", "replace").strip()
        raise AcceptanceFailure("git_failed", f"git {' '.join(args)} failed: {diagnostic}")
    try:
        return completed.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AcceptanceFailure("git_encoding", f"git {' '.join(args)} output is not UTF-8") from exc


def require_clean_committed_tree(
    repo: Path, *, git: Callable[..., str] = _git_subprocess
) -> dict[str, Any]:
    """Require HEAD with no staged, unstaged, or untracked bytes and record identity."""

    head = git(repo, "rev-parse", "HEAD").strip()
    branch = git(repo, "branch", "--show-current").strip()
    status_text = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    worktree_diff = git(repo, "diff", "--no-ext-diff", "--binary", "HEAD")
    index_diff = git(repo, "diff", "--cached", "--no-ext-diff", "--binary", "HEAD")
    tree = git(repo, "write-tree").strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", head):
        raise AcceptanceFailure("git_head", "repository has no valid committed HEAD")
    if not branch:
        raise AcceptanceFailure("git_detached", "detached HEAD is forbidden for Phase 6 acceptance")
    lines = status_text.splitlines()
    untracked = [line[3:] for line in lines if line.startswith("?? ")]
    staged = [line[3:] for line in lines if len(line) >= 3 and line[0] not in {" ", "?"}]
    unstaged = [line[3:] for line in lines if len(line) >= 3 and line[1] not in {" ", "?"}]
    if untracked:
        raise AcceptanceFailure("git_untracked", "clean tree required; untracked paths exist", details={"paths": untracked})
    if staged or index_diff:
        raise AcceptanceFailure("git_staged", "clean tree required; staged changes exist", details={"paths": staged})
    if unstaged or worktree_diff or lines:
        raise AcceptanceFailure("git_dirty", "clean tree required; unstaged changes exist", details={"paths": unstaged})
    return {
        "head": head,
        "branch": branch,
        "tree": tree,
        "status_sha256": _sha256(status_text.encode("utf-8")),
        "worktree_diff_sha256": _sha256(worktree_diff.encode("utf-8")),
        "index_diff_sha256": _sha256(index_diff.encode("utf-8")),
        "clean": True,
    }


def parse_cli(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        action="append",
        required=True,
        metavar="CASE[,CASE...]",
        help="repeatable/comma-separated exact Phase 6 cases",
    )
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--no-resume",
        action="store_true",
        required=True,
        help="mandatory assertion that no prior work/output is reused",
    )
    args = parser.parse_args(argv)
    normalized: list[str] = []
    for group in args.cases:
        parts = group.split(",")
        if not parts or any(not part or part != part.strip() for part in parts):
            parser.error("--cases contains an empty or whitespace-padded case")
        normalized.extend(parts)
    unknown = [case for case in normalized if case not in CASES]
    duplicates = sorted({case for case in normalized if normalized.count(case) > 1})
    if unknown:
        parser.error(f"unknown case(s): {','.join(unknown)}")
    if duplicates:
        parser.error(f"duplicate case(s): {','.join(duplicates)}")
    args.cases = tuple(normalized)
    args.output_root = args.output_root.expanduser().absolute()
    return args


def _strict_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AcceptanceFailure("invalid_contract", f"{field} must be an object")
    return value


def _forbid_resume(value: Any, *, path: str = "$", key: str | None = None) -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            lowered = child_key.lower().replace("-", "_")
            if lowered == "resume" and child not in {False, None, "false", "False", "0", 0}:
                raise AcceptanceFailure("resume_forbidden", f"resume/reuse enabled at {path}.{child_key}")
            _forbid_resume(child, path=f"{path}.{child_key}", key=child_key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _forbid_resume(child, path=f"{path}[{index}]", key=key)
    elif isinstance(value, str):
        tokens = value.split()
        if any(token == "-resume" or token.startswith("-resume=") for token in tokens):
            raise AcceptanceFailure("resume_forbidden", f"-resume forbidden at {path}")
        if key and key.lower() in {"output_dir", "work_dir", "output_root", "work_root"}:
            # Paths are checked separately against unique roots.
            return


def validate_managed_submission(case: str, submission: Mapping[str, Any]) -> None:
    data = _strict_mapping(dict(submission), field="submission")
    allowed_keys = {"method", "path", "json", "multipart", "headers"}
    extra = set(data) - allowed_keys
    if extra:
        raise AcceptanceFailure("direct_execution", f"submission has forbidden execution keys: {sorted(extra)}")
    if data.get("method") != "POST":
        raise AcceptanceFailure("submission_method", "managed submission must use POST")
    path = data.get("path")
    if not isinstance(path, str) or not path.startswith("/") or "?" in path or "#" in path:
        raise AcceptanceFailure("submission_path", "submission path must be an absolute API path without query/fragment")
    exact_upload = "/api/frustrampnn/jobs/uploads/analyze"
    allowed = path == "/api/jobs" or path == exact_upload or path.startswith("/api/conformational-mapping/")
    if not allowed:
        raise AcceptanceFailure("unmanaged_entrypoint", f"not an allowlisted managed API entrypoint: {path}")
    if case in PARENT_CASES | {"disabled_not_requested"} and path != "/api/jobs":
        raise AcceptanceFailure("wrong_entrypoint", f"{case} must launch through /api/jobs")
    if case in {"scheduler_gpu", "one_ubq_1520", "exact_multichain_map", "intentional_runtime_failure"} and path != exact_upload:
        raise AcceptanceFailure("wrong_entrypoint", f"{case} must launch through the managed typed upload endpoint")
    if ("json" in data) == ("multipart" in data):
        raise AcceptanceFailure("submission_body", "submission must contain exactly one of json or multipart")
    if "headers" in data:
        headers = _strict_mapping(data["headers"], field="submission.headers")
        forbidden = [key for key in headers if key.lower() in {"authorization", "cookie", "x-api-key"}]
        if forbidden:
            raise AcceptanceFailure("embedded_secret", "case definitions must not embed auth headers")
    _forbid_resume(data)


def _load_canonical_file(path: Path, *, root: Path | None = None) -> tuple[Any, dict[str, Any]]:
    resolved_parent = path.parent.resolve()
    if root is not None and not _is_within(resolved_parent, root.resolve()):
        raise AcceptanceFailure("path_escape", f"file escapes configured root: {path}")
    raw, file_stat = _open_regular_nofollow(path, max_bytes=MAX_JSON_BYTES)
    value = _json_loads(raw, source=os.fspath(path), require_canonical=True)
    return value, {
        "path": os.fspath(path),
        "size_bytes": len(raw),
        "sha256": _sha256(raw),
        "mode": stat.S_IMODE(file_stat.st_mode),
    }


def load_case_definition(case: str, cases_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    value, record = _load_canonical_file(cases_root / f"{case}.json", root=cases_root)
    definition = _strict_mapping(value, field=f"case {case}")
    required = {"schema_name", "schema_version", "case", "submission", "timeout_seconds", "expected"}
    if set(definition) != required:
        raise AcceptanceFailure("case_keys", f"{case} definition keys must be exactly {sorted(required)}")
    if definition["schema_name"] != CASE_SCHEMA_NAME or definition["schema_version"] != 1:
        raise AcceptanceFailure("case_schema", f"{case} has the wrong case-definition schema")
    if definition["case"] != case:
        raise AcceptanceFailure("case_identity", f"definition identity does not match requested case {case}")
    timeout = definition["timeout_seconds"]
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 30 <= timeout <= 86400:
        raise AcceptanceFailure("case_timeout", f"{case} timeout must be an integer in [30,86400]")
    _strict_mapping(definition["expected"], field=f"{case}.expected")
    validate_managed_submission(case, _strict_mapping(definition["submission"], field="submission"))
    return definition, record


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            lowered = key.lower()
            if any(marker in lowered for marker in ("token", "secret", "password", "authorization", "cookie", "api_key", "apikey")):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact(child)
        return redacted
    if isinstance(value, list):
        return [_redact(child) for child in value]
    return value


class ApiClient:
    def __init__(self, base: str, auth_token: str | None):
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise AcceptanceFailure("api_base", "API base must be a credential-free HTTP(S) origin")
        if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise AcceptanceFailure("api_base", "API base must not contain path/query/fragment")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise AcceptanceFailure("api_tls", "plain HTTP is permitted only for loopback API origins")
        self.base = f"{parsed.scheme}://{parsed.netloc}"
        self.auth_token = auth_token
        self.opener = urllib.request.build_opener(_NoRedirect())

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        timeout: float = 30.0,
        allow_status: Iterable[int] = (200,),
    ) -> ApiResponse:
        if not path.startswith("/") or "//" in path or "\\" in path:
            raise AcceptanceFailure("api_path", f"unsafe API path: {path}")
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=timeout)
            status = response.status
            raw = response.read(MAX_JSON_BYTES + 1)
            response_headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            raw = exc.read(MAX_JSON_BYTES + 1)
            status = exc.code
            response_headers = dict(exc.headers.items())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AcceptanceFailure("api_unavailable", f"managed API request failed: {method} {path}: {exc}") from exc
        if len(raw) > MAX_JSON_BYTES:
            raise AcceptanceFailure("api_response_too_large", f"API response too large: {method} {path}")
        data = _json_loads(raw, source=f"API {method} {path}") if raw else None
        if status not in set(allow_status):
            raise AcceptanceFailure(
                "api_status",
                f"managed API returned HTTP {status}: {method} {path}",
                details={"response": _redact(data)},
            )
        return ApiResponse(status=status, headers=response_headers, body=raw, data=data)


def _load_auth_token(path_value: str | None) -> str | None:
    if not path_value:
        return None
    path = Path(path_value).expanduser().absolute()
    raw, file_stat = _open_regular_nofollow(path, max_bytes=16 * 1024)
    if file_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise AcceptanceFailure("auth_permissions", "auth file must not grant group/other permissions")
    try:
        token = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise AcceptanceFailure("auth_encoding", "auth file must be UTF-8") from exc
    if not token or any(character.isspace() for character in token):
        raise AcceptanceFailure("auth_format", "auth file must contain one non-whitespace token")
    return token


def _multipart_body(multipart: Mapping[str, Any], cases_root: Path) -> tuple[bytes, str, dict[str, Any]]:
    data = _strict_mapping(dict(multipart), field="multipart")
    required = {"field", "source", "sha256"}
    optional = {"filename", "content_type", "fields"}
    if set(data) - required - optional or not required.issubset(data):
        raise AcceptanceFailure("multipart_contract", "multipart requires field/source/sha256 and only known optional keys")
    field = data["field"]
    source_text = _safe_relative_path(data["source"], field="multipart.source")
    if not isinstance(field, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", field):
        raise AcceptanceFailure("multipart_field", "unsafe multipart field")
    source = cases_root / source_text
    raw, _ = _open_regular_nofollow(source)
    expected_sha = data["sha256"]
    if not isinstance(expected_sha, str) or _sha256(raw) != expected_sha:
        raise AcceptanceFailure("fixture_hash", f"fixture hash mismatch: {source_text}")
    filename = data.get("filename", source.name)
    if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", filename):
        raise AcceptanceFailure("multipart_filename", "unsafe multipart filename")
    content_type = data.get("content_type", "chemical/x-pdb")
    if not isinstance(content_type, str) or "\r" in content_type or "\n" in content_type:
        raise AcceptanceFailure("multipart_content_type", "unsafe multipart content type")
    fields = _strict_mapping(data.get("fields", {}), field="multipart.fields")
    boundary = f"phase6-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name in sorted(fields):
        value = fields[name]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
            raise AcceptanceFailure("multipart_field", "unsafe multipart scalar field")
        if not isinstance(value, (str, int, float, bool)) or isinstance(value, float) and not math.isfinite(value):
            raise AcceptanceFailure("multipart_value", f"unsupported multipart value: {name}")
        rendered = str(value).lower() if isinstance(value, bool) else str(value)
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                rendered.encode("utf-8"),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            raw,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}", {
        "field": field,
        "source": source_text,
        "source_size_bytes": len(raw),
        "source_sha256": expected_sha,
        "fields": _redact(fields),
    }


def _extract_job_id(data: Any) -> str:
    mapping = _strict_mapping(data, field="submission response")
    candidates = [mapping.get(key) for key in ("id", "job_id", "child_job_id")]
    values = [value for value in candidates if isinstance(value, str) and value]
    if len(set(values)) != 1:
        raise AcceptanceFailure("job_identity", "submission response must expose one unambiguous job ID")
    return values[0]


def _job_status(job: Mapping[str, Any]) -> str:
    for key in ("status", "queue_status", "state"):
        value = job.get(key)
        if isinstance(value, str) and value:
            return value.lower()
    raise AcceptanceFailure("job_status", "job response has no typed status")


def _poll_job(
    client: ApiClient,
    job_id: str,
    *,
    timeout_seconds: int,
    chronology: list[dict[str, Any]],
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    delay = 1.0
    while True:
        response = client.request("GET", f"/api/jobs/{urllib.parse.quote(job_id, safe='')}")
        job = _strict_mapping(response.data, field="job")
        status = _job_status(job)
        gpu_snapshot = client.request("GET", "/api/gpu/gpus").data
        chronology.append(
            {
                "observed_at": _now(),
                "kind": "poll",
                "job_id": job_id,
                "status": status,
                "queue_status": job.get("queue_status"),
                "assigned_gpu": job.get("assigned_gpu"),
                "gpu_snapshot": _redact(gpu_snapshot),
            }
        )
        if status in TERMINAL_STATES:
            return job
        if time.monotonic() >= deadline:
            cancel = client.request(
                "DELETE",
                f"/api/jobs/{urllib.parse.quote(job_id, safe='')}",
                allow_status=(200, 202, 204),
            )
            chronology.append(
                {
                    "observed_at": _now(),
                    "kind": "cancel_requested",
                    "job_id": job_id,
                    "http_status": cancel.status,
                }
            )
            cancel_deadline = time.monotonic() + 60
            while time.monotonic() < cancel_deadline:
                observed = client.request("GET", f"/api/jobs/{urllib.parse.quote(job_id, safe='')}")
                cancelled = _strict_mapping(observed.data, field="cancelled job")
                cancelled_status = _job_status(cancelled)
                chronology.append(
                    {
                        "observed_at": _now(),
                        "kind": "cancel_poll",
                        "job_id": job_id,
                        "status": cancelled_status,
                    }
                )
                if cancelled_status in TERMINAL_STATES:
                    break
                time.sleep(1)
            raise AcceptanceFailure("job_timeout", f"job {job_id} exceeded {timeout_seconds}s and was cancelled")
        time.sleep(delay)
        delay = min(delay * 1.5, 10.0)


def _snapshot_api(client: ApiClient, path: str, *, allow_status: Iterable[int] = (200,)) -> Any:
    return client.request("GET", path, allow_status=allow_status).data


def _list_related_jobs(client: ApiClient, parent: Mapping[str, Any], submitted_id: str) -> list[dict[str, Any]]:
    related: dict[str, dict[str, Any]] = {}
    parent_id = parent.get("parent_job_id") if isinstance(parent.get("parent_job_id"), str) else None
    identifiers = {submitted_id}
    if isinstance(parent.get("id"), str):
        identifiers.add(parent["id"])
    if parent_id:
        identifiers.add(parent_id)
    for identifier in tuple(identifiers):
        response = client.request("GET", f"/api/jobs/{urllib.parse.quote(identifier, safe='')}")
        job = _strict_mapping(response.data, field="related job")
        if isinstance(job.get("id"), str):
            related[job["id"]] = job
    listing = client.request("GET", "/api/jobs?include_children=true&limit=500")
    data = listing.data
    items = data.get("jobs", data.get("items", [])) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise AcceptanceFailure("jobs_listing", "managed jobs listing is not an array")
    for value in items:
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            continue
        if value["id"] in identifiers or value.get("parent_job_id") in identifiers or value.get("id") == parent_id:
            related[value["id"]] = value
    return [related[key] for key in sorted(related)]


def _derive_role(relative: str) -> str | None:
    path = PurePosixPath(relative)
    name = path.name
    for role, patterns in DEFAULT_EVIDENCE_PATTERNS.items():
        if any(path.match(pattern) or name == pattern for pattern in patterns):
            return role
    if "frustrampnn/results/" in relative:
        return "terminal_bundle"
    return None


def _walk_regular_files(root: Path) -> list[Path]:
    if not root.is_absolute():
        raise AcceptanceFailure("output_path", f"managed output path is not absolute: {root}")
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise AcceptanceFailure("output_missing", f"managed output root unavailable: {root}: {exc}") from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise AcceptanceFailure("output_type", f"managed output root must be a real directory: {root}")
    found: list[Path] = []
    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(directory)
        safe_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            child_stat = child.lstat()
            if stat.S_ISLNK(child_stat.st_mode):
                raise AcceptanceFailure("output_symlink", f"symlink directory in managed output: {child}")
            if not stat.S_ISDIR(child_stat.st_mode):
                raise AcceptanceFailure("output_type", f"non-directory in output traversal: {child}")
            safe_dirs.append(name)
        dirnames[:] = safe_dirs
        for name in sorted(filenames):
            path = current / name
            path_stat = path.lstat()
            if stat.S_ISLNK(path_stat.st_mode):
                raise AcceptanceFailure("output_symlink", f"symlink file in managed output: {path}")
            if not stat.S_ISREG(path_stat.st_mode):
                raise AcceptanceFailure("output_type", f"non-regular managed output: {path}")
            found.append(path)
    return found


def _retain_managed_outputs(
    jobs: Sequence[Mapping[str, Any]], results_root: Path, evidence_root: Path
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen_sources: set[tuple[int, int]] = set()
    total_bytes = 0
    for job in jobs:
        job_id = job.get("id")
        output_text = job.get("output_dir")
        if not isinstance(job_id, str) or not job_id or not isinstance(output_text, str) or not output_text:
            raise AcceptanceFailure("job_output_identity", "every related job must expose id and output_dir")
        output = Path(output_text)
        resolved_output = output.resolve()
        if not _is_within(resolved_output, results_root.resolve()):
            raise AcceptanceFailure("output_escape", f"job {job_id} output_dir escapes BMS_PHASE6_RESULTS_ROOT")
        for source in _walk_regular_files(output):
            relative_source = source.relative_to(output).as_posix()
            role = _derive_role(relative_source)
            if role is None:
                continue
            source_lstat = source.lstat()
            inode = (source_lstat.st_dev, source_lstat.st_ino)
            if inode in seen_sources:
                raise AcceptanceFailure("artifact_alias", f"managed output aliases an already retained inode: {source}")
            seen_sources.add(inode)
            raw, _ = _open_regular_nofollow(source)
            total_bytes += len(raw)
            if total_bytes > MAX_TOTAL_RETAINED_BYTES:
                raise AcceptanceFailure("packet_too_large", "retained packet exceeds total byte limit")
            retained_relative = PurePosixPath("files", job_id, relative_source).as_posix()
            destination = evidence_root / retained_relative
            _write_exclusive(destination, raw)
            record = {
                "role": role,
                "path": retained_relative,
                "source_job_id": job_id,
                "source_relative_path": relative_source,
                "size_bytes": len(raw),
                "sha256": _sha256(raw),
            }
            records.append(record)
    return sorted(records, key=lambda value: value["path"])


def validate_packet_inventory(
    root: Path,
    records: Sequence[Mapping[str, Any]],
    *,
    expected_paths: set[str] | None = None,
    return_documents: bool = False,
) -> dict[str, Any]:
    """Validate exact retained closure with no-follow reads and canonical JSON."""

    root = root.absolute()
    if root.is_symlink() or not root.is_dir():
        raise AcceptanceFailure("packet_root", "packet evidence root must be a real directory")
    manifested: set[str] = set()
    documents: dict[str, Any] = {}
    for raw_record in records:
        record = _strict_mapping(dict(raw_record), field="inventory record")
        path_text = _safe_relative_path(record.get("path"))
        if path_text in manifested:
            raise AcceptanceFailure("inventory_duplicate", f"duplicate inventory path: {path_text}")
        manifested.add(path_text)
        expected_size = record.get("size_bytes")
        expected_sha = record.get("sha256")
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0:
            raise AcceptanceFailure("inventory_size", f"invalid inventory size: {path_text}")
        if not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
            raise AcceptanceFailure("inventory_sha", f"invalid inventory SHA-256: {path_text}")
        candidate = root / path_text
        if candidate.is_symlink():
            raise AcceptanceFailure("artifact_symlink", f"symlink evidence is forbidden: {path_text}")
        try:
            parent = candidate.parent.resolve(strict=True)
        except OSError as exc:
            raise AcceptanceFailure("artifact_missing", f"artifact parent unavailable: {path_text}") from exc
        if not _is_within(parent, root.resolve()):
            raise AcceptanceFailure("unsafe_path", f"artifact escapes evidence root: {path_text}")
        raw, _ = _open_regular_nofollow(candidate)
        if len(raw) != expected_size:
            raise AcceptanceFailure("artifact_size", f"size mismatch: {path_text}")
        if _sha256(raw) != expected_sha:
            raise AcceptanceFailure("artifact_hash", f"SHA-256 mismatch: {path_text}")
        if path_text.endswith(".json"):
            documents[path_text] = _json_loads(raw, source=path_text, require_canonical=True)
    actual: set[str] = set()
    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(directory)
        for name in list(dirnames):
            child = current / name
            if child.is_symlink():
                raise AcceptanceFailure("artifact_symlink", f"symlink directory in packet: {child}")
        for name in filenames:
            child = current / name
            if child.is_symlink():
                raise AcceptanceFailure("artifact_symlink", f"symlink file in packet: {child}")
            actual.add(child.relative_to(root).as_posix())
    if actual != manifested:
        extra = sorted(actual - manifested)
        missing = sorted(manifested - actual)
        raise AcceptanceFailure(
            "inventory_closure",
            "packet contains unmanifested or missing retained files",
            details={"unmanifested": extra, "missing": missing},
        )
    if expected_paths is not None and manifested != expected_paths:
        raise AcceptanceFailure("inventory_expected", "packet inventory does not equal expected path set")
    return documents if return_documents else {}


def _product_schema_validator() -> Callable[[str, Any], None]:
    if os.fspath(API_ROOT) not in sys.path:
        sys.path.insert(0, os.fspath(API_ROOT))
    try:
        from services.frustrampnn.contracts import ContractValidationError, validate_schema
    except Exception as exc:
        raise AcceptanceFailure("product_validator_unavailable", f"canonical product validators unavailable: {exc}") from exc

    def validate(schema_key: str, document: Any) -> None:
        try:
            validate_schema(schema_key, document)
        except ContractValidationError as exc:
            raise AcceptanceFailure("schema_validation", f"{schema_key} validation failed: {exc}") from exc

    return validate


def _documents_named(documents: Mapping[str, Any], basename: str) -> list[tuple[str, dict[str, Any]]]:
    values: list[tuple[str, dict[str, Any]]] = []
    for path, value in documents.items():
        if PurePosixPath(path).name == basename and isinstance(value, dict):
            values.append((path, value))
    return values


def _identity_tuple(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("entity_instance_id"),
        row.get("auth_asym_id"),
        row.get("auth_seq_id"),
        row.get("insertion_code", ""),
        row.get("sequence_index"),
    )


def validate_one_ubq_1520(landscape: Mapping[str, Any]) -> dict[str, int]:
    data = _strict_mapping(dict(landscape), field="1UBQ landscape")
    residues = data.get("residues")
    expected_wt = data.get("expected_wt_sequence")
    if not isinstance(residues, list) or len(residues) != 76:
        raise AcceptanceFailure("ubq_residue_count", "1UBQ must contain exactly 76 canonical residues")
    if not isinstance(expected_wt, str) or len(expected_wt) != 76:
        raise AcceptanceFailure("ubq_wt_sequence", "1UBQ expected WT sequence must contain exactly 76 residues")
    identities: set[tuple[Any, ...]] = set()
    row_keys: set[tuple[tuple[Any, ...], str]] = set()
    native_count = 0
    observed_wt: list[str] = []
    for index, raw_residue in enumerate(residues, 1):
        residue = _strict_mapping(raw_residue, field=f"1UBQ residue {index}")
        identity = _identity_tuple(residue)
        if identity in identities:
            raise AcceptanceFailure("ubq_duplicate_residue", f"duplicate 1UBQ residue identity at {index}")
        identities.add(identity)
        if residue.get("sequence_index") != index:
            raise AcceptanceFailure("ubq_order", "1UBQ sequence indices must be exactly 1..76")
        wt = residue.get("wt")
        slots = residue.get("slots")
        if not isinstance(wt, str) or wt not in AA_ORDER:
            raise AcceptanceFailure("ubq_wt", f"invalid 1UBQ WT at sequence index {index}")
        observed_wt.append(wt)
        if not isinstance(slots, list) or len(slots) != 20:
            raise AcceptanceFailure("ubq_slot_count", f"1UBQ residue {index} must have exactly 20 slots")
        mutation_order = [slot.get("mutation_aa") if isinstance(slot, dict) else None for slot in slots]
        if mutation_order != list(AA_ORDER):
            raise AcceptanceFailure("ubq_aa_order", f"1UBQ residue {index} does not use exact canonical AA order")
        residue_native = 0
        for slot in slots:
            slot_map = _strict_mapping(slot, field=f"1UBQ slot {index}")
            mutation = slot_map.get("mutation_aa")
            key = (identity, mutation)
            if key in row_keys:
                raise AcceptanceFailure("ubq_duplicate_row", "1UBQ landscape contains a duplicate row")
            row_keys.add(key)
            score = slot_map.get("score")
            if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score):
                raise AcceptanceFailure("ubq_nonfinite", "1UBQ landscape scores must be finite")
            native = slot_map.get("native")
            if not isinstance(native, bool) or native != (mutation == wt):
                raise AcceptanceFailure("ubq_native", "1UBQ native slot flags must exactly match WT")
            residue_native += int(native)
        if residue_native != 1:
            raise AcceptanceFailure("ubq_native_count", "1UBQ requires exactly one native slot per residue")
        native_count += residue_native
    if "".join(observed_wt) != expected_wt:
        raise AcceptanceFailure("ubq_wt_order", "1UBQ exact WT/order does not match retained expectation")
    if len(row_keys) != 1520:
        raise AcceptanceFailure("ubq_cardinality", "1UBQ must contain exactly 1520 unique rows")
    return {"residues": 76, "rows": 1520, "native_slots": native_count}


def validate_exact_multichain_map(
    structure_map: Mapping[str, Any], expected_rows: Sequence[Mapping[str, Any]]
) -> dict[str, int]:
    data = _strict_mapping(dict(structure_map), field="structure map")
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise AcceptanceFailure("map_rows", "structure map rows must be a non-empty array")
    canonical_fields = (
        "entity_instance_id",
        "auth_asym_id",
        "auth_seq_id",
        "insertion_code",
        "sequence_index",
        "wt",
    )
    observed: list[dict[str, Any]] = []
    identities: set[tuple[Any, ...]] = set()
    chains: set[tuple[Any, Any]] = set()
    for index, raw_row in enumerate(rows):
        row = _strict_mapping(raw_row, field=f"structure map row {index}")
        if any(field not in row for field in canonical_fields):
            raise AcceptanceFailure("map_field", f"structure map row {index} lacks exact identity fields")
        projected = {field: row[field] for field in canonical_fields}
        identity = _identity_tuple(projected)
        if identity in identities:
            raise AcceptanceFailure("map_duplicate", f"duplicate/ambiguous structure identity: {identity!r}")
        identities.add(identity)
        chains.add((projected["entity_instance_id"], projected["auth_asym_id"]))
        observed.append(projected)
    expected = [{field: row.get(field) for field in canonical_fields} for row in expected_rows]
    if observed != expected:
        raise AcceptanceFailure("map_exact_mismatch", "chain/auth residue/insertion/sequence mapping is not exact")
    return {"rows": len(observed), "chains": len(chains)}


def validate_intentional_runtime_failure(evidence: Mapping[str, Any]) -> None:
    data = _strict_mapping(dict(evidence), field="intentional failure evidence")
    parent = _strict_mapping(data.get("parent"), field="failure parent")
    child = _strict_mapping(data.get("child"), field="failure child")
    terminal = _strict_mapping(data.get("terminal_result"), field="failure terminal result")
    if _job_status(parent) not in FAILURE_STATES or _job_status(child) not in FAILURE_STATES:
        raise AcceptanceFailure("failure_status", "intentional managed runtime failure did not terminate failed")
    if terminal.get("status") not in FAILURE_STATES:
        raise AcceptanceFailure("failure_terminal", "intentional failure lacks a typed failed terminal result")
    failure_class = terminal.get("failure_class")
    diagnostic = terminal.get("diagnostic") or terminal.get("error")
    if not isinstance(failure_class, str) or not failure_class or not isinstance(diagnostic, str) or not diagnostic:
        raise AcceptanceFailure("failure_classification", "intentional failure lacks class and diagnostic")
    forbidden = (
        list(data.get("success_markers", []))
        + list(data.get("persisted_results", []))
        + list(terminal.get("artifacts", []))
    )
    if forbidden:
        raise AcceptanceFailure("failure_success_evidence", "intentional failure published success evidence")


def validate_disabled_not_requested(evidence: Mapping[str, Any]) -> None:
    data = _strict_mapping(dict(evidence), field="disabled evidence")
    parent = _strict_mapping(data.get("parent"), field="disabled parent")
    manifest = _strict_mapping(data.get("terminal_manifest"), field="disabled terminal manifest")
    provenance = _strict_mapping(parent.get("provenance"), field="disabled parent provenance")
    states = _strict_mapping(provenance.get("stage_terminal_states"), field="stage_terminal_states")
    component = _strict_mapping(states.get("frustrampnn"), field="frustrampnn terminal state")
    if component.get("status") != "not_requested" or component.get("outputs") != []:
        raise AcceptanceFailure("disabled_not_durable", "scheduler-owned FrustraMPNN state is not durably not_requested")
    expected = {
        "status": "not_requested",
        "requiredness": "not_requested",
        "candidate_count": 0,
        "candidates": [],
        "reported_outputs": [],
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise AcceptanceFailure("disabled_manifest", "disabled terminal manifest is not canonical not_requested")
    if data.get("model_tasks"):
        raise AcceptanceFailure("disabled_model_task", "disabled case scheduled a model task")
    if data.get("result_bundles"):
        raise AcceptanceFailure("disabled_output", "disabled case published model output/result bundles")


def _find_receipts(documents: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [value for _, value in _documents_named(documents, "frustrampnn_execution_receipt_v1.json")]


def _validate_scheduler_gpu(
    jobs: Sequence[Mapping[str, Any]], documents: Mapping[str, Any], chronology: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    receipts = _find_receipts(documents)
    if len(receipts) != 1:
        raise AcceptanceFailure("gpu_receipt_count", "scheduler_gpu requires exactly one execution receipt")
    receipt = receipts[0]
    physical = receipt.get("assigned_physical_gpu_id")
    if not isinstance(physical, str) or not physical:
        raise AcceptanceFailure("gpu_assignment", "execution receipt lacks physical GPU identity")
    scheduler_assignments = {
        str(job.get("assigned_gpu")) for job in jobs if job.get("assigned_gpu") is not None
    } | {
        str(item.get("assigned_gpu")) for item in chronology if item.get("assigned_gpu") is not None
    }
    if physical not in scheduler_assignments:
        raise AcceptanceFailure("gpu_cross_binding", "receipt physical GPU does not match scheduler assignment")
    if receipt.get("task_visible_device_index") != 0:
        raise AcceptanceFailure("gpu_visibility", "task-visible device must be exactly 0")
    argv = receipt.get("argv")
    if not isinstance(argv, list) or not all(isinstance(value, str) for value in argv):
        raise AcceptanceFailure("gpu_argv", "execution receipt argv is invalid")
    if "--device" not in argv or argv[argv.index("--device") + 1 : argv.index("--device") + 2] != ["cuda"]:
        raise AcceptanceFailure("gpu_device", "contained invocation must use --device cuda")
    if any(value == "--gpu_id" or value.startswith("--gpu_id=") for value in argv):
        raise AcceptanceFailure("gpu_legacy_flag", "model invocation must not receive --gpu_id")
    visible_assignments = [
        value.split("=", 1)[1]
        for value in argv
        if value.startswith("CUDA_VISIBLE_DEVICES=")
    ]
    if visible_assignments != [physical]:
        raise AcceptanceFailure("gpu_visible_identity", "CUDA_VISIBLE_DEVICES must retain physical identity")
    samples: list[Mapping[str, Any]] = []
    try:
        physical_index = int(physical)
    except ValueError as exc:
        raise AcceptanceFailure("gpu_assignment", "physical GPU identity is not an integer index") from exc
    for observation in chronology:
        snapshot = observation.get("gpu_snapshot")
        gpu_values = snapshot.get("gpus") if isinstance(snapshot, dict) else None
        if not isinstance(gpu_values, list):
            continue
        for gpu in gpu_values:
            if isinstance(gpu, dict) and gpu.get("index") == physical_index:
                samples.append(gpu)
    if not samples:
        raise AcceptanceFailure("gpu_metrics", "scheduler chronology lacks physical GPU samples")
    memory_samples = [value.get("memory_used_mb") for value in samples]
    utilization_samples = [value.get("utilization") for value in samples]
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in memory_samples):
        raise AcceptanceFailure("gpu_peak_vram", "GPU memory chronology is malformed")
    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0 <= value <= 100
        for value in utilization_samples
    ):
        raise AcceptanceFailure("gpu_utilization", "GPU utilization chronology is malformed")
    peak = max(memory_samples) * 1024 * 1024
    utilization = max(utilization_samples)
    duration = receipt.get("duration_seconds")
    if not isinstance(peak, int) or isinstance(peak, bool) or peak <= 0:
        raise AcceptanceFailure("gpu_peak_vram", "peak VRAM evidence must be positive bytes")
    if not isinstance(utilization, (int, float)) or isinstance(utilization, bool) or not 0 < utilization <= 100:
        raise AcceptanceFailure("gpu_utilization", "peak GPU utilization must be in (0,100]")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0:
        raise AcceptanceFailure("gpu_duration", "GPU duration evidence must be positive")
    return {
        "physical_gpu_id": physical,
        "task_visible_device_index": 0,
        "peak_vram_bytes": peak,
        "peak_utilization_percent": utilization,
        "duration_seconds": duration,
    }


def _validate_runtime_identity(receipt: Mapping[str, Any], runtime: Mapping[str, Any]) -> None:
    expected = {
        "sif_sha256": runtime["sif_sha256"],
        "checkpoint_id": runtime["checkpoint_id"],
        "checkpoint_sha256": runtime["checkpoint_sha256"],
        "executable_path": runtime["executable_path"],
        "executable_sha256": runtime["executable_sha256"],
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise AcceptanceFailure("runtime_identity_mismatch", f"execution receipt {key} does not match canonical runtime")
    software = receipt.get("software_versions")
    if not isinstance(software, dict) or software.get("source_commit") != runtime["source_commit"]:
        raise AcceptanceFailure("source_revision_mismatch", "execution receipt source revision does not match canonical runtime")


def _candidate_id(document: Mapping[str, Any]) -> str | None:
    value = document.get("candidate_id")
    if isinstance(value, str) and value:
        return value
    candidate = document.get("candidate")
    return candidate.get("candidate_id") if isinstance(candidate, dict) and isinstance(candidate.get("candidate_id"), str) else None


def _validate_parent_case(
    case: str,
    jobs: Sequence[Mapping[str, Any]],
    documents: Mapping[str, Any],
) -> dict[str, Any]:
    parents = [job for job in jobs if not job.get("parent_job_id")]
    if len(parents) != 1 or _job_status(parents[0]) not in SUCCESS_STATES:
        raise AcceptanceFailure("parent_terminal", f"{case} parent must have one terminal success record")
    requests = [value for _, value in _documents_named(documents, "workflow_component_request_v1.json")]
    results = [value for _, value in _documents_named(documents, "workflow_component_result_v1.json")]
    manifests = [value for _, value in _documents_named(documents, "frustrampnn_result_manifest_v1.json")]
    if not requests or len(requests) != len(results) or len(results) != len(manifests):
        raise AcceptanceFailure("candidate_bundle_count", f"{case} requires one closed terminal bundle per candidate")
    request_ids: list[str] = []
    for request in requests:
        candidate_id = _candidate_id(request)
        source = request.get("source_artifact")
        if not candidate_id or not isinstance(source, dict) or not SHA256_RE.fullmatch(str(source.get("sha256", ""))):
            raise AcceptanceFailure("candidate_lineage", f"{case} candidate lacks typed source SHA lineage")
        if request.get("requiredness") != "required":
            raise AcceptanceFailure("candidate_requiredness", f"{case} requested candidate is not canonically required")
        required_lineage = ("parent_job_id", "parent_workflow_id", "producer_stage", "producer_candidate_key")
        if any(not isinstance(request.get(field), str) or not request[field] for field in required_lineage):
            raise AcceptanceFailure("candidate_lineage", f"{case} request lacks typed producer lineage")
        request_ids.append(candidate_id)
    if len(request_ids) != len(set(request_ids)):
        raise AcceptanceFailure("candidate_duplicate", f"{case} contains duplicate candidate identities")
    result_ids = [_candidate_id(value) for value in results]
    manifest_ids = [_candidate_id(value) for value in manifests]
    if sorted(request_ids) != sorted(result_ids) or sorted(request_ids) != sorted(manifest_ids):
        raise AcceptanceFailure("candidate_cross_binding", f"{case} request/result/manifest candidate IDs do not cross-bind")
    for result in results:
        if str(result.get("status", "")).lower() not in SUCCESS_STATES:
            raise AcceptanceFailure("component_failure", f"{case} parent passed before every component succeeded")
    provenance = parents[0].get("provenance")
    stages = provenance.get("stage_terminal_states", {}) if isinstance(provenance, dict) else {}
    component = stages.get("frustrampnn") if isinstance(stages, dict) else None
    if not isinstance(component, dict) or str(component.get("status", "")).lower() not in SUCCESS_STATES:
        raise AcceptanceFailure("parent_component_state", f"{case} parent lacks durable FrustraMPNN success association")
    outputs = component.get("outputs")
    if not isinstance(outputs, list) or len(outputs) < len(request_ids):
        raise AcceptanceFailure("persisted_association", f"{case} lacks persisted result association for every candidate")
    if case == "complex_prediction":
        for request in requests:
            key = str(request.get("producer_candidate_key", ""))
            if "placeholder" in key.lower() or not key:
                raise AcceptanceFailure("complex_placeholder", "complex candidate is a placeholder")
    if case == "protein_design":
        for request in requests:
            source = request.get("source_artifact")
            media_type = source.get("media_type") if isinstance(source, dict) else None
            if media_type not in {"chemical/x-pdb", "chemical/x-mmcif"}:
                raise AcceptanceFailure("protein_terminal_structure", "every protein-design terminal candidate must be a structure")
    if case == "antibody_denovo":
        for request in requests:
            lineage = request.get("transformation_lineage") or request.get("producer_provenance")
            if not isinstance(lineage, (list, dict)):
                raise AcceptanceFailure("antibody_iggm_lineage", "antibody candidate lacks IgGM freshness lineage")
            serialized = _canonical_bytes(lineage).decode("utf-8")
            if "iggm" not in serialized.lower() or "stale" in serialized.lower():
                raise AcceptanceFailure("antibody_stale_iggm", "antibody candidate does not prove fresh post-IgGM structure")
    return {"candidates": len(request_ids), "candidate_ids": sorted(request_ids)}


def _validate_cm(
    documents: Mapping[str, Any], records: Sequence[Mapping[str, Any]], expected: Mapping[str, Any], runtime: Mapping[str, Any]
) -> dict[str, Any]:
    receipts = _find_receipts(documents)
    if not receipts:
        raise AcceptanceFailure("cm_receipt", "conformational mapping lacks shared runtime receipt")
    for receipt in receipts:
        _validate_runtime_identity(receipt, runtime)
    artifact_hashes = expected.get("artifact_sha256")
    semantic_hashes = expected.get("semantic_sha256")
    if not isinstance(artifact_hashes, dict) or not artifact_hashes or not isinstance(semantic_hashes, dict) or not semantic_hashes:
        raise AcceptanceFailure("cm_expected_fixture", "CM expected artifact and semantic fixture hashes are required")
    by_source = {record.get("source_relative_path"): record.get("sha256") for record in records}
    for relative, digest in artifact_hashes.items():
        if by_source.get(relative) != digest:
            raise AcceptanceFailure("cm_artifact_parity", f"CM artifact hash mismatch: {relative}")
    by_basename = {PurePosixPath(path).name: document for path, document in documents.items()}
    for basename, digest in semantic_hashes.items():
        document = by_basename.get(basename)
        if document is None or _sha256(_canonical_bytes(document)) != digest:
            raise AcceptanceFailure("cm_semantic_parity", f"CM semantic hash mismatch: {basename}")
    return {"runtime_receipts": len(receipts), "artifact_hashes": len(artifact_hashes), "semantic_hashes": len(semantic_hashes)}


def _runtime_registry() -> dict[str, Any]:
    if os.fspath(API_ROOT) not in sys.path:
        sys.path.insert(0, os.fspath(API_ROOT))
    try:
        from services.frustrampnn.runtime import FRUSTRAMPNN_RUNTIME_IDENTITY
    except Exception as exc:
        raise AcceptanceFailure("runtime_registry_unavailable", f"canonical runtime registry unavailable: {exc}") from exc
    identity = FRUSTRAMPNN_RUNTIME_IDENTITY
    fields = (
        "sif_sha256",
        "checkpoint_id",
        "checkpoint_sha256",
        "executable_path",
        "executable_sha256",
        "source_commit",
    )
    record = {field: getattr(identity, field, None) for field in fields}
    if not all(isinstance(record[field], str) and record[field] for field in fields):
        raise AcceptanceFailure("runtime_registry_invalid", "canonical runtime identity is incomplete")
    configured_path = os.environ.get("BMS_FRUSTRAMPNN_SIF", str(identity.configured_sif_path))
    sif_path = Path(configured_path).expanduser().absolute()
    if os.fspath(sif_path) != identity.configured_sif_path:
        raise AcceptanceFailure("runtime_sif_path", "configured SIF path does not match the canonical registry")
    raw, _ = _open_regular_nofollow(sif_path)
    if _sha256(raw) != record["sif_sha256"]:
        raise AcceptanceFailure("runtime_sif_hash", "configured runtime SIF does not match canonical registry")
    record["configured_sif_path"] = os.fspath(sif_path)
    record["sif_size_bytes"] = len(raw)
    return record


def _spec_record(path: Path) -> dict[str, Any]:
    raw, file_stat = _open_regular_nofollow(path, max_bytes=MAX_JSON_BYTES)
    if not raw:
        raise AcceptanceFailure("spec_empty", "canonical Phase 6 specification is empty")
    return {
        "path": os.fspath(path),
        "size_bytes": len(raw),
        "sha256": _sha256(raw),
        "mode": stat.S_IMODE(file_stat.st_mode),
    }


def _prepare_root(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise AcceptanceFailure("stale_output_root", f"output root already exists; reuse is forbidden: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.mkdir(path, 0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        os.chmod(path, 0o700)


def _job_snapshots(client: ApiClient, jobs: Sequence[Mapping[str, Any]], case_dir: Path) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for job in jobs:
        job_id = job.get("id")
        if not isinstance(job_id, str):
            raise AcceptanceFailure("job_identity", "related job lacks an ID")
        quoted = urllib.parse.quote(job_id, safe="")
        surfaces: dict[str, Any] = {"job": _redact(dict(job))}
        for role, path in (
            ("logs", f"/api/jobs/{quoted}/logs?tail=20000"),
            ("stages", f"/api/jobs/{quoted}/stages"),
            ("results", f"/api/jobs/{quoted}/results"),
        ):
            try:
                surfaces[role] = _redact(_snapshot_api(client, path, allow_status=(200, 404)))
            except AcceptanceFailure as exc:
                raise AcceptanceFailure("api_evidence_surface", f"cannot collect {role} for {job_id}: {exc}") from exc
        relative = PurePosixPath("api", f"{job_id}.json").as_posix()
        raw = _canonical_bytes(surfaces)
        _write_exclusive(case_dir / relative, raw)
        snapshots.append(
            {
                "role": "authoritative_job_snapshot",
                "path": relative,
                "source_job_id": job_id,
                "size_bytes": len(raw),
                "sha256": _sha256(raw),
            }
        )
    return snapshots


def _submit_case(
    client: ApiClient,
    case: str,
    definition: Mapping[str, Any],
    cases_root: Path,
    chronology: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    submission = _strict_mapping(definition["submission"], field="submission")
    path = submission["path"]
    if "json" in submission:
        body = _canonical_bytes(submission["json"])
        content_type = "application/json"
        submission_record = {
            "method": "POST",
            "path": path,
            "body_size_bytes": len(body),
            "body_sha256": _sha256(body),
            "body": _redact(submission["json"]),
        }
    else:
        body, content_type, multipart_record = _multipart_body(submission["multipart"], cases_root)
        submission_record = {
            "method": "POST",
            "path": path,
            "body_size_bytes": len(body),
            "body_sha256": _sha256(body),
            "multipart": multipart_record,
        }
    launched_at = _now()
    response = client.request("POST", path, body=body, content_type=content_type, allow_status=(200, 201, 202))
    job_id = _extract_job_id(response.data)
    chronology.append(
        {
            "observed_at": launched_at,
            "kind": "submitted",
            "case": case,
            "job_id": job_id,
            "http_status": response.status,
        }
    )
    submission_record["response"] = _redact(response.data)
    submission_record["job_id"] = job_id
    return job_id, submission_record


def _case_semantics(
    case: str,
    jobs: Sequence[Mapping[str, Any]],
    documents: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    chronology: Sequence[Mapping[str, Any]],
    expected: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> dict[str, Any]:
    receipts = _find_receipts(documents)
    if case not in {"intentional_runtime_failure", "disabled_not_requested"}:
        if not receipts:
            raise AcceptanceFailure("receipt_missing", f"{case} lacks an execution receipt")
        for receipt in receipts:
            _validate_runtime_identity(receipt, runtime)
    if case == "scheduler_gpu":
        return _validate_scheduler_gpu(jobs, documents, chronology)
    if case == "one_ubq_1520":
        landscapes = _documents_named(documents, "frustrampnn_landscape_v1.json")
        if len(landscapes) != 1:
            raise AcceptanceFailure("ubq_landscape_count", "one_ubq_1520 requires exactly one landscape")
        return validate_one_ubq_1520(landscapes[0][1])
    if case == "exact_multichain_map":
        maps = _documents_named(documents, "frustrampnn_structure_map_v1.json")
        expected_rows = expected.get("structure_map_rows")
        if len(maps) != 1 or not isinstance(expected_rows, list):
            raise AcceptanceFailure("map_fixture", "exact_multichain_map requires one map and retained expected rows")
        return validate_exact_multichain_map(maps[0][1], expected_rows)
    if case in PARENT_CASES:
        return _validate_parent_case(case, jobs, documents)
    if case == "conformational_mapping":
        return _validate_cm(documents, records, expected, runtime)
    if case == "intentional_runtime_failure":
        failed_jobs = [job for job in jobs if _job_status(job) in FAILURE_STATES]
        results = [value for _, value in _documents_named(documents, "workflow_component_result_v1.json")]
        terminal = results[0] if len(results) == 1 else {}
        success_markers = [record["path"] for record in records if "complete" in str(record.get("source_relative_path", "")).lower()]
        persisted = [value for value in results if str(value.get("status", "")).lower() in SUCCESS_STATES]
        evidence = {
            "parent": failed_jobs[0] if failed_jobs else {},
            "child": failed_jobs[-1] if failed_jobs else {},
            "terminal_result": terminal,
            "success_markers": success_markers,
            "persisted_results": persisted,
        }
        validate_intentional_runtime_failure(evidence)
        return {"classified_failure": terminal.get("failure_class")}
    if case == "disabled_not_requested":
        parents = [job for job in jobs if not job.get("parent_job_id")]
        terminals = [value for path, value in documents.items() if "terminal_manifest" in PurePosixPath(path).name]
        model_tasks = [
            record["path"]
            for record in records
            if "CanonicalFrustraMPNN" in str(record.get("source_relative_path", ""))
        ]
        bundles = [record["path"] for record in records if record.get("role") == "terminal_bundle"]
        validate_disabled_not_requested(
            {
                "parent": parents[0] if len(parents) == 1 else {},
                "terminal_manifest": terminals[0] if len(terminals) == 1 else {},
                "model_tasks": model_tasks,
                "result_bundles": bundles,
            }
        )
        return {"status": "not_requested", "candidate_count": 0}
    raise AcceptanceFailure("case_unhandled", f"no validator exists for {case}")


def run_case(
    *,
    case: str,
    definition: Mapping[str, Any],
    definition_record: Mapping[str, Any],
    client: ApiClient,
    cases_root: Path,
    results_root: Path,
    evidence_root: Path,
    git_record: Mapping[str, Any],
    spec_record: Mapping[str, Any],
    runtime_record: Mapping[str, Any],
    harness_argv: Sequence[str],
) -> dict[str, Any]:
    case_id = f"{case}-{uuid.uuid4()}"
    case_dir = evidence_root / case_id
    os.mkdir(case_dir, 0o700)
    chronology: list[dict[str, Any]] = []
    started_at = _now()
    submitted_id, submission_record = _submit_case(client, case, definition, cases_root, chronology)
    terminal = _poll_job(
        client,
        submitted_id,
        timeout_seconds=int(definition["timeout_seconds"]),
        chronology=chronology,
    )
    jobs = _list_related_jobs(client, terminal, submitted_id)
    if not jobs:
        raise AcceptanceFailure("related_jobs", f"{case} produced no authoritative related job records")
    expected_failure = case == "intentional_runtime_failure"
    expected_disabled = case == "disabled_not_requested"
    terminal_status = _job_status(terminal)
    if expected_failure:
        if terminal_status not in FAILURE_STATES:
            raise AcceptanceFailure("failure_did_not_fail", "intentional runtime failure unexpectedly passed")
    elif terminal_status not in SUCCESS_STATES:
        raise AcceptanceFailure("case_job_failed", f"{case} terminated {terminal_status}")
    api_records = _job_snapshots(client, jobs, case_dir)
    output_records = _retain_managed_outputs(jobs, results_root, case_dir)
    # Re-open every retained byte exactly once after the complete closure exists.
    # The returned JSON objects are the only objects used by semantic validators.
    documents = validate_packet_inventory(
        case_dir,
        [*api_records, *output_records],
        return_documents=True,
    )
    canonical_validator = _product_schema_validator()
    for path, document in documents.items():
        schema_key = SCHEMA_BY_BASENAME.get(PurePosixPath(path).name)
        if schema_key:
            canonical_validator(schema_key, document)
    role_counts: dict[str, int] = {}
    for record in output_records:
        role = str(record["role"])
        role_counts[role] = role_counts.get(role, 0) + 1
    common_roles = {
        "report",
        "timeline",
        "trace",
        "dag",
        "nextflow_log",
        "stdout",
        "stderr",
        "terminal_manifest",
    }
    component_roles = {
        "request",
        "source",
        "structure_map",
        "landscape",
        "summary",
        "receipt",
        "result",
        "artifact_manifest",
    }
    required_roles = common_roles
    if case not in {"intentional_runtime_failure", "disabled_not_requested"}:
        required_roles = common_roles | component_roles
    missing_roles = sorted(role for role in required_roles if role_counts.get(role, 0) == 0)
    if missing_roles:
        raise AcceptanceFailure(
            "evidence_role_missing",
            f"{case} retained output lacks required evidence roles: {','.join(missing_roles)}",
        )
    semantics = _case_semantics(
        case,
        jobs,
        documents,
        output_records,
        chronology,
        _strict_mapping(definition["expected"], field="expected"),
        runtime_record,
    )
    # Disabled cases still require report/timeline/trace/DAG/log/terminal manifest,
    # but correctly have no model bundle.  All other success cases require bundle closure.
    if not expected_failure and not expected_disabled:
        bundle_paths = [record for record in output_records if record.get("role") == "terminal_bundle"]
        if not bundle_paths:
            raise AcceptanceFailure("terminal_bundle_missing", f"{case} has no terminal result bundle")
    packet = {
        "schema_name": PACKET_SCHEMA_NAME,
        "schema_version": 1,
        "status": "PASS",
        "case": case,
        "case_id": case_id,
        "started_at": started_at,
        "ended_at": _now(),
        "no_resume": True,
        "command": {"argv": list(harness_argv), "executable": os.fspath(Path(sys.executable).resolve())},
        "config": dict(definition_record),
        "git": dict(git_record),
        "spec": dict(spec_record),
        "runtime": dict(runtime_record),
        "submission": submission_record,
        "jobs": [_redact(dict(job)) for job in jobs],
        "gpu_chronology": chronology,
        "semantics": semantics,
        "files": sorted([*api_records, *output_records], key=lambda value: value["path"]),
    }
    packet_path = evidence_root.parent / "packets" / f"{case}.json"
    _write_canonical_exclusive(packet_path, packet)
    return {
        "case": case,
        "case_id": case_id,
        "status": "PASS",
        "packet": packet_path.relative_to(evidence_root.parent).as_posix(),
        "packet_size_bytes": packet_path.stat().st_size,
        "packet_sha256": _sha256(packet_path.read_bytes()),
        "retained_files": len(packet["files"]),
    }


def _failure_record(args: argparse.Namespace | None, failure: AcceptanceFailure) -> dict[str, Any]:
    return {
        "schema_name": FAILURE_SCHEMA_NAME,
        "schema_version": 1,
        "status": "INCOMPLETE_FAILURE",
        "pass": False,
        "failed_at": _now(),
        "failure": {
            "code": failure.code,
            "message": str(failure),
            "details": _redact(failure.details),
        },
        "requested_cases": list(args.cases) if args is not None else [],
        "no_resume": bool(args and args.no_resume),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args: argparse.Namespace | None = None
    output_prepared = False
    work_root: Path | None = None
    try:
        args = parse_cli(argv)
        output_root: Path = args.output_root
        if _is_within(output_root, REPO_ROOT) or output_root == REPO_ROOT:
            raise AcceptanceFailure("output_inside_repo", "output root must be outside the source repository")
        # Cleanliness is checked before any output is created.
        git_record = require_clean_committed_tree(REPO_ROOT)
        _prepare_root(output_root)
        output_prepared = True
        evidence_root = output_root / f"evidence-{uuid.uuid4()}"
        packets_root = output_root / "packets"
        os.mkdir(evidence_root, 0o700)
        os.mkdir(packets_root, 0o700)
        work_root = Path(tempfile.mkdtemp(prefix="bms-phase6-work-"))
        os.chmod(work_root, 0o700)
        cases_root_text = os.environ.get("BMS_PHASE6_CASES_ROOT")
        results_root_text = os.environ.get("BMS_PHASE6_RESULTS_ROOT")
        spec_path_text = os.environ.get("BMS_PHASE6_SPEC_PATH")
        api_base = os.environ.get("BMS_PHASE6_API_BASE")
        missing = [
            name
            for name, value in (
                ("BMS_PHASE6_CASES_ROOT", cases_root_text),
                ("BMS_PHASE6_RESULTS_ROOT", results_root_text),
                ("BMS_PHASE6_SPEC_PATH", spec_path_text),
                ("BMS_PHASE6_API_BASE", api_base),
            )
            if not value
        ]
        if missing:
            raise AcceptanceFailure("environment_missing", f"required safe environment is missing: {','.join(missing)}")
        cases_root = Path(str(cases_root_text)).expanduser().absolute()
        results_root = Path(str(results_root_text)).expanduser().absolute()
        if cases_root.is_symlink() or not cases_root.is_dir():
            raise AcceptanceFailure("cases_root", "BMS_PHASE6_CASES_ROOT must be a real directory")
        if results_root.is_symlink() or not results_root.is_dir():
            raise AcceptanceFailure("results_root", "BMS_PHASE6_RESULTS_ROOT must be a real directory")
        spec_record = _spec_record(Path(str(spec_path_text)).expanduser().absolute())
        runtime_record = _runtime_registry()
        client = ApiClient(str(api_base), _load_auth_token(os.environ.get("BMS_PHASE6_AUTH_FILE")))
        results: list[dict[str, Any]] = []
        command_argv = [os.fspath(Path(sys.argv[0]).absolute()), *(argv if argv is not None else sys.argv[1:])]
        for case in args.cases:
            definition, definition_record = load_case_definition(case, cases_root)
            result = run_case(
                case=case,
                definition=definition,
                definition_record=definition_record,
                client=client,
                cases_root=cases_root,
                results_root=results_root,
                evidence_root=evidence_root,
                git_record=git_record,
                spec_record=spec_record,
                runtime_record=runtime_record,
                harness_argv=command_argv,
            )
            results.append(result)
        if len(results) != len(args.cases) or any(result.get("status") != "PASS" for result in results):
            raise AcceptanceFailure("overall_incomplete", "not every requested case produced PASS")
        index = {
            "schema_name": INDEX_SCHEMA_NAME,
            "schema_version": 1,
            "status": "PASS",
            "pass": True,
            "completed_at": _now(),
            "requested_cases": list(args.cases),
            "case_count": len(results),
            "no_resume": True,
            "git": git_record,
            "spec": spec_record,
            "runtime": runtime_record,
            "evidence_root": evidence_root.relative_to(output_root).as_posix(),
            "cases": results,
        }
        _write_canonical_exclusive(output_root / "phase6_index.json", index)
        print(_canonical_bytes(index).decode("utf-8"))
        return 0
    except AcceptanceFailure as failure:
        record = _failure_record(args, failure)
        if args is not None:
            output_root = args.output_root
            try:
                if not output_prepared and not output_root.exists() and not output_root.is_symlink():
                    _prepare_root(output_root)
                    output_prepared = True
                if output_prepared:
                    _write_canonical_exclusive(output_root / "phase6_incomplete_failure.json", record)
            except (AcceptanceFailure, OSError):
                pass
        print(_canonical_bytes(record).decode("utf-8"), file=sys.stderr)
        return 1
    except Exception as exc:
        failure = AcceptanceFailure("unhandled_internal_error", f"unhandled harness error: {type(exc).__name__}: {exc}")
        record = _failure_record(args, failure)
        if args is not None and output_prepared:
            try:
                _write_canonical_exclusive(args.output_root / "phase6_incomplete_failure.json", record)
            except (AcceptanceFailure, OSError):
                pass
        print(_canonical_bytes(record).decode("utf-8"), file=sys.stderr)
        return 1
    finally:
        if work_root is not None:
            shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
