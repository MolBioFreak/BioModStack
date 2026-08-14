"""Required source closure for canonical ConforNets execution attestations."""

from __future__ import annotations

from typing import Final

COMMON_UPSTREAM_SOURCES: Final = (
    "preprocess.py",
    "confornet/preprocess.py",
    "confornet/data.py",
    "confornet/utils/io.py",
    "confornet/utils/dist.py",
    "confornet/utils/cm_coordinate_ledger.py",
)

TASK_UPSTREAM_SOURCES: Final = {
    "diversity": (
        "scripts/run_diversity.py",
        "confornet/inference/diversity.py",
        "confornet/core/confornet.py",
        "confornet/core/diffusion.py",
        "confornet/core/losses.py",
        "confornet/core/of3.py",
        "confornet/core/trunk.py",
        "confornet/utils/align.py",
        "confornet/utils/cli.py",
        "confornet/utils/confidence.py",
        "confornet/utils/csv_io.py",
    ),
    "mse": (
        "scripts/run_mse_training.py",
        "confornet/inference/mse_training.py",
        "confornet/inference/transfer.py",
        "confornet/core/confornet.py",
        "confornet/core/diffusion.py",
        "confornet/core/losses.py",
        "confornet/core/of3.py",
        "confornet/core/trunk.py",
        "confornet/utils/align.py",
        "confornet/utils/cli.py",
        "confornet/utils/confidence.py",
        "confornet/utils/csv_io.py",
    ),
    "transfer": (
        "scripts/run_transfer.py",
        "confornet/inference/transfer.py",
        "confornet/core/confornet.py",
        "confornet/core/diffusion.py",
        "confornet/core/of3.py",
        "confornet/core/trunk.py",
        "confornet/utils/align.py",
        "confornet/utils/cli.py",
        "confornet/utils/confidence.py",
        "confornet/utils/csv_io.py",
    ),
}

BMS_WRAPPER_SOURCES: Final = {
    "diversity": (
        "biomodstack/confornets_source_closure.py",
        "biomodstack/run_confornets_inference.py",
        "biomodstack/run_diversity_bounded.py",
    ),
    "mse": (
        "biomodstack/confornets_source_closure.py",
        "biomodstack/run_confornets_inference.py",
    ),
    "transfer": (
        "biomodstack/confornets_source_closure.py",
        "biomodstack/run_confornets_inference.py",
    ),
}


def required_source_paths(task: str) -> tuple[str, ...]:
    """Return the exact ordered source-path closure for one task."""
    if task not in TASK_UPSTREAM_SOURCES:
        raise ValueError(f"unsupported ConforNets task: {task!r}")
    return (
        *COMMON_UPSTREAM_SOURCES,
        *TASK_UPSTREAM_SOURCES[task],
        *BMS_WRAPPER_SOURCES[task],
    )


def validate_source_evidence(
    task: str,
    sources: object,
    commands: object,
) -> tuple[str, ...]:
    """Require the exact task source set and both ordered child drivers."""
    expected = required_source_paths(task)
    if not isinstance(sources, list):
        raise ValueError("ConforNets source evidence is missing")
    observed = tuple(
        source.get("relative_path") if isinstance(source, dict) else None
        for source in sources
    )
    if observed != expected:
        raise ValueError("ConforNets source evidence does not equal the required closure")
    if (
        not isinstance(commands, list)
        or len(commands) != 2
        or any(not isinstance(command, list) or len(command) < 2 for command in commands)
    ):
        raise ValueError("ConforNets ordered command evidence is missing")
    preprocess_child = str(commands[0][1])
    if not preprocess_child.endswith("/preprocess.py"):
        raise ValueError("ConforNets preprocessing driver is not part of the source closure")
    child = str(commands[1][1])
    required_child = {
        "diversity": "/run_diversity_bounded.py",
        "mse": "/scripts/run_mse_training.py",
        "transfer": "/scripts/run_transfer.py",
    }[task]
    if not child.endswith(required_child):
        raise ValueError("ConforNets inference driver is not part of the source closure")
    return expected
