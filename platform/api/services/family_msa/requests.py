"""Single-source projection of the standalone native request contract.

These helpers do not submit or schedule jobs.  The canonical Job owner must
persist the returned request and select its CPU-only scheduler path.
"""
from bms_alignment import AlignmentRequest

MODEL_PROTEIN = 'family_msa_protein'
MODEL_NUCLEIC = 'family_msa_nucleic'
MODE = 'family_msa'


def request_schema() -> dict:
    return AlignmentRequest.model_json_schema()


def prepare_request(payload: dict) -> tuple[str, dict]:
    request = AlignmentRequest.model_validate(payload)
    model = MODEL_PROTEIN if request.roster.molecule == 'protein' else MODEL_NUCLEIC
    return model, request.model_dump(mode='json')
