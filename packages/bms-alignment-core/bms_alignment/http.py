"""Small typed client for the existing BMS model and Job HTTP routes.

The server remains authoritative for admission, project ownership, and execution.
"""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .models import AlignmentRequest


class ModelMode(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: str
    parameter_schema: dict[str, Any] | None = None


class ModelInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: str
    modes: list[ModelMode]


class JobSubmission(BaseModel):
    """The alignment-specific subset of the existing JobCreate wire schema."""
    model_config = ConfigDict(extra='forbid')
    name: str
    model_id: Literal['family_msa_protein', 'family_msa_nucleic']
    mode: Literal['family_msa'] = 'family_msa'
    params: AlignmentRequest
    launch_context_id: str | None = None
    execution_target_id: str | None = None
    execution_plan_approval: str | None = None
    execution_policy: dict[str, Any] | None = None


class JobPreview(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    schema_name: Literal['bms.job.execution-preview.v1'] = Field(alias='schema')
    approval_digest: str
    admissible: bool
    request: dict[str, Any]
    plan: dict[str, Any]
    input_identities: list[dict[str, Any]]
    generated_inputs: list[dict[str, Any]]
    declared_expansions: list[dict[str, Any]] = Field(default_factory=list)
    deferred_preparation: list[str]
    blockers: list[dict[str, Any]]


class Job(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: str
    name: str
    status: str
    model_id: str
    mode: str


class ImportReceipt(BaseModel):
    receipt_id: str
    replayed: bool
    shared_state: Literal['retained_unmapped', 'imported', 'imported_with_unresolved', 'none']
    shared_states: list[dict[str, Any]] = Field(default_factory=list)


class ClosedRequest(BaseModel):
    """HTTP wire contracts; server validation remains authoritative."""
    model_config = ConfigDict(extra='forbid', populate_by_name=True)


class ComparisonInput(ClosedRequest):
    left_receipt_id: str = Field(min_length=1)
    left_artifact_id: str = Field(min_length=1)
    right_receipt_id: str = Field(min_length=1)
    right_artifact_id: str = Field(min_length=1)


class EnsembleSupportInput(ClosedRequest):
    receipt_id: str = Field(min_length=1)
    reference_artifact_id: str = Field(min_length=1)
    member_artifact_ids: list[str]


class BrowserExport(ClosedRequest):
    shared_state_ids: list[str] = Field(default_factory=list)


class FreezeRequest(ClosedRequest):
    ordinals: list[Annotated[int, Field(strict=True)]] | None = None


class ResolveRequest(ClosedRequest):
    kind: Literal['molecular', 'design']
    ordinal: int
    start: int | None = None
    end: int | None = None
    chain: str | None = None


class AlignmentImport(ClosedRequest):
    format: Literal['fasta', 'stockholm', 'clustal', 'a3m']
    molecule: Literal['protein', 'dna', 'rna']
    replay_key: str = Field(min_length=1, max_length=255)


class ArtifactColumns(ClosedRequest):
    scope: Literal['artifact_columns']
    artifact_id: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode='after')
    def ordered(self):
        if self.end < self.start:
            raise ValueError('end must not precede start')
        return self


class SourcePositions(ClosedRequest):
    scope: Literal['source_positions']
    source_member_id: str = Field(min_length=1)
    occurrence_id: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode='after')
    def ordered(self):
        if self.end < self.start:
            raise ValueError('end must not precede start')
        return self


class Mark(ClosedRequest):
    id: str = Field(min_length=1)
    label: str
    coordinate: Annotated[ArtifactColumns | SourcePositions, Field(discriminator='scope')]
    note: str = ''
    provenance: dict[str, Any] = Field(default_factory=dict)


class View(ClosedRequest):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    hidden_row_ids: list[str] = Field(default_factory=list)
    row_order: list[str] = Field(default_factory=list)
    selected_marks: list[str] = Field(default_factory=list)
    viewer: str | None = None
    selected_row_id: str | None = None
    selected_column: int | None = Field(default=None, ge=0)


class Comparison(ClosedRequest):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    left_artifact_id: str = Field(min_length=1)
    right_artifact_id: str = Field(min_length=1)
    method: str = Field(min_length=1)
    report: dict[str, Any]
    provenance: dict[str, Any] = Field(default_factory=dict)


class SharedState(ClosedRequest):
    schema_name: Literal['bms.family-msa.shared-state.v1'] = Field(default='bms.family-msa.shared-state.v1', alias='schema')
    marks: list[Mark] = Field(default_factory=list)
    views: list[View] = Field(default_factory=list)
    comparisons: list[Comparison] = Field(default_factory=list)


class SharedCreate(ClosedRequest):
    name: str = Field(min_length=1, max_length=255)
    domain_experiment_id: str = Field(min_length=1, max_length=128)
    state: SharedState


class SharedSave(ClosedRequest):
    expected_head_generation: int = Field(ge=0, strict=True)
    state: SharedState


class SharedRevision(BaseModel):
    id: str
    project_id: str
    head_generation: int
    revision_id: str
    revision_number: int
    payload_sha256: str
    state: SharedState


class ReceiptItem(BaseModel):
    receipt_id: str
    entity_kind: str
    availability: str


class ReceiptPage(BaseModel):
    items: list[ReceiptItem]
    next_cursor: str | None


class Artifact(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: str
    role: str
    logical_path: str | None = None


class ResultManifest(BaseModel):
    model_config = ConfigDict(extra='allow')
    artifacts: list[Artifact]
    import_provenance: dict[str, Any] | None = None


class DocumentRow(BaseModel):
    row_id: str
    label: str
    aligned: str
    source_member_id: str | None = None
    occurrence_id: str | None = None
    source_positions: list[int | None] | None = None


class AlignmentDocument(BaseModel):
    model_config = ConfigDict(extra='allow')
    result_id: str
    rows: list[DocumentRow]


class ResourcePage(BaseModel):
    model_config = ConfigDict(extra='allow')
    project_id: str
    dataset_id: str
    revision_id: str
    revision_sha256: str
    items: list[dict[str, Any]]
    offset: int
    limit: int
    total: int
    next_offset: int | None


class ResourceSelection(BaseModel):
    model_config = ConfigDict(extra='allow')
    project_id: str
    dataset_id: str
    revision_id: str
    revision_sha256: str
    members: list[dict[str, Any]]


class ResolvedMember(BaseModel):
    model_config = ConfigDict(extra='allow')
    project_id: str
    dataset_id: str
    revision_id: str
    ordinal: int
    status: str
    reason: str | None = None
    sequence: str | None = None
    source_positions: list[int] | None = None


class PairAgreement(BaseModel):
    method: str
    matched_rows: int
    shared_mapped_residues: int
    left_unmapped_residues: int
    right_unmapped_residues: int
    rows_with_unknown_mapping: int
    left_only_mapped_residues_known: int
    right_only_mapped_residues_known: int
    left_aligned_pairs: int
    right_aligned_pairs: int
    shared_aligned_pairs: int
    union_aligned_pairs: int
    jaccard: float | None


class SourceCorrespondence(BaseModel):
    source: int
    left_column: int
    right_column: int


class ComparedRow(BaseModel):
    model_config = ConfigDict(extra='allow')
    source_member_id: str
    occurrence_id: str
    left_row_id: str
    right_row_id: str
    positions: list[SourceCorrespondence]
    left_only_positions: list[int]
    right_only_positions: list[int]
    mapping_unknown: bool
    left_unmapped_columns: list[int]
    right_unmapped_columns: list[int]


class ComparisonReport(BaseModel):
    model_config = ConfigDict(extra='allow')
    agreement: PairAgreement
    rows: list[ComparedRow] = Field(default_factory=list)
    left_excluded: int
    right_excluded: int


class ComparisonResponse(BaseModel):
    method: str
    report: ComparisonReport


class EnsembleSupportResponse(BaseModel):
    model_config = ConfigDict(extra='allow')
    receipt_id: str
    reference_artifact_id: str
    report: dict[str, Any]


class ExportInput(ClosedRequest):
    receipt_id: str = Field(min_length=1)
    shared_state_ids: list[str] = Field(default_factory=list)


class OperationRequest(ClosedRequest):
    molecule: Literal['protein', 'dna', 'rna']
    domain_experiment_id: str = Field(min_length=1)
    name: str = Field(default='Alignment operation', min_length=1, max_length=255)


class CompareOperation(OperationRequest):
    operation: Literal['compare'] = 'compare'
    inputs: ComparisonInput


class EnsembleOperation(OperationRequest):
    operation: Literal['ensemble-support'] = 'ensemble-support'
    inputs: EnsembleSupportInput


class ExportOperation(OperationRequest):
    operation: Literal['export'] = 'export'
    inputs: ExportInput


ManagedOperation = Annotated[CompareOperation | EnsembleOperation | ExportOperation, Field(discriminator='operation')]


class OperationStatus(BaseModel):
    job_id: str
    project_id: str
    operation: Literal['compare', 'ensemble-support', 'export']
    status: str
    queue_status: str | None
    request_dataset_id: str
    result_url: str
    artifact_url: str | None
    error_message: str | None


class BmsHttpError(Exception):
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.body = body
        super().__init__(f'BMS HTTP {status}: {body.decode("utf-8", "replace")}')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AlignmentClient:
    def __init__(self, base_url: str, *, headers: dict[str, str] | None = None, timeout: float = 30):
        parts = urlsplit(base_url)
        if parts.scheme not in ('http', 'https') or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError('base_url must be an HTTP(S) origin or base path without credentials, query, or fragment')
        self.base_url = base_url.rstrip('/')
        self.headers = dict(headers or {})
        self.timeout = timeout
        self._opener = build_opener(_NoRedirect())

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None, *, launch_context_id: str | None = None) -> Any:
        headers = {**self.headers, 'Accept': 'application/json'}
        if launch_context_id is not None:
            headers['x-bms-launch-context-id'] = launch_context_id
        data = None
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            raise BmsHttpError(exc.code, exc.read()) from exc

    def export_project_bundle(self, project_id: str, receipt_id: str, destination, *, shared_state_ids: list[str] | None = None) -> dict:
        """Export the exact authorized Project artifacts and selected shared states.

        If not explicitly selected, discover states attached by portable import.
        Other Project annotations still require explicit selection.
        """
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from .bundle import make_bundle

        prefix = '/api/family-msa/projects/' + quote(project_id, safe='')
        result_path = prefix + '/receipts/' + quote(receipt_id, safe='')
        listing = self._request('GET', result_path)
        if shared_state_ids is None:
            shared_state_ids = [s['dataset_id'] for s in listing.get('import_provenance', {}).get('shared_states', []) if s.get('dataset_id')]
        artifacts = listing['artifacts']
        if not isinstance(artifacts, list) or not artifacts:
            raise ValueError('empty artifact inventory')
        if len({a['id'] for a in artifacts}) != len(artifacts):
            raise ValueError('duplicate artifact identifiers')
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = []
            inventory = []
            for index, artifact in enumerate(artifacts):
                path = f'artifacts/{index:06d}'
                request = Request(self.base_url + result_path + '/artifacts/' + quote(artifact['id'], safe=''),
                                  headers={**self.headers, 'Accept': 'application/octet-stream'}, method='GET')
                try:
                    with self._opener.open(request, timeout=self.timeout) as response:
                        data = response.read()
                except HTTPError as exc:
                    raise BmsHttpError(exc.code, exc.read()) from exc
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                files.append(path)
                inventory.append({'id': artifact['id'], 'role': artifact['role'], 'logical_path': artifact.get('logical_path'), 'path': path})
            states = []
            for index, state_id in enumerate(shared_state_ids or []):
                state = self._request('GET', prefix + '/shared-state/' + quote(state_id, safe=''))
                path = f'shared-state/{index:06d}.json'
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                files.append(path)
                states.append({'id': state_id, 'path': path})
            inventory_path = 'project-export.json'
            (root / inventory_path).write_text(json.dumps({'schema': 'bms.family-msa.project-export.v1',
                'project_id': project_id, 'receipt_id': receipt_id, 'artifacts': inventory,
                'shared_states': states}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            files.insert(0, inventory_path)
            return make_bundle(root, Path(destination), files, excluded=[
                'Project shared states not explicitly selected',
                'External dependencies not retained in the Project receipt'])

    def import_project_bundle(self, project_id: str, directory, *, replay_key: str, domain_experiment_id: str | None = None) -> "ImportReceipt":
        import base64
        from pathlib import Path
        from .bundle import inspect_project_bundle
        root = Path(directory)
        inspect_project_bundle(root)
        manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
        files = {name: base64.b64encode((root / name).read_bytes()).decode('ascii')
                 for name in manifest['dependencies']['included']}
        response = self._request('POST', '/api/family-msa/projects/' + quote(project_id, safe='') + '/imports',
                                 {'replay_key': replay_key, 'manifest': manifest, 'files': files, 'domain_experiment_id': domain_experiment_id})
        return ImportReceipt.model_validate(response)

    def import_project_bundle_streaming(self, project_id: str, directory, *, replay_key: str, domain_experiment_id: str | None = None) -> "ImportReceipt":
        """Upload verified portable files in bounded chunks without base64 encoding."""
        from pathlib import Path
        from .bundle import inspect_project_bundle
        root = Path(directory)
        inspect_project_bundle(root)
        manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
        metadata = json.dumps({'replay_key': replay_key, 'manifest': manifest, 'domain_experiment_id': domain_experiment_id}).encode('utf-8')
        def body():
            yield len(metadata).to_bytes(8, 'big')
            yield metadata
            for item in manifest['files']:
                with (root / item['path']).open('rb') as source:
                    while chunk := source.read(1024 * 1024):
                        yield chunk
        headers = {**self.headers, 'Accept': 'application/json',
                   'Content-Type': 'application/vnd.bms.alignment-bundle-stream',
                   'Content-Length': str(8 + len(metadata) + sum(item['size'] for item in manifest['files']))}
        path = '/api/family-msa/projects/' + quote(project_id, safe='') + '/imports/stream'
        request = Request(self.base_url + path, data=body(), headers=headers, method='POST')
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return ImportReceipt.model_validate(json.load(response))
        except HTTPError as exc:
            raise BmsHttpError(exc.code, exc.read()) from exc

    @staticmethod
    def _project(project_id: str) -> str:
        return '/api/family-msa/projects/' + quote(project_id, safe='')

    @classmethod
    def _source(cls, project_id: str, dataset_id: str, revision_id: str) -> str:
        return cls._project(project_id) + '/datasets/' + quote(dataset_id, safe='') + '/revisions/' + quote(revision_id, safe='')

    @classmethod
    def _result(cls, project_id: str, receipt_id: str) -> str:
        return cls._project(project_id) + '/receipts/' + quote(receipt_id, safe='')

    @staticmethod
    def _query(path: str, **params) -> str:
        values = {k: v for k, v in params.items() if v is not None}
        return path + ('?' + urlencode(values) if values else '')

    def _binary(self, method: str, path: str, data: bytes | None = None, *, content_type: str = 'application/octet-stream') -> bytes:
        headers = {**self.headers, 'Accept': 'application/octet-stream'}
        if data is not None:
            headers['Content-Type'] = content_type
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return response.read()
        except HTTPError as exc:
            raise BmsHttpError(exc.code, exc.read()) from exc

    def settings_schema(self) -> dict[str, Any]:
        return self._request('GET', '/api/family-msa/settings-schema')

    def resources(self, project_id: str, dataset_id: str, revision_id: str, *, offset: int = 0, limit: int = 50) -> ResourcePage:
        return ResourcePage.model_validate(self._request('GET', self._query(self._source(project_id, dataset_id, revision_id) + '/resources', offset=offset, limit=limit)))

    def freeze(self, project_id: str, dataset_id: str, revision_id: str, request: FreezeRequest) -> ResourceSelection:
        return ResourceSelection.model_validate(self._request('POST', self._source(project_id, dataset_id, revision_id) + '/resources/freeze', request.model_dump(mode='json')))

    def resolve(self, project_id: str, dataset_id: str, revision_id: str, request: ResolveRequest) -> ResolvedMember:
        path = self._source(project_id, dataset_id, revision_id) + f'/resources/{request.ordinal}/{request.kind}'
        params = request.model_dump(exclude={'ordinal', 'kind'}, exclude_none=True)
        if request.kind == 'molecular' and request.chain is not None:
            raise ValueError('chain is only supported by the design source route')
        return ResolvedMember.model_validate(self._request('GET', self._query(path, **params)))

    def receipts(self, project_id: str, *, after: str | None = None) -> ReceiptPage:
        return ReceiptPage.model_validate(self._request('GET', self._query(self._project(project_id) + '/receipts', after=after)))

    def result(self, project_id: str, receipt_id: str) -> ResultManifest:
        return ResultManifest.model_validate(self._request('GET', self._result(project_id, receipt_id)))

    def document(self, project_id: str, receipt_id: str, artifact_id: str) -> AlignmentDocument:
        return AlignmentDocument.model_validate(self._request('GET', self._result(project_id, receipt_id) + '/documents/' + quote(artifact_id, safe='')))

    def artifact(self, project_id: str, receipt_id: str, artifact_id: str) -> bytes:
        return self._binary('GET', self._result(project_id, receipt_id) + '/artifacts/' + quote(artifact_id, safe=''))

    def job_result(self, job_id: str) -> ResultManifest:
        return ResultManifest.model_validate(self._request('GET', '/api/family-msa/jobs/' + quote(job_id, safe='')))

    def job_document(self, job_id: str, artifact_id: str) -> AlignmentDocument:
        return AlignmentDocument.model_validate(self._request('GET', '/api/family-msa/jobs/' + quote(job_id, safe='') + '/documents/' + quote(artifact_id, safe='')))

    def job_artifact(self, job_id: str, artifact_id: str) -> bytes:
        return self._binary('GET', '/api/family-msa/jobs/' + quote(job_id, safe='') + '/artifacts/' + quote(artifact_id, safe=''))

    def import_alignment(self, project_id: str, data: bytes, request: AlignmentImport) -> ImportReceipt:
        path = self._query(self._project(project_id) + '/imports/alignment', **request.model_dump())
        return ImportReceipt.model_validate_json(self._binary('POST', path, data))

    def import_project_zip(self, project_id: str, data: bytes, *, replay_key: str, domain_experiment_id: str | None = None) -> ImportReceipt:
        path = self._query(self._project(project_id) + '/imports/zip', replay_key=replay_key, domain_experiment_id=domain_experiment_id)
        return ImportReceipt.model_validate_json(self._binary('POST', path, data, content_type='application/zip'))

    def export_project_zip(self, project_id: str, receipt_id: str, request: BrowserExport) -> bytes:
        """Compatibility synchronous ZIP route; use managed export when available."""
        return self._binary('POST', self._result(project_id, receipt_id) + '/bundle', json.dumps(request.model_dump()).encode('utf-8'), content_type='application/json')

    def compare(self, project_id: str, request: ComparisonInput) -> ComparisonResponse:
        """Compatibility synchronous comparison; managed work has a separate method."""
        return ComparisonResponse.model_validate(self._request('POST', self._project(project_id) + '/compare', request.model_dump(mode='json')))

    def ensemble_support(self, project_id: str, request: EnsembleSupportInput) -> EnsembleSupportResponse:
        return EnsembleSupportResponse.model_validate(self._request('POST', self._project(project_id) + '/ensemble-support', request.model_dump(mode='json')))

    def shared_state(self, project_id: str, dataset_id: str, *, revision_id: str | None = None) -> SharedRevision:
        path = self._query(self._project(project_id) + '/shared-state/' + quote(dataset_id, safe=''), revision_id=revision_id)
        return SharedRevision.model_validate(self._request('GET', path))

    def create_shared_state(self, project_id: str, request: SharedCreate) -> SharedRevision:
        """Persist typed views, source/artifact annotations and comparison reports."""
        return SharedRevision.model_validate(self._request('POST', self._project(project_id) + '/shared-state', request.model_dump(mode='json', by_alias=True)))

    def save_shared_state(self, project_id: str, dataset_id: str, request: SharedSave) -> SharedRevision:
        return SharedRevision.model_validate(self._request('PUT', self._project(project_id) + '/shared-state/' + quote(dataset_id, safe=''), request.model_dump(mode='json', by_alias=True)))

    def submit_operation(self, project_id: str, request: ManagedOperation) -> OperationStatus:
        """Submit ordinary managed CPU work, returning its resumable Job handle."""
        return OperationStatus.model_validate(self._request('POST', self._project(project_id) + '/operations', request.model_dump(mode='json')))

    def operation(self, project_id: str, job_id: str) -> OperationStatus:
        return OperationStatus.model_validate(self._request('GET', self._project(project_id) + '/operations/' + quote(job_id, safe='')))

    def operation_artifact(self, project_id: str, job_id: str) -> bytes:
        return self._binary('GET', self._project(project_id) + '/operations/' + quote(job_id, safe='') + '/artifact')

    def comparison_result(self, project_id: str, job_id: str) -> ComparisonResponse:
        return ComparisonResponse.model_validate_json(self.operation_artifact(project_id, job_id))

    def ensemble_result(self, project_id: str, job_id: str) -> EnsembleSupportResponse:
        return EnsembleSupportResponse.model_validate_json(self.operation_artifact(project_id, job_id))

    def model(self, model_id: Literal['family_msa_protein', 'family_msa_nucleic']) -> ModelInfo:
        return ModelInfo.model_validate(self._request('GET', '/api/models/' + quote(model_id, safe='')))

    @staticmethod
    def _submission_body(submission: JobSubmission) -> dict[str, Any]:
        body = submission.model_dump(mode='json', exclude_unset=True)
        body['mode'] = submission.mode
        return body

    def preview(self, submission: JobSubmission) -> JobPreview:
        return JobPreview.model_validate(self._request('POST', '/api/jobs/execution-plan/preview',
            self._submission_body(submission), launch_context_id=submission.launch_context_id))

    def submit(self, submission: JobSubmission) -> Job:
        return Job.model_validate(self._request('POST', '/api/jobs',
            self._submission_body(submission), launch_context_id=submission.launch_context_id))

    def job(self, job_id: str) -> Job:
        return Job.model_validate(self._request('GET', '/api/jobs/' + quote(job_id, safe='')))
