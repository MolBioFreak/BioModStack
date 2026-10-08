"""Project Manager verified-adapter and presentation services."""

from services.global_experiments.adapters import AdapterError, registry
from services.global_experiments.read_models import build_project_manager_read_model
from services.global_experiments.receipts import attach_verified_entity
from services.global_experiments.result_surfaces import result_surface_for_receipt

__all__ = [
    "AdapterError",
    "attach_verified_entity",
    "build_project_manager_read_model",
    "registry",
    "result_surface_for_receipt",
]
