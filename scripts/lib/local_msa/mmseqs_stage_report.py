from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass
class MmseqsStageReport:
    stage: str
    module: str
    binary: str
    binary_kind: str
    target_db: str | None
    argv: list[str]
    uses_gpu_flag: bool
    uses_gpu_server: bool
    gpu_server_wait_timeout: int | None
    prefilter_mode: int | None
    db_load_mode: int | None
    split_count: int | None
    split_mode: int | None
    threads: int | None
    elapsed_seconds: float | None = None
    returncode: int | None = None
    fallback_from_gpu: bool = False
    fallback_reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def classify_mmseqs_binary(mmseqs_bin: str | Path) -> str:
    text = str(mmseqs_bin).lower()
    if "gpu" in text or "blackwell" in text or "cuda" in text:
        return "gpu"
    if "cpu" in text:
        return "cpu"
    name = Path(str(mmseqs_bin)).name.lower()
    if name == "mmseqs":
        return "unknown"
    return "unknown"


def _get_option(args: list[str], name: str) -> str | None:
    for idx, token in enumerate(args):
        if token == name:
            if idx + 1 < len(args):
                return args[idx + 1]
            return None
        if token.startswith(name + "="):
            return token.split("=", 1)[1]
    return None


def _get_int_option(args: list[str], name: str) -> int | None:
    value = _get_option(args, name)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _target_db_from_argv(argv: list[str]) -> str | None:
    if len(argv) < 3:
        return None
    module = argv[0]
    if module in {"search", "expandaln", "align", "filterresult", "result2msa"}:
        return Path(argv[2]).name
    if module in {"splitdb", "createdb"} and len(argv) >= 2:
        return Path(argv[1]).name
    return None


def command_report(stage: str, mmseqs_bin: str | Path, params: Iterable[Any]) -> MmseqsStageReport:
    argv = [str(part) for part in params]
    module = argv[0] if argv else "unknown"
    return MmseqsStageReport(
        stage=str(stage),
        module=module,
        binary=str(mmseqs_bin),
        binary_kind=classify_mmseqs_binary(mmseqs_bin),
        target_db=_target_db_from_argv(argv),
        argv=argv,
        uses_gpu_flag=_get_option(argv, "--gpu") == "1",
        uses_gpu_server=_get_option(argv, "--gpu-server") == "1",
        gpu_server_wait_timeout=_get_int_option(argv, "--gpu-server-wait-timeout"),
        prefilter_mode=_get_int_option(argv, "--prefilter-mode"),
        db_load_mode=_get_int_option(argv, "--db-load-mode"),
        split_count=_get_int_option(argv, "--split"),
        split_mode=_get_int_option(argv, "--split-mode"),
        threads=_get_int_option(argv, "--threads"),
    )


def stage_uses_gpu(report: dict[str, Any] | MmseqsStageReport) -> bool:
    if isinstance(report, MmseqsStageReport):
        return bool(report.uses_gpu_flag or report.uses_gpu_server)
    return bool(report.get("uses_gpu_flag") or report.get("uses_gpu_server"))


def effective_gpu_stages(reports: Iterable[dict[str, Any] | MmseqsStageReport]) -> list[str]:
    stages: list[str] = []
    seen: set[str] = set()
    for report in reports:
        if not stage_uses_gpu(report):
            continue
        stage = report.stage if isinstance(report, MmseqsStageReport) else str(report.get("stage"))
        if stage and stage not in seen:
            seen.add(stage)
            stages.append(stage)
    return stages
