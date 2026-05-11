from __future__ import annotations

import importlib
import logging
import warnings

from pydantic.warnings import PydanticDeprecatedSince20


PYDANTIC_SCHEMA_MODULES = [
    "schemas",
    "routers.assay_analytics",
    "routers.designs",
    "routers.molbio_ops",
    "routers.nucleotide_sequences",
    "routers.user_sequences",
    "routers.user_templates",
]


def test_schema_modules_do_not_emit_pydantic_v2_config_deprecations() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", PydanticDeprecatedSince20)
        for module_name in PYDANTIC_SCHEMA_MODULES:
            importlib.import_module(module_name)


def test_missing_nvidia_smi_is_optional_gpu_metadata_info(monkeypatch, caplog) -> None:
    from services import gpu_metadata

    def missing_nvidia_smi(*args, **kwargs):  # noqa: ANN002, ANN003
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(gpu_metadata.subprocess, "run", missing_nvidia_smi)

    with caplog.at_level(logging.WARNING, logger=gpu_metadata.__name__):
        discovered = gpu_metadata.discover_gpus()

    assert discovered == {}
    assert "nvidia-smi not found" not in caplog.text
    assert "No GPUs discovered" not in caplog.text
