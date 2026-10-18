"""Closed Jobs linkage projection; no publication receipts or result counts.

Select JSON fragments rather than materializing ORM Jobs. Presence/type columns
preserve absent, null and malformed legacy evidence for the existing receiver.
"""
from sqlalchemy import case, func

from database import Job
from schemas import BinderNavigationJob

TOP_KEYS = (
    "id", "name", "model_id", "status", "created_at", "mode", "stage_family",
    "parent_job_id", "lineage_root_job_id", "selection_source_job_id", "source_stage_job_id",
)
PARAM_KEYS = (
    "lineage_root_job_id", "iteration_source_root_job_id", "selection_source_job_id",
    "source_stage_job_id", "iteration_source_job_id", "selection_source_type",
    "sequence_name", "complex_components",
)
COMPONENT_KEYS = ("sequence", "role", "count", "copies")
STEP_KEYS = ("source_ref", "source_job_id", "retry_of_job_id", "stage", "input_components")


def _closed(value, keys):
    return {key: value[key] for key in keys if key in value} if isinstance(value, dict) else value


def _components(value):
    return [_closed(item, COMPONENT_KEYS) for item in value] if isinstance(value, list) else value


def navigation_columns():
    columns = [getattr(Job, key) for key in TOP_KEYS]
    for name, keys in (("params", PARAM_KEYS), ("provenance", ("binder_round_step",))):
        column = getattr(Job, name)
        columns.extend((func.json_type(column).label(name + "_type"),
                        case((func.json_type(column) != "object", column), else_=None).label(name + "_raw")))
        for key in keys:
            columns.extend((column[key].label(name + "_" + key),
                            func.json_type(column, "$." + key).label(name + "_" + key + "_type")))
    return columns


def navigation_job(row):
    data = {key: row[key] for key in TOP_KEYS}
    for name, keys in (("params", PARAM_KEYS), ("provenance", ("binder_round_step",))):
        data[name] = ({key: row[name + "_" + key] for key in keys
                       if row[name + "_" + key + "_type"] is not None}
                      if row[name + "_type"] == "object" else row[name + "_raw"])
    params = data["params"]
    if isinstance(params, dict) and "complex_components" in params:
        params["complex_components"] = _components(params["complex_components"])
    provenance = data["provenance"]
    if isinstance(provenance, dict) and "binder_round_step" in provenance:
        step = _closed(provenance["binder_round_step"], STEP_KEYS)
        if isinstance(step, dict):
            if "source_ref" in step:
                step["source_ref"] = _closed(step["source_ref"], ("job_id", "native_record_id"))
            if "input_components" in step:
                step["input_components"] = _components(step["input_components"])
        provenance["binder_round_step"] = step
    return BinderNavigationJob(**data)
