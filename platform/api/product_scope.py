"""Public surface of the local MolBio / NGS distribution.

The shared job engine remains the persistence and scheduler authority. Only
its transport and NGS lifecycle routes are exposed by this installation.
"""
from fastapi import APIRouter, FastAPI

NGS_MODEL_IDS = frozenset({
    "nanopore", "ont_fastq_qc", "ont_plasmid_qc", "ont_construct_screening",
    "wf_clone_validation",
})

_SHARED_PATHS = {
    "jobs": frozenset({
        "", "/execution-plan/preview", "/{job_id}", "/{job_id}/permanent",
        "/{job_id}/execution-settings", "/{job_id}/resubmit", "/{job_id}/reingest",
        "/{job_id}/stage-complete", "/{job_id}/stage-terminal",
        "/{job_id}/stage-gates/{stage}/open", "/{job_id}/stage-gates",
        "/{parent_id}/children/status", "/{parent_id}/children/mark-aggregated",
        "/{job_id}/stage-start", "/{job_id}/stages", "/{job_id}/resume",
        "/{job_id}/logs", "/{job_id}/remote-artifacts", "/{job_id}/remote-artifacts/download",
        "/{job_id}/remote-diagnostics/pull", "/{job_id}/remote-results/pull",
    }),
    "models": frozenset({"", "/categories", "/{model_id}", "/{model_id}/modes"}),
    "files": frozenset({"/browse", "/upload", "/download/{file_path:path}", "/stream/{file_path:path}"}),
}


def include_shared_routes(app: FastAPI, router: APIRouter, *, prefix: str, family: str) -> None:
    retained = APIRouter()
    retained.routes.extend(route for route in router.routes if route.path in _SHARED_PATHS[family])
    app.include_router(retained, prefix=prefix, tags=[family])
