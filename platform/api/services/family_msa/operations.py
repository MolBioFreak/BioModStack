"""Typed managed post-processing on the ordinary family-MSA CPU Job owner."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class OperationInputs(BaseModel):
    model_config = ConfigDict(extra='forbid')

    def __getitem__(self, key):
        return getattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __contains__(self, key):
        return key in type(self).model_fields


class ComparisonInput(OperationInputs):
    left_receipt_id: str = Field(min_length=1)
    left_artifact_id: str = Field(min_length=1)
    right_receipt_id: str = Field(min_length=1)
    right_artifact_id: str = Field(min_length=1)


class EnsembleSupportInput(OperationInputs):
    receipt_id: str = Field(min_length=1)
    reference_artifact_id: str = Field(min_length=1)
    member_artifact_ids: list[str]


class ExportInput(OperationInputs):
    receipt_id: str = Field(min_length=1)
    shared_state_ids: list[str] = Field(default_factory=list)


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation: Literal['compare', 'ensemble-support', 'export']
    molecule: Literal['protein', 'dna', 'rna']
    domain_experiment_id: str = Field(min_length=1)
    name: str = Field(default='Alignment operation', min_length=1, max_length=255)
    inputs: ComparisonInput | EnsembleSupportInput | ExportInput

    @model_validator(mode='after')
    def validate_inputs(self):
        kind = {'compare': ComparisonInput, 'ensemble-support': EnsembleSupportInput, 'export': ExportInput}[self.operation]
        if not isinstance(self.inputs, kind):
            raise ValueError('inputs do not match the selected operation')
        if isinstance(self.inputs, ExportInput) and len(set(self.inputs.shared_state_ids)) != len(self.inputs.shared_state_ids):
            raise ValueError('duplicate shared state selection')
        return self


class ManagedOperation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request: OperationRequest
    project_id: str = Field(min_length=1)
    request_dataset_id: str = Field(min_length=1)
    shared_states: list[dict] = Field(default_factory=list)
    request_revision_id: str | None = None


class OperationStatus(BaseModel):
    job_id: str
    project_id: str
    operation: str
    status: str
    queue_status: str | None
    request_dataset_id: str
    result_url: str
    artifact_url: str | None
    error_message: str | None


async def bind_request(session, *, request, project_id, shared_states, job_id):
    """Retain the exact request revision under its existing Project/domain owner."""
    from experiment_services import create_dataset, save_dataset_revision
    head = await create_dataset(session, project_id, request.name,
        'family_msa.operation.v1', experiment_id=request.domain_experiment_id)
    operation = ManagedOperation(request=request, project_id=project_id,
        shared_states=shared_states, request_dataset_id=head.aggregate_id)
    revision = await save_dataset_revision(session, head.aggregate_id,
        {'schema': 'bms.family-msa.operation.v1', 'job_id': job_id,
         **operation.model_dump(mode='json', exclude={'request_revision_id'})},
        expected_head_generation=head.head_generation)
    operation.request_revision_id = revision.resource_id
    await session.commit()
    return operation


def status(job):
    operation = ManagedOperation.model_validate(job.params['managed_operation'])
    url = f'/api/family-msa/projects/{operation.project_id}/operations/{job.id}'
    return OperationStatus(job_id=job.id, project_id=operation.project_id,
        operation=operation.request.operation, status=job.status, queue_status=job.queue_status,
        request_dataset_id=operation.request_dataset_id, result_url=url,
        artifact_url=url + '/artifact' if job.status == 'completed' else None,
        error_message=job.error_message)
