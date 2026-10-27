"""Small typed client for the existing BMS model and Job HTTP routes.

The server remains authoritative for admission, project ownership, and execution.
"""
from __future__ import annotations

import json
from typing import Any, Literal
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field
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
