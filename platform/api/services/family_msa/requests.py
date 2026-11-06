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
    if 'managed_operation' in payload:
        from services.family_msa.operations import ManagedOperation
        if set(payload) != {'managed_operation'}:
            raise ValueError('managed operation cannot contain alignment settings')
        operation = ManagedOperation.model_validate(payload['managed_operation'])
        model = MODEL_PROTEIN if operation.request.molecule == 'protein' else MODEL_NUCLEIC
        return model, {'managed_operation': operation.model_dump(mode='json')}
    requested = payload.get('_family_msa_requested', payload)
    request = AlignmentRequest.model_validate({key: value for key, value in payload.items() if key != '_family_msa_requested'})
    model = MODEL_PROTEIN if request.roster.molecule == 'protein' else MODEL_NUCLEIC
    return model, {**request.model_dump(mode='json'), '_family_msa_requested': requested}
