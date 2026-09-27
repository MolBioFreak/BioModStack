"""Ordinary LigandMPNN native rows, files and settings; no diagnostic projection."""
import hashlib
import json
from paths import resolve_runtime_data_path
from services.ligandmpnn_interface_publication import regular_bytes

from services.ligandmpnn_design import (
    MODES, prepare_design_request, read_design_result, science_params,
)

from services.caliby_native_publication import (
    publication_root, native_files, native_artifacts, artifact_handles,
)

DIRECTORY = "ligandmpnn_design"


def _read(job, root):
    document = read_design_result(root / DIRECTORY)
    if job.model_id != "ligandmpnn" or job.mode not in MODES or document["mode"] != job.mode:
        raise ValueError("LigandMPNN native publication does not match Job mode")
    science = science_params(job.mode, job.params or {})
    expected = prepare_design_request(job.mode, science)
    request = document["request"]
    if {key: value for key, value in request.items() if key != "source"} != expected:
        raise ValueError("LigandMPNN publication scientific request differs from Job settings")

    # The returned digest alone is not controller source authority. Use only
    # retained Job inputs, never reopen the original scientific source path.
    # Historical cleanup is permitted: unavailable evidence is not a new gate.
    retained = None
    request_path = (job.params or {}).get("ligandmpnn_design_request")
    if request_path:
        try:
            retained = json.loads(regular_bytes(resolve_runtime_data_path(request_path)))
        except (OSError, ValueError):
            pass  # Removed historical input evidence is not a failure.
    if isinstance(retained, dict):
        if {key: value for key, value in retained.items() if key != "source"} != expected:
            raise ValueError("LigandMPNN retained scientific request differs from Job settings")
    else:
        retained = {}

    digest = document["source_sha256"]
    independent_hashes = []
    for source, independent in ((request.get("source"), False),
                                (retained.get("source"), True)):
        if isinstance(source, dict):
            if source.get("requested_path") is not None and source["requested_path"] != science["target_pdb"]:
                raise ValueError("LigandMPNN publication requested source differs from Job source")
            if source.get("sha256") is not None:
                if source["sha256"] != digest:
                    raise ValueError("LigandMPNN publication source digest differs from declared or retained source")
                if independent:
                    independent_hashes.append(source["sha256"])
    input_path = (job.params or {}).get("ligandmpnn_design_input")
    if input_path:
        try:
            independent_hashes.append(hashlib.sha256(regular_bytes(resolve_runtime_data_path(input_path))).hexdigest())
        except (OSError, ValueError):
            pass
    if any(value != digest for value in independent_hashes):
        raise ValueError("LigandMPNN publication source digest differs from retained input")
    if any(row["source_sha256"] != digest for row in document["records"]):
        raise ValueError("LigandMPNN publication row source digest differs from document")
    return document


async def publish_native_results(job, root, session):
    root = publication_root(job, root)
    files = native_files(root, DIRECTORY)
    _read(job, root)
    await native_artifacts(job, root, DIRECTORY, files, session, publish=True)
    return 0


async def read_published_native_results(job, session):
    root = publication_root(job)
    files = native_files(root, DIRECTORY)
    artifacts = await native_artifacts(job, root, DIRECTORY, files, session)
    document = _read(job, root)
    handles = artifact_handles(root, DIRECTORY, files, artifacts)
    by_path = {item["path"]: item for item in handles}
    return {**document, "schema": document["contract"], "job_id": job.id, "artifacts": handles,
            "records": [{**row, "artifacts": [{**item, **by_path[item["path"]]}
                                                for item in row["artifacts"]]}
                        for row in document["records"]]}
